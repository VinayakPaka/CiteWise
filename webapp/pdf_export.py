"""Server-side PDF rendering of an approved CiteWise report.

Builds a polished, on-brand PDF — the "Critical Edition" editorial look from the
web app — out of a ``FinalReport`` dict, using reportlab (pure-Python, no native
dependencies, so it installs cleanly on Windows and in slim containers). The web
API streams the returned bytes to the user as a one-click download.

Design notes:
  * Palette and type roles mirror the web app's ``:root`` (navy headings, a green
    citation/verification accent, hairline rules, a mono kicker + footer).
  * reportlab's base-14 fonts (Times / Helvetica / Courier) are used so no font
    files need to be bundled; ``_winansi`` down-converts the Greek letters and
    math symbols that appear in research prose (e.g. "TNF-α", "−3.12 mmHg") into
    safe equivalents so nothing renders as a missing-glyph box.
  * Inline ``[n]`` citation markers become small green superscripts, matching the
    citation apparatus that is the project's signature.
"""
from __future__ import annotations

import io
import os
import re
import unicodedata
from datetime import datetime
from urllib.parse import urlparse

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    ListFlowable,
    ListItem,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.graphics.widgets.markers import makeMarker

# --- brand palette (from the web app's :root) --------------------------------
INK = HexColor("#16202E")
INK_SOFT = HexColor("#46556A")
INK_FAINT = HexColor("#8794A6")
NAVY = HexColor("#1C2E4A")
NAVY_300 = HexColor("#5C7196")
VERIFIED = HexColor("#157A60")
RULE = HexColor("#D3DAE4")
TINT = HexColor("#F1F4FA")  # faint cool tint for the summary box
GREEN_HEX = "#157A60"
FAINT_HEX = "#8794A6"

# --- fonts -------------------------------------------------------------------
# Embed the actual brand fonts (Fraunces display, Newsreader reading serif, IBM
# Plex Mono) bundled in webapp/fonts/, falling back to reportlab's base-14 if the
# files are ever missing so the export always works.
_FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")


def _register_brand_fonts() -> bool:
    files = {
        "Fraunces": "Fraunces-Display.ttf",
        "Newsreader": "Newsreader-Regular.ttf",
        "Newsreader-Bold": "Newsreader-SemiBold.ttf",
        "Newsreader-Italic": "Newsreader-Italic.ttf",
        "PlexMono": "IBMPlexMono-Regular.ttf",
        "PlexMono-Bold": "IBMPlexMono-Medium.ttf",
    }
    try:
        for name, fn in files.items():
            path = os.path.join(_FONT_DIR, fn)
            if not os.path.exists(path):
                return False
            pdfmetrics.registerFont(TTFont(name, path))
        # Map bold/italic so <b>/<i> inside body paragraphs resolve correctly.
        pdfmetrics.registerFontFamily(
            "Newsreader", normal="Newsreader", bold="Newsreader-Bold",
            italic="Newsreader-Italic", boldItalic="Newsreader-Bold",
        )
        pdfmetrics.registerFontFamily(
            "PlexMono", normal="PlexMono", bold="PlexMono-Bold",
            italic="PlexMono", boldItalic="PlexMono-Bold",
        )
        return True
    except Exception:
        return False


_BRAND_FONTS = _register_brand_fonts()

if _BRAND_FONTS:
    DISPLAY = "Fraunces"        # title + section headings
    SERIF = "Newsreader"        # body + summary (family carries bold/italic)
    SERIF_BOLD = "Newsreader-Bold"
    MONO = "PlexMono"           # kicker, meta, sources, footer
    MONO_BOLD = "PlexMono-Bold"
else:  # base-14 fallback (no font files)
    DISPLAY = "Times-Bold"
    SERIF = "Times-Roman"
    SERIF_BOLD = "Times-Bold"
    MONO = "Courier"
    MONO_BOLD = "Courier-Bold"

# Greek + math/symbol characters that appear in scientific prose but are not in
# the base-14 (WinAnsi) fonts — mapped to readable ASCII so they always render.
_SUBST = {
    "−": "-", "—": "-", "–": "-", "‐": "-", "‑": "-",
    "≥": ">=", "≤": "<=", "≈": "~", "≠": "!=", "±": "+/-",
    "→": "->", "←": "<-", "↔": "<->", "⇒": "=>", "∞": "inf",
    "↑": "up", "↓": "down", "‰": " per mille", "′": "'", "″": '"',
    "×": "x", "÷": "/", "·": "·", "∼": "~", "≅": "~=",
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon",
    "ζ": "zeta", "η": "eta", "θ": "theta", "ι": "iota", "κ": "kappa",
    "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi", "ο": "o", "π": "pi",
    "ρ": "rho", "σ": "sigma", "ς": "sigma", "τ": "tau", "υ": "upsilon",
    "φ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega",
    "Α": "Alpha", "Β": "Beta", "Γ": "Gamma", "Δ": "Delta", "Θ": "Theta",
    "Λ": "Lambda", "Π": "Pi", "Σ": "Sigma", "Φ": "Phi", "Ω": "Omega",
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
    "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
}


def _winansi(s: str) -> str:
    """Down-convert a string to characters the base-14 fonts can render.

    Known scientific symbols are mapped to readable equivalents; anything else
    outside WinAnsi is decomposed to ASCII (stripping accents) or dropped, so the
    PDF never shows a missing-glyph box.
    """
    s = "".join(_SUBST.get(ch, ch) for ch in (s or ""))
    out = []
    for ch in s:
        try:
            ch.encode("cp1252")  # WinAnsi ≈ cp1252
            out.append(ch)
        except UnicodeEncodeError:
            out.append(unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode("ascii"))
    s = "".join(out)
    # Tidy up spacing left behind when an unmapped symbol was dropped.
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r" +([)\]])", r"\1", s)
    return s


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _markup(text: str, n_cites: int) -> str:
    """Sanitise + escape text, then apply light markdown and citation styling.

    Produces reportlab mini-HTML: **bold**, *italic*, `code`, and ``[n]`` markers
    rendered as small green superscripts (only for n within the source count).
    """
    s = _xml_escape(_winansi(text))
    s = re.sub(r"`([^`]+)`", rf'<font face="{MONO}">\1</font>', s)
    s = re.sub(r"\*\*([^*]+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", s)

    def _cite(m: "re.Match") -> str:
        k = int(m.group(1))
        if 1 <= k <= n_cites:
            return f'<super size="7" rise="3"><font color="{GREEN_HEX}">{k}</font></super>'
        return m.group(0)

    return re.sub(r"\[(\d{1,3})\]", _cite, s)


def _host(url: str) -> str:
    try:
        return urlparse(url).hostname.replace("www.", "", 1) or url
    except Exception:
        return url


# --- paragraph styles --------------------------------------------------------
_KICKER = ParagraphStyle(
    "kicker", fontName=MONO, fontSize=7.5, textColor=NAVY_300, leading=11,
    spaceAfter=8,
)
_TITLE = ParagraphStyle(
    "title", fontName=DISPLAY, fontSize=22, textColor=INK, leading=25.5,
    spaceAfter=8,
)
_META = ParagraphStyle(
    "meta", fontName=MONO, fontSize=7.6, textColor=INK_FAINT, leading=11,
)
_LEAD = ParagraphStyle(
    "lead", fontName=SERIF, fontSize=12, textColor=INK_SOFT, leading=18,
    alignment=TA_JUSTIFY, spaceAfter=6,
)
_H2 = ParagraphStyle(
    "h2", fontName=DISPLAY, fontSize=13.5, textColor=NAVY, leading=17,
    spaceBefore=15, spaceAfter=5,
)
_BODY = ParagraphStyle(
    "body", fontName=SERIF, fontSize=10.5, textColor=INK, leading=16,
    alignment=TA_JUSTIFY, spaceAfter=5,
)
_SRC_LABEL = ParagraphStyle(
    "srcLabel", fontName=MONO, fontSize=8, textColor=NAVY_300, leading=12,
    spaceBefore=4, spaceAfter=7,
)
_SRC = ParagraphStyle(
    "src", fontName=SERIF, fontSize=9.5, textColor=INK, leading=13, spaceAfter=7,
    alignment=TA_LEFT,
)
_NOTE = ParagraphStyle(
    "note", fontName=MONO, fontSize=7.2, textColor=INK_FAINT, leading=11,
    spaceBefore=8,
)
_SUMM_LABEL = ParagraphStyle(
    "summLabel", fontName=MONO, fontSize=7, textColor=VERIFIED, leading=10,
    spaceAfter=5,
)
_SUMM_TEXT = ParagraphStyle(
    "summText", fontName=SERIF, fontSize=11.5, textColor=INK_SOFT, leading=17,
    alignment=TA_JUSTIFY, spaceAfter=0,
)
_STAT_VALUE = ParagraphStyle(
    "statVal", fontName=DISPLAY, fontSize=17, textColor=NAVY, leading=19,
    alignment=TA_CENTER,
)
_STAT_LABEL = ParagraphStyle(
    "statLab", fontName=SERIF, fontSize=8.2, textColor=INK_SOFT, leading=10.5,
    alignment=TA_CENTER, spaceBefore=3,
)
_STAT_REF = ParagraphStyle(
    "statRef", fontName=MONO, fontSize=6.5, textColor=VERIFIED, leading=9,
    alignment=TA_CENTER, spaceBefore=2,
)

_CHART_COLORS = [VERIFIED, NAVY, NAVY_300]


def _safe_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _stat_band(figures: list, width: float):
    """A row of headline 'key figure' cells (value / label / source ref)."""
    cells = []
    for f in figures:
        cell = [
            Paragraph(_xml_escape(_winansi(str(f.get("value", "")))), _STAT_VALUE),
            Paragraph(_xml_escape(_winansi(str(f.get("label", "")))), _STAT_LABEL),
        ]
        ref = f.get("source_index")
        if ref:
            cell.append(Paragraph(f"[{int(ref)}]", _STAT_REF))
        cells.append(cell)
    col = width / len(cells)
    table = Table([cells], colWidths=[col] * len(cells))
    style = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 11),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEABOVE", (0, 0), (-1, 0), 1.2, NAVY),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, RULE),
    ]
    for i in range(1, len(cells)):
        style.append(("LINEBEFORE", (i, 0), (i, 0), 0.5, RULE))
    table.setStyle(TableStyle(style))
    return table


def _chart_drawing(chart: dict, width: float):
    """A simple on-brand bar or line chart built from the spec (vector graphics)."""
    cats = [str(c) for c in (chart.get("categories") or [])]
    vals = [_safe_float(v) for v in (chart.get("values") or [])]
    k = min(len(cats), len(vals))
    if k < 2:
        return None
    cats, vals = cats[:k], vals[:k]
    data = [vals]  # single flat series
    ch_h = 148
    d = Drawing(width, ch_h + 52)
    d.add(String(2, ch_h + 32, _winansi(str(chart.get("title", ""))),
                 fontName=DISPLAY, fontSize=10.5, fillColor=NAVY))
    if chart.get("y_label"):
        d.add(String(2, ch_h + 18, _winansi(str(chart["y_label"])),
                     fontName=MONO, fontSize=6, fillColor=INK_FAINT))
    is_line = chart.get("kind") == "line"
    c = HorizontalLineChart() if is_line else VerticalBarChart()
    c.x, c.y = 42, 24
    c.width = width - 58
    c.height = ch_h - 8
    c.data = data
    c.categoryAxis.categoryNames = cats
    c.categoryAxis.labels.fontName = MONO
    c.categoryAxis.labels.fontSize = 6.5
    c.categoryAxis.labels.fillColor = INK_SOFT
    c.categoryAxis.strokeColor = RULE
    c.valueAxis.labels.fontName = MONO
    c.valueAxis.labels.fontSize = 6.5
    c.valueAxis.labels.fillColor = INK_FAINT
    c.valueAxis.strokeColor = RULE
    c.valueAxis.gridStrokeColor = HexColor("#E7ECF3")
    c.valueAxis.visibleGrid = True
    if is_line:
        c.lines.strokeWidth = 2
        c.lines[0].strokeColor = VERIFIED
        marker = makeMarker("FilledCircle")
        marker.size = 3.5
        marker.fillColor = VERIFIED
        c.lines[0].symbol = marker
    else:
        c.barWidth = 9
        c.groupSpacing = 12
        c.valueAxis.valueMin = 0
        c.bars[0].fillColor = VERIFIED
        c.bars[0].strokeColor = None
    d.add(c)
    return d


def _content_flowables(text: str, n_cites: int) -> list:
    """Turn a section's plain text into paragraphs + bullet lists (flowables)."""
    flow: list = []
    para: list[str] = []
    items: list[str] = []

    def flush_para() -> None:
        if para:
            flow.append(Paragraph(_markup(" ".join(para), n_cites), _BODY))
            para.clear()

    def flush_items() -> None:
        if items:
            flow.append(ListFlowable(
                [ListItem(Paragraph(_markup(it, n_cites), _BODY), leftIndent=6,
                          value=None) for it in items],
                bulletType="bullet", start="•", leftIndent=14,
                bulletColor=VERIFIED, bulletFontName=SERIF, bulletFontSize=7,
            ))
            items.clear()

    for raw in (text or "").replace("\r", "").split("\n"):
        line = raw.strip()
        if not line:
            flush_para(); flush_items(); continue
        bullet = re.match(r"^[-*•]\s+(.*)", line)
        numbered = re.match(r"^\d+[.)]\s+(.*)", line)
        if bullet:
            flush_para(); items.append(bullet.group(1))
        elif numbered:
            flush_para(); items.append(numbered.group(1))
        else:
            flush_items(); para.append(line)
    flush_para(); flush_items()
    return flow


def _footer(canvas, doc) -> None:
    """Running footer: a hairline, the wordmark, and a page number."""
    canvas.saveState()
    y = 13 * mm
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(doc.leftMargin, y + 4 * mm, doc.pagesize[0] - doc.rightMargin, y + 4 * mm)
    canvas.setFont(MONO, 7)
    canvas.setFillColor(INK_FAINT)
    canvas.drawString(doc.leftMargin, y, "CiteWise · every claim traced to its source")
    canvas.drawRightString(doc.pagesize[0] - doc.rightMargin, y, f"{doc.page}")
    canvas.restoreState()


def build_report_pdf(question: str, report: dict, *, meta: dict | None = None) -> bytes:
    """Render an approved report to PDF bytes.

    ``report`` is the FinalReport dict (``summary``, ``sections``, ``citations``).
    ``meta`` may carry ``created_at`` (unix ts), ``provider``, ``model`` and
    ``n_verified`` for the header line.
    """
    meta = meta or {}
    summary = report.get("summary", "") or ""
    sections = report.get("sections", []) or []
    citations = report.get("citations", []) or []
    n_cites = len(citations)
    figures = report.get("key_figures") or []
    chart = report.get("chart") or None

    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=22 * mm, rightMargin=22 * mm, topMargin=20 * mm, bottomMargin=22 * mm,
        title=f"CiteWise — {_winansi(question)[:90]}", author="CiteWise",
        subject="Verified research brief",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=_footer)])

    # header / meta line
    ts = meta.get("created_at")
    when = datetime.fromtimestamp(ts) if ts else datetime.now()
    bits = [when.strftime("%d %b %Y")]
    if meta.get("n_verified") is not None:
        bits.append(f"{meta['n_verified']} verified claims")
    bits.append(f"{n_cites} source{'s' if n_cites != 1 else ''}")
    if meta.get("provider"):
        bits.append(f"{meta['provider']}" + (f"/{meta['model']}" if meta.get("model") else ""))
    meta_line = _xml_escape(_winansi("  ·  ".join(bits)))

    story: list = [
        Paragraph("CITEWISE &nbsp; / &nbsp; VERIFIED RESEARCH BRIEF", _KICKER),
        Paragraph(_xml_escape(_winansi(question)), _TITLE),
        Paragraph(meta_line, _META),
        Spacer(1, 7),
        HRFlowable(width="100%", thickness=1, color=NAVY, spaceBefore=2, spaceAfter=12),
    ]

    if summary:
        # A clearly-labelled "at a glance" box so a reader grasps the gist first.
        box = Table(
            [[[Paragraph("SUMMARY", _SUMM_LABEL),
               Paragraph(_markup(summary, n_cites), _SUMM_TEXT)]]],
            colWidths=[doc.width],
        )
        box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), TINT),
            ("LINEBEFORE", (0, 0), (-1, -1), 2.2, VERIFIED),
            ("LEFTPADDING", (0, 0), (-1, -1), 13),
            ("RIGHTPADDING", (0, 0), (-1, -1), 13),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
        ]))
        story.append(box)
        story.append(Spacer(1, 8))

    # Infographic: key-figure stat band + an optional chart, when present.
    if figures:
        story.append(_stat_band(figures, doc.width))
        story.append(Spacer(1, 11))
    if chart and chart.get("values"):
        drawing = _chart_drawing(chart, doc.width)
        if drawing is not None:
            story.append(drawing)
            story.append(Spacer(1, 12))

    for s in sections:
        story.append(Paragraph(_xml_escape(_winansi(s.get("heading", ""))), _H2))
        story.extend(_content_flowables(s.get("content", ""), n_cites))

    if citations:
        story.append(Spacer(1, 8))
        story.append(HRFlowable(width="100%", thickness=0.6, color=RULE, spaceBefore=6, spaceAfter=4))
        story.append(Paragraph("S O U R C E S", _SRC_LABEL))
        for i, url in enumerate(citations, 1):
            safe_url = _xml_escape(_winansi(url))
            safe_host = _xml_escape(_winansi(_host(url)))
            story.append(Paragraph(
                f'<font color="{GREEN_HEX}">•</font> '
                f'<font face="{MONO_BOLD}" color="{GREEN_HEX}">{i}</font>&nbsp;&nbsp;'
                f'<b>{safe_host}</b><br/>'
                f'<font face="{MONO}" size="7.5" color="{FAINT_HEX}">{safe_url}</font>',
                _SRC,
            ))
        story.append(HRFlowable(width="38%", thickness=0.5, color=RULE, spaceBefore=4, spaceAfter=0, hAlign="LEFT"))
        story.append(Paragraph("Every claim above is anchored to a cited source.", _NOTE))

    doc.build(story)
    return buf.getvalue()


def filename_for(question: str) -> str:
    """A safe, descriptive download filename derived from the question."""
    slug = unicodedata.normalize("NFKD", question or "report").encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", slug).strip("-").lower()[:60] or "report"
    return f"citewise-{slug}.pdf"
