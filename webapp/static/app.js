/* CiteWise — single-page app logic.
 *
 * Two top-level views share one page:
 *   #view-landing  — the marketing/auth hero (logged out)
 *   #view-app      — the sidebar + research workspace (logged in)
 *
 * boot() asks the server who we are (/api/me) and which logins exist
 * (/api/config), then shows the right view. Everything below the sidebar is
 * rendered into #main on demand: the composer, a live research run, or a
 * past run re-opened from history.
 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  // ---- module state --------------------------------------------------------
  let CONFIG = {};
  let USER = null;
  let HISTORY = [];
  let threadId = null;
  let es = null;
  let activeHistoryId = null;
  let appWired = false;
  let authMode = "login";   // "login" | "signup" (landing auth card)
  let reportSeq = 0;        // unique id per rendered report (scopes citation anchors)

  const PIPELINE = [
    { key: "guardrail", label: "Validate" },
    { key: "planner", label: "Plan" },
    { key: "researcher", label: "Research" },
    { key: "fact_checker", label: "Fact-check" },
    { key: "synthesizer", label: "Write" },
  ];

  const STATUS_STYLE = {
    supported:           { cls: "vtag-ok",    glyph: "✓", t: "supported" },
    unsupported:         { cls: "vtag-no",    glyph: "✕", t: "unsupported" },
    needs_more_evidence: { cls: "vtag-maybe", glyph: "~", t: "needs more" },
  };

  // ---- utils ---------------------------------------------------------------
  function escapeHtml(s) {
    return (s ?? "").toString().replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  // ---- report prose formatting --------------------------------------------
  // Turn the model's (HTML-escaped) plain text into tidy, readable HTML:
  // paragraphs, bullet/number lists, light **bold**/*italic*/`code`, and
  // inline [n] citation markers rendered as pills that jump to the source list.

  function citeMarkers(s, rid, nCites) {
    return s.replace(/\[(\d{1,3})\]/g, (m, d) => {
      const n = +d;
      if (n < 1 || n > nCites) return m;       // unknown marker → leave as plain text
      return `<a href="#cite-${rid}-${n}" data-cite-ref="${n}" class="cite-ref" ` +
             `title="Jump to source ${n}">${n}</a>`;
    });
  }

  function inlineFmt(s) {
    return s
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/(^|[\s(])_([^_\n]+)_/g, "$1<em>$2</em>");
  }

  const fmtLine = (s, rid, nCites) => inlineFmt(citeMarkers(s, rid, nCites));

  function formatProse(text, rid, nCites) {
    const lines = escapeHtml(text).replace(/\r/g, "").split("\n");
    const out = [];
    let para = [];
    let list = null;   // { type: "ul" | "ol", items: [] }
    const flushPara = () => { if (para.length) { out.push(`<p>${fmtLine(para.join(" "), rid, nCites)}</p>`); para = []; } };
    const flushList = () => { if (list) { out.push(`<${list.type}>${list.items.join("")}</${list.type}>`); list = null; } };
    for (const raw of lines) {
      const line = raw.trim();
      if (!line) { flushPara(); flushList(); continue; }
      const ul = line.match(/^[-*•]\s+(.*)/);
      const ol = line.match(/^\d+[.)]\s+(.*)/);
      if (ul) {
        flushPara();
        if (!list || list.type !== "ul") { flushList(); list = { type: "ul", items: [] }; }
        list.items.push(`<li>${fmtLine(ul[1], rid, nCites)}</li>`);
      } else if (ol) {
        flushPara();
        if (!list || list.type !== "ol") { flushList(); list = { type: "ol", items: [] }; }
        list.items.push(`<li>${fmtLine(ol[1], rid, nCites)}</li>`);
      } else {
        flushList();
        para.push(line);
      }
    }
    flushPara(); flushList();
    return out.join("");
  }

  function prettyHost(u) {
    try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; }
  }

  function timeAgo(ts) {
    const s = Date.now() / 1000 - ts;
    if (s < 60) return "just now";
    const m = Math.floor(s / 60); if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60); if (h < 24) return `${h}h ago`;
    const d = Math.floor(h / 24); if (d < 7) return `${d}d ago`;
    return new Date(ts * 1000).toLocaleDateString();
  }

  function toast(msg, kind = "info") {
    const t = document.createElement("div");
    t.className = `toast rise ${kind === "error" ? "error" : kind === "success" ? "success" : ""}`;
    t.textContent = msg;
    $("toast").appendChild(t);
    setTimeout(() => { t.style.transition = "opacity .3s"; t.style.opacity = "0"; setTimeout(() => t.remove(), 320); }, 3200);
  }

  function authFormError(code) {
    return ({
      invalid_email: "Enter a valid email address.",
      weak_password: "Password must be at least 8 characters.",
      email_taken: "An account with that email already exists — try logging in.",
      bad_credentials: "Wrong email or password.",
      guest_disabled: "Guest login is turned off on this server.",
    })[code] || "Something went wrong. Please try again.";
  }

  // =========================================================================
  // Boot
  // =========================================================================
  async function boot() {
    try { CONFIG = await fetch("/api/config").then((r) => r.json()); } catch { CONFIG = {}; }
    let me = { user: null };
    try { me = await fetch("/api/me").then((r) => r.json()); } catch {}
    USER = me.user;

    $("splash").hidden = true;

    if (USER) showApp(); else showLanding();
  }

  // =========================================================================
  // Landing / auth
  // =========================================================================
  function showLanding() {
    $("view-app").hidden = true;
    $("view-landing").hidden = false;
    renderAuthControls();
    document.querySelectorAll("[data-login]").forEach((b) => {
      b.onclick = () => {
        const inp = $("authControls").querySelector("input[name=email]");
        if (inp) { inp.focus(); inp.scrollIntoView({ behavior: "smooth", block: "center" }); }
      };
    });
  }

  const FIELD_CLS = "field";

  function renderAuthControls() {
    const c = $("authControls");
    const guest = !!CONFIG.allow_guest;
    c.innerHTML = `
      <div class="sheet auth-card" style="padding:1.4rem">
        <div class="auth-tabs">
          <button data-tab="login" type="button" class="auth-tab">Log in</button>
          <button data-tab="signup" type="button" class="auth-tab">Sign up</button>
        </div>
        <form data-auth-form novalidate style="display:flex;flex-direction:column;gap:.7rem">
          <input data-name-field name="name" placeholder="Your name" autocomplete="name" class="${FIELD_CLS}" hidden />
          <input name="email" type="email" placeholder="you@example.com" autocomplete="email" required class="${FIELD_CLS}" />
          <input name="password" type="password" placeholder="Password (8+ characters)" required minlength="8" class="${FIELD_CLS}" />
          <p data-auth-error class="auth-err" hidden></p>
          <button type="submit" class="btn btn-ink" style="justify-content:center;width:100%;margin-top:.2rem">Log in</button>
        </form>
        ${guest ? `
        <div class="auth-or">or</div>
        <form data-guest-form style="display:flex;gap:.5rem">
          <input name="name" placeholder="Continue as guest" autocomplete="name" class="${FIELD_CLS}" style="flex:1;min-width:0" />
          <button type="submit" class="btn btn-ghost">Guest</button>
        </form>` : ``}
      </div>`;

    c.querySelectorAll("[data-tab]").forEach((b) => {
      b.onclick = () => { authMode = b.dataset.tab; applyAuthMode(); };
    });
    c.querySelector("[data-auth-form]").onsubmit = (e) => {
      e.preventDefault();
      const f = e.target;
      emailAuth({ email: f.email.value, password: f.password.value, name: f.name.value });
    };
    if (guest) {
      const gf = c.querySelector("[data-guest-form]");
      gf.onsubmit = (e) => { e.preventDefault(); guestLogin(gf.name.value); };
    }
    applyAuthMode();
  }

  function applyAuthMode() {
    const c = $("authControls");
    const login = authMode === "login";
    c.querySelectorAll("[data-tab]").forEach((b) => {
      const active = b.dataset.tab === authMode;
      b.className = `auth-tab${active ? " active" : ""}`;
    });
    const nameField = c.querySelector("[data-name-field]");
    if (nameField) nameField.hidden = login;
    const pw = c.querySelector("[data-auth-form] input[name=password]");
    if (pw) pw.setAttribute("autocomplete", login ? "current-password" : "new-password");
    const submit = c.querySelector("[data-auth-form] button[type=submit]");
    if (submit) submit.textContent = login ? "Log in" : "Create account";
    const err = c.querySelector("[data-auth-error]");
    if (err) { err.hidden = true; err.textContent = ""; }
  }

  async function emailAuth({ email, password, name }) {
    const c = $("authControls");
    const err = c.querySelector("[data-auth-error]");
    const btn = c.querySelector("[data-auth-form] button[type=submit]");
    err.hidden = true;
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = authMode === "login" ? "Logging in…" : "Creating…";
    try {
      const r = await fetch(`/auth/${authMode}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: (email || "").trim(), password, name: (name || "").trim() }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) { err.textContent = authFormError(data.error); err.hidden = false; return; }
      USER = data.user;
      showApp();
    } catch {
      err.textContent = "Could not reach the server. Please try again.";
      err.hidden = false;
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  }

  async function guestLogin(name) {
    try {
      const r = await fetch("/auth/demo", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name || "Guest" }),
      });
      if (!r.ok) throw new Error("guest login failed");
      USER = (await r.json()).user;
      showApp();
    } catch {
      toast("Could not start a guest session.", "error");
    }
  }

  async function logout() {
    try { await fetch("/auth/logout", { method: "POST" }); } catch {}
    USER = null; threadId = null; activeHistoryId = null; HISTORY = [];
    if (es) es.close();
    closeSidebar();
    showLanding();
  }

  // =========================================================================
  // App shell
  // =========================================================================
  function showApp() {
    $("view-landing").hidden = true;
    $("view-app").hidden = false;

    $("userName").textContent = USER.name || "Researcher";
    $("userEmail").textContent = USER.email || (USER.provider === "guest" ? "Guest session" : "");
    const av = $("userAvatar");
    if (USER.picture) {
      av.innerHTML = `<img src="${escapeHtml(USER.picture)}" alt="" class="h-full w-full object-cover" referrerpolicy="no-referrer" />`;
    } else {
      av.textContent = (USER.name || "R").trim().charAt(0).toUpperCase();
    }

    if (CONFIG.provider) {
      const chain = CONFIG.chain || [];
      $("modelText").textContent = chain.length > 1
        ? `${CONFIG.provider} · ${CONFIG.model}  →  ${chain.slice(1).join(" → ")}`
        : `${CONFIG.provider} · ${CONFIG.model}`;
      $("modelBadge").hidden = false;
    }

    wireAppOnce();
    renderComposer();
    loadHistory();
  }

  function wireAppOnce() {
    if (appWired) return;
    appWired = true;
    $("newResearchBtn").onclick = () => { closeSidebar(); renderComposer(); };
    $("logoutBtn").onclick = logout;
    $("sidebarOpen").onclick = openSidebar;
    $("sidebarClose").onclick = closeSidebar;
    $("sidebarBackdrop").onclick = closeSidebar;
  }

  function openSidebar() { $("sidebar").classList.add("open"); $("sidebarBackdrop").hidden = false; }
  function closeSidebar() { $("sidebar").classList.remove("open"); $("sidebarBackdrop").hidden = true; }

  // =========================================================================
  // Composer + live research
  // =========================================================================
  function renderComposer() {
    activeHistoryId = null;
    highlightHistory();
    $("mainTitle").textContent = "New inquiry";
    $("main").innerHTML = `
      <div class="wrap">
        <div class="sheet rise" style="padding:1.4rem">
          <label for="question" class="eyebrow" style="display:block;margin-bottom:.7rem">Research question</label>
          <textarea id="question" rows="3"
            placeholder="e.g. How has the cost of solar power changed, and why does it matter?"
            class="field" style="font-size:1.08rem"></textarea>
          <div style="margin-top:.9rem;display:flex;align-items:center;justify-content:space-between;gap:.8rem">
            <p class="mono" style="font-size:.64rem;color:var(--ink-ghost);letter-spacing:.04em;margin:0">Plan → Research → Fact-check → Write · you sign off before export.</p>
            <button id="researchBtn" class="btn btn-go">
              <span id="btnText">Commission</span><span aria-hidden="true">→</span>
            </button>
          </div>
        </div>
        <div id="examples" style="margin-top:1rem;display:flex;flex-wrap:wrap;gap:.5rem"></div>
        <section id="progress" hidden class="sheet rise" style="margin-top:1.6rem;padding:1.2rem 1.4rem">
          <p class="eyebrow" style="margin-bottom:.8rem">Proof stages</p>
          <div id="steps" class="stages"></div>
        </section>
        <section id="results" class="stack" style="margin-top:1.6rem"></section>
      </div>`;

    const examples = [
      "How has the cost of solar power changed, and why does it matter?",
      "What are the proven health effects of intermittent fasting?",
      "How effective are four-day work weeks?",
    ];
    $("examples").innerHTML = examples
      .map((q) => `<button data-ex="${escapeHtml(q)}" class="chip">${escapeHtml(q)}</button>`)
      .join("");
    $("examples").querySelectorAll("[data-ex]").forEach((b) => {
      b.onclick = () => { $("question").value = b.dataset.ex; $("question").focus(); };
    });

    $("researchBtn").onclick = startResearch;
    $("question").addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") startResearch();
    });
    $("question").focus();
  }

  function setBusy(busy) {
    const b = $("researchBtn"); if (!b) return;
    b.disabled = busy;
    $("btnText").textContent = busy ? "Working…" : "Commission";
  }

  function renderSteps(activeKey, allDone = false) {
    const steps = $("steps"); if (!steps) return;
    const activeIdx = PIPELINE.findIndex((x) => x.key === activeKey);
    steps.innerHTML = PIPELINE.map((p, i) => {
      let state = "", num = i + 1;
      if (allDone || (activeIdx > -1 && i < activeIdx)) { state = "done"; num = "✓"; }
      else if (activeIdx > -1 && i === activeIdx) { state = "live"; }
      return `<span class="stage ${state}"><span class="num">${num}</span>${p.label}<span class="arrow">→</span></span>`;
    }).join("");
  }

  function card(inner, extra = "") {
    const wrap = document.createElement("div");
    wrap.className = `sheet rise ${extra}`;
    wrap.style.padding = "1.4rem";
    wrap.innerHTML = inner;
    $("results").appendChild(wrap);
    return wrap;
  }

  // ---- infographic: key-figure band + a simple inline-SVG chart -----------
  function renderKeyFigures(figures) {
    if (!figures || !figures.length) return "";
    const cards = figures.map((f, i) => `
      <div style="flex:1;min-width:0;text-align:center;padding:.1rem .7rem;${i ? "border-left:1px solid var(--rule);" : ""}">
        <div style="font-family:var(--font-display);font-weight:600;font-size:1.55rem;color:var(--navy);line-height:1.08;letter-spacing:-.01em">${escapeHtml(f.value)}</div>
        <div style="font-family:var(--font-body);font-size:.82rem;color:var(--ink-soft);margin-top:.3rem;line-height:1.25">${escapeHtml(f.label)}</div>
        ${f.source_index ? `<div class="mono" style="font-size:.6rem;color:var(--verified);margin-top:.25rem">[${f.source_index}]</div>` : ""}
      </div>`).join("");
    return `<div style="display:flex;align-items:flex-start;border-top:2px solid var(--navy);border-bottom:1px solid var(--rule);padding:1rem 0;margin:1.3rem 0">${cards}</div>`;
  }

  function renderChart(chart) {
    if (!chart || !chart.categories || !chart.values) return "";
    const cats = chart.categories.map(String);
    const vals = chart.values.map((v) => Number(v) || 0);
    const k = Math.min(cats.length, vals.length);
    if (k < 2) return "";
    const C = cats.slice(0, k), V = vals.slice(0, k);
    const lo = Math.min(0, ...V);
    let hi = Math.max(0, ...V);
    if (hi === lo) hi = lo + 1;
    const W = 600, H = 300, padL = 56, padR = 16, padT = 30, padB = 42;
    const pw = W - padL - padR, ph = H - padT - padB, x0 = padL, y0 = padT;
    const GREEN = "#157A60";
    const mono = "'IBM Plex Mono', ui-monospace, monospace";
    const yPx = (y) => y0 + ph - ((y - lo) / (hi - lo)) * ph;
    let grid = "", yl = "";
    const T = 4;
    for (let t = 0; t <= T; t++) {
      const yv = lo + (t / T) * (hi - lo), py = yPx(yv);
      grid += `<line x1="${x0}" y1="${py.toFixed(1)}" x2="${x0 + pw}" y2="${py.toFixed(1)}" stroke="#E7ECF3"/>`;
      yl += `<text x="${x0 - 8}" y="${(py + 3).toFixed(1)}" text-anchor="end" font-family="${mono}" font-size="10" fill="#8794A6">${Number(yv.toFixed(2))}</text>`;
    }
    const n = C.length, slot = pw / n;
    let xl = "";
    for (let i = 0; i < n; i++)
      xl += `<text x="${(x0 + slot * (i + 0.5)).toFixed(1)}" y="${y0 + ph + 18}" text-anchor="middle" font-family="${mono}" font-size="10" fill="#46556A">${escapeHtml(C[i])}</text>`;
    let marks = "";
    if (chart.kind === "line") {
      const pts = V.map((v, i) => `${(x0 + slot * (i + 0.5)).toFixed(1)},${yPx(v).toFixed(1)}`).join(" ");
      marks += `<polyline points="${pts}" fill="none" stroke="${GREEN}" stroke-width="2.5"/>`;
      V.forEach((v, i) => { marks += `<circle cx="${(x0 + slot * (i + 0.5)).toFixed(1)}" cy="${yPx(v).toFixed(1)}" r="3.5" fill="${GREEN}"/>`; });
    } else {
      const bw = Math.min(slot * 0.5, 48);
      V.forEach((v, i) => {
        const py = yPx(v), z = yPx(0);
        marks += `<rect x="${(x0 + slot * (i + 0.5) - bw / 2).toFixed(1)}" y="${Math.min(py, z).toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.abs(z - py).toFixed(1)}" fill="${GREEN}" rx="1"/>`;
      });
    }
    const axis = `<line x1="${x0}" y1="${y0 + ph}" x2="${x0 + pw}" y2="${y0 + ph}" stroke="#C2CBD8"/>`;
    const title = chart.title ? `<text x="0" y="15" font-family="'Fraunces', Georgia, serif" font-weight="600" font-size="15" fill="#1C2E4A">${escapeHtml(chart.title)}</text>` : "";
    const ylab = chart.y_label ? `<text x="0" y="${y0 - 7}" font-family="${mono}" font-size="9" fill="#8794A6">${escapeHtml(chart.y_label)}</text>` : "";
    const ref = chart.source_index ? `<div class="mono" style="font-size:.6rem;color:var(--ink-faint);margin-top:.15rem">Chart data: source [${chart.source_index}]</div>` : "";
    return `<figure style="margin:1.3rem 0 1.1rem">
      <svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:560px;display:block;height:auto" role="img" aria-label="${escapeHtml(chart.title || "chart")}">${grid}${axis}${marks}${yl}${xl}${title}${ylab}</svg>${ref}
    </figure>`;
  }

  function renderReport(report) {
    if (!report) return "";
    const rid = ++reportSeq;
    const citations = report.citations || [];
    const nCites = citations.length;

    const summary = formatProse(report.summary, rid, nCites);
    const sections = (report.sections || []).map((s) =>
      `<section class="doc-section">
        <h4>${escapeHtml(s.heading)}</h4>
        ${formatProse(s.content, rid, nCites)}
      </section>`).join("");

    // Every listed source is a kept, cited source — it survived the drop pass,
    // so it backs at least one verified claim (✓). The verdict ledger below
    // carries the full supported / needs-more / refuted breakdown per claim.
    const cites = citations.map((u, i) => {
      const n = i + 1;
      const host = prettyHost(u);
      const url = escapeHtml(u);
      return `<li id="cite-${rid}-${n}" data-cite-anchor="${n}" class="source" style="animation:settle .45s cubic-bezier(.2,.7,.2,1) both;animation-delay:${0.05 * i}s">
        <span class="source-num">${n}<span class="tick tick-ok" title="Supported source">✓</span></span>
        <a href="${url}" target="_blank" rel="noopener" class="source-link">
          <span class="source-host">${escapeHtml(host)}</span>
          ${host !== u ? `<span class="source-url">${url}</span>` : ""}
        </a></li>`;
    }).join("");

    return `
      <article class="doc" data-report>
        <div class="doc-head">
          <span class="kicker">Report</span>
        </div>
        <div class="doc-grid">
          <div class="doc-body">
            <div class="doc-summary">${summary}</div>
            ${renderKeyFigures(report.key_figures)}
            ${renderChart(report.chart)}
            ${sections}
          </div>
          ${cites ? `<aside class="doc-margin">
            <div class="doc-margin-inner">
              <div class="margin-label">Sources</div>
              <ol class="sources">${cites}</ol>
              <hr class="footnote-rule" style="margin-top:.9rem" />
              <p class="mono" style="font-size:.6rem;color:var(--ink-ghost);margin:.55rem 0 0;line-height:1.5">Every claim above is anchored to a cited source.</p>
            </div>
          </aside>` : ""}
        </div>
      </article>`;
  }

  function renderVerdicts(verdicts) {
    if (!verdicts || !verdicts.length) return "";
    const rows = verdicts.map((v) => {
      const st = STATUS_STYLE[v.status] || STATUS_STYLE.needs_more_evidence;
      return `<div class="verdict">
        <span class="vtag ${st.cls}"><span>${st.glyph}</span>${st.t} · ${escapeHtml(String(v.confidence))}</span>
        <div style="min-width:0">
          <p class="claim">${escapeHtml(v.claim)}</p>
          <p class="reason">${escapeHtml(v.reasoning)}</p>
        </div></div>`;
    }).join("");
    return `<details class="verdicts"><summary>Fact-check ledger · ${verdicts.length} claims</summary>
            <div style="margin-top:.4rem">${rows}</div></details>`;
  }

  function renderDraft(d) {
    renderSteps(null, true);
    const stats = `<div class="mono" style="display:flex;gap:1.4rem;font-size:.66rem;color:var(--ink-faint);margin-top:.7rem">
        <span><b style="color:var(--verified)">${d.n_verified}</b> verified</span>
        <span><b style="color:var(--ink)">${d.n_claims}</b> claims gathered</span></div>`;
    const elx = card(`
      <div style="display:flex;align-items:center;justify-content:space-between">
        <span data-status-banner class="banner" style="color:var(--pending)"><span class="dot dot-wait"></span>Draft — awaiting your sign-off</span>
      </div>
      ${stats}
      <hr class="hair" style="margin:1.2rem 0" />
      ${renderReport(d.report)}
      ${renderVerdicts(d.verdicts)}
      <hr class="hair" style="margin:1.3rem 0 0" />
      <div data-actions style="display:flex;gap:.7rem;padding-top:1.2rem">
        <button data-act="approve" class="btn btn-go" style="flex:1;justify-content:center">Approve &amp; export</button>
        <button data-act="reject" class="btn btn-ghost" style="flex:1;justify-content:center">Reject &amp; revise</button>
      </div>
      <div data-reject-panel hidden class="rise" style="margin-top:.9rem">
        <label class="mono" style="display:block;font-size:.64rem;letter-spacing:.04em;color:var(--ink-faint);margin-bottom:.5rem">What should change? (optional — leave blank for a re-structured take)</label>
        <textarea data-feedback rows="2"
          placeholder="e.g. make it shorter · focus on the risks · add a ‘practical takeaways’ section · use plainer language"
          class="field"></textarea>
        <div style="margin-top:.6rem;display:flex;align-items:center;justify-content:flex-end;gap:.5rem">
          <button data-act="reject-cancel" class="btn btn-quiet">Cancel</button>
          <button data-act="reject-send" class="btn btn-ink">Send revision →</button>
        </div>
      </div>`);
    const panel = elx.querySelector("[data-reject-panel]");
    elx.querySelector('[data-act="approve"]').onclick = () => decide(elx, true);
    elx.querySelector('[data-act="reject"]').onclick = () => { panel.hidden = false; elx.querySelector("[data-feedback]").focus(); };
    elx.querySelector('[data-act="reject-cancel"]').onclick = () => { panel.hidden = true; };
    elx.querySelector('[data-act="reject-send"]').onclick = () => decide(elx, false, elx.querySelector("[data-feedback]").value);
  }

  async function decide(draftEl, approved, feedback = "") {
    draftEl.querySelectorAll("button").forEach((b) => { b.disabled = true; b.style.opacity = ".5"; });
    const pending = card(`<div class="mono" style="display:flex;align-items:center;gap:.6rem;font-size:.74rem;color:var(--ink-soft)">
        <span class="spinner"></span>
        ${approved ? "Exporting…" : "Revising the draft — applying your feedback…"}</div>`);
    try {
      const res = await fetch("/api/approve", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ thread_id: threadId, approved, feedback }),
      }).then((r) => r.json());
      pending.remove();

      if (res.status === "exported") {
        // Transform the existing draft card in place — do NOT re-render the report
        // below (it's already shown above). Just flip the banner + action buttons.
        const banner = draftEl.querySelector("[data-status-banner]");
        if (banner) {
          banner.style.color = "var(--verified)";
          banner.innerHTML = `<span class="dot dot-go"></span>Approved — your report is ready`;
        }
        const actions = draftEl.querySelector("[data-actions]");
        if (actions) {
          const dl = activeHistoryId
            ? `<a href="/api/history/${activeHistoryId}/pdf" class="btn btn-go" download style="text-decoration:none;flex:1;justify-content:center">Download PDF <span aria-hidden="true">↓</span></a>`
            : "";
          actions.innerHTML = `${dl}<button data-act="new" class="btn btn-ghost" style="flex:1;justify-content:center">New inquiry</button>`;
          const nb = actions.querySelector('[data-act="new"]');
          if (nb) nb.onclick = () => { closeSidebar(); renderComposer(); };
        }
        const rp = draftEl.querySelector("[data-reject-panel]");
        if (rp) rp.remove();
        draftEl.style.borderColor = "rgba(21,122,96,.35)";
        toast("Report approved.", "success");
      } else if (res.status === "draft") {
        const applied = feedback && feedback.trim();
        card(`<p class="mono" style="font-size:.7rem;color:var(--ink-faint);margin:0">Revised draft generated${applied ? " — your feedback was applied" : ""}. Review again:</p>`);
        renderDraft(res);
      } else if (res.status === "error") {
        renderError(res.message);
      } else {
        card(`<p style="margin:0;color:var(--ink-soft)">Report was not approved; nothing was exported.</p>`);
      }
      loadHistory();
    } catch (e) {
      pending.remove();
      renderError(String(e));
    }
  }

  function renderRefused(reason) {
    renderSteps(null, false);
    const wrap = card(`<div style="display:flex;align-items:flex-start;gap:.7rem">
      <span class="dot dot-no" style="margin-top:.45rem"></span>
      <div><h3 style="font-family:var(--font-display);font-weight:600;font-size:1.1rem;color:var(--refuted);margin:0">Request refused by guardrails</h3>
      <p style="margin:.4rem 0 0;color:var(--ink-soft);font-size:.96rem">${escapeHtml(reason)}</p></div></div>`);
    wrap.style.borderColor = "rgba(178,58,72,.35)";
  }

  function renderError(msg) {
    const wrap = card(`<h3 style="font-family:var(--font-display);font-weight:600;font-size:1.1rem;color:var(--refuted);margin:0">Something went wrong</h3>
          <p style="margin:.4rem 0 0;word-break:break-word;color:var(--ink-soft);font-size:.92rem">${escapeHtml(msg)}</p>`);
    wrap.style.borderColor = "rgba(178,58,72,.35)";
  }

  function startResearch() {
    const q = $("question").value.trim();
    if (!q) { $("question").focus(); return; }
    $("results").innerHTML = "";
    $("progress").hidden = false;
    renderSteps("guardrail");
    setBusy(true);
    $("mainTitle").textContent = q.length > 70 ? q.slice(0, 70) + "…" : q;
    threadId = null;
    if (es) es.close();

    es = new EventSource(`/api/research?question=${encodeURIComponent(q)}`);
    es.onmessage = (ev) => {
      const d = JSON.parse(ev.data);
      if (d.type === "thread") threadId = d.thread_id;
      else if (d.type === "node") renderSteps(d.node);
      else if (d.type === "draft") { activeHistoryId = d.research_id; renderDraft(d); loadHistory(); es.close(); setBusy(false); }
      else if (d.type === "refused") { renderRefused(d.reason); loadHistory(); es.close(); setBusy(false); }
      else if (d.type === "error") { renderError(d.message); es.close(); setBusy(false); }
      else if (d.type === "end") { es.close(); setBusy(false); }
    };
    es.onerror = async () => {
      es.close(); setBusy(false);
      if (!threadId) {
        // Never got a single event — likely the session expired (401) or the server is down.
        let m = { user: null };
        try { m = await fetch("/api/me").then((r) => r.json()); } catch {}
        if (!m.user) { USER = null; showLanding(); return; }
        toast("Could not reach the server.", "error");
      }
    };
  }

  // =========================================================================
  // History
  // =========================================================================
  async function loadHistory() {
    try {
      HISTORY = (await fetch("/api/history").then((r) => r.json())).items || [];
      renderHistoryList();
    } catch {}
  }

  function highlightHistory() { if (HISTORY.length || $("historyList")) renderHistoryList(); }

  function renderHistoryList() {
    const list = $("historyList"); if (!list) return;
    if (!HISTORY.length) {
      list.innerHTML = `<p class="rail-empty">The file is empty.<br/>Commission an inquiry to begin.</p>`;
      return;
    }
    list.innerHTML = HISTORY.map((it) => {
      const dot = it.status === "exported" ? "dot-go" : it.status === "refused" ? "dot-no" : "dot-wait";
      const active = it.id === activeHistoryId;
      return `<div data-id="${it.id}" role="button" tabindex="0" class="file-item${active ? " active" : ""}">
        <span class="dot ${dot}"></span>
        <span class="file-q">
          <span class="q">${escapeHtml(it.question)}</span>
          <span class="t">${timeAgo(it.created_at)}</span>
        </span>
        <button data-del="${it.id}" title="Delete" class="file-del">✕</button>
      </div>`;
    }).join("");

    list.querySelectorAll("[data-id]").forEach((row) => {
      row.onclick = (e) => { if (e.target.closest("[data-del]")) return; openHistory(+row.dataset.id); };
      row.onkeydown = (e) => { if (e.key === "Enter") openHistory(+row.dataset.id); };
    });
    list.querySelectorAll("[data-del]").forEach((x) => {
      x.onclick = (e) => { e.stopPropagation(); deleteHistory(+x.dataset.del); };
    });
  }

  async function openHistory(id) {
    closeSidebar();
    try {
      const rec = await fetch(`/api/history/${id}`).then((r) => { if (!r.ok) throw new Error("not found"); return r.json(); });
      activeHistoryId = id;
      highlightHistory();
      $("mainTitle").textContent = rec.question;

      const statusMap = {
        exported: { t: "Exported", color: "var(--verified)", dot: "dot-go" },
        draft:    { t: "Draft",    color: "var(--pending)",  dot: "dot-wait" },
        refused:  { t: "Refused",  color: "var(--refuted)",  dot: "dot-no" },
      };
      const st = statusMap[rec.status] || statusMap.draft;
      const when = new Date(rec.created_at * 1000).toLocaleString();

      $("main").innerHTML = `
        <div class="wrap">
          <div class="sheet rise" style="padding:1.4rem">
            <div style="display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:.7rem">
              <span class="banner" style="color:${st.color}"><span class="dot ${st.dot}"></span>${st.t}</span>
              <div style="display:flex;align-items:center;gap:.9rem">
                <span class="mono" style="font-size:.64rem;color:var(--ink-ghost)">${escapeHtml(when)} · ${rec.n_verified || 0} verified</span>
                ${rec.report ? `<a href="/api/history/${id}/pdf" class="btn btn-ghost" download style="text-decoration:none">Download PDF <span aria-hidden="true">↓</span></a>` : ""}
              </div>
            </div>
            <h3 style="font-family:var(--font-display);font-weight:600;font-size:1.3rem;letter-spacing:-.01em;color:var(--ink);margin:.9rem 0 0;line-height:1.2">${escapeHtml(rec.question)}</h3>
            <hr class="hair" style="margin:1.2rem 0" />
            ${rec.report
              ? renderReport(rec.report)
              : `<p style="color:var(--ink-soft);font-size:.96rem">No report was produced for this run${rec.status === "refused" ? " (the request was refused by guardrails)." : "."}</p>`}
            ${renderVerdicts(rec.verdicts)}
          </div>
        </div>`;
    } catch {
      toast("Could not open that item.", "error");
    }
  }

  async function deleteHistory(id) {
    try {
      await fetch(`/api/history/${id}`, { method: "DELETE" });
      if (activeHistoryId === id) renderComposer();
      loadHistory();
    } catch {
      toast("Delete failed.", "error");
    }
  }

  // Inline citation markers ↔ margin sources. Delegated once on document so the
  // wiring survives every re-render of a report.
  const anchorFor = (ref) => {
    const root = ref.closest("[data-report]");
    const n = ref.getAttribute("data-cite-ref");
    return root && root.querySelector(`[data-cite-anchor="${n}"]`);
  };

  // Click → smooth-scroll to the source and flash it.
  document.addEventListener("click", (e) => {
    const ref = e.target.closest("[data-cite-ref]");
    if (!ref) return;
    e.preventDefault();
    const target = anchorFor(ref);
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.classList.remove("cite-flash");
    void target.offsetWidth;            // restart the flash animation
    target.classList.add("cite-flash");
  });

  // Hover a marker → light up its source in the margin (the apparatus connecting).
  document.addEventListener("mouseover", (e) => {
    const ref = e.target.closest("[data-cite-ref]");
    if (!ref) return;
    ref.classList.add("is-active");
    const target = anchorFor(ref);
    if (target) target.classList.add("is-linked");
  });
  document.addEventListener("mouseout", (e) => {
    const ref = e.target.closest("[data-cite-ref]");
    if (!ref) return;
    ref.classList.remove("is-active");
    const target = anchorFor(ref);
    if (target) target.classList.remove("is-linked");
  });

  // ---- go ------------------------------------------------------------------
  boot();
})();
