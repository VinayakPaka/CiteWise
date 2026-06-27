"""Source-quality policy — which domains may be cited, and how authoritative.

The Researcher gathers evidence from web search; the
Fact-Checker only judges whether a snippet supports a claim, *not* whether the
source itself is credible. Without a gate, low-quality domains (social media,
video, forums) and tertiary encyclopedias (Wikipedia) end up cited in the final
brief — exactly the weakness a reviewer flags first.

This module is that gate. It classifies a URL's host into a tier and exposes:

  * ``is_citable(url)``    — drop sources that must never appear in a report
  * ``domain_score(url)``  — rank the survivors so authoritative *primary*
                             sources (gov labs/agencies, IGOs, peer-reviewed
                             journals) are preferred over ordinary sites
  * ``blocked_domains()``  — the denylist to hand a search API's exclude_domains

Member 1 (Vinayak Paka). The default policy is "strict academic": social/video/
forum domains are always non-citable, and tertiary wikis (Wikipedia, Britannica,
Investopedia, …) are non-citable too. Tune via env without code changes:

  CITEWISE_ALLOW_WIKIPEDIA=true          # treat wikis as citable (default: false)
  CITEWISE_BLOCKED_DOMAINS=a.com,b.com   # extra domains to always exclude
  CITEWISE_TRUSTED_DOMAINS=c.org,d.edu   # extra domains to treat as authoritative
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

# Never citable in a formal report: social media, video, forums/Q&A and other
# user-generated content. These are the sources a reviewer rejects on sight
# (Facebook, YouTube, Reddit, …).
_BLOCKED_DOMAINS = {
    "facebook.com", "fb.com", "fb.watch",
    "youtube.com", "youtu.be",
    "twitter.com", "x.com", "t.co",
    "instagram.com", "tiktok.com", "threads.net", "snapchat.com",
    "reddit.com", "quora.com", "stackexchange.com",
    "pinterest.com", "tumblr.com", "linkedin.com",
    "medium.com", "substack.com", "blogspot.com", "wordpress.com",
    "answers.com", "ask.com",
}

# Tertiary encyclopedias / wikis. Useful for orientation, but not a formal
# citation when a primary source exists. Non-citable unless CITEWISE_ALLOW_WIKIPEDIA.
_TERTIARY_DOMAINS = {
    "wikipedia.org", "wikimedia.org", "wikinews.org", "wiktionary.org",
    "wikiwand.com", "fandom.com", "wikihow.com",
    "britannica.com", "investopedia.com", "thoughtco.com", "simple.wikipedia.org",
}

# Authoritative top-level suffixes: US-style government / education / military /
# international-org domains.
_AUTHORITATIVE_TLDS = (".gov", ".edu", ".mil", ".int")
# International government / academic second-level domains paired with a country
# code, e.g. gov.uk, ac.uk, edu.au, go.jp, gob.mx, gouv.fr.
_AUTHORITATIVE_SLDS = {"gov", "edu", "ac", "mil", "gob", "gouv", "go"}

# Highly authoritative primary sources by name: government labs/agencies, IGOs,
# standards bodies, and major peer-reviewed publishers / scholarly repositories.
_TRUSTED_DOMAINS = {
    # Energy / climate primary sources
    "iea.org", "irena.org", "nrel.gov", "energy.gov", "eia.gov",
    "epa.gov", "lazard.com", "ipcc.ch", "bnef.com",
    # IGOs / official statistics / national science agencies
    "un.org", "worldbank.org", "oecd.org", "imf.org", "who.int",
    "europa.eu", "nasa.gov", "noaa.gov", "nist.gov", "nih.gov", "cdc.gov",
    # Peer-reviewed / scholarly publishers & repositories
    "nature.com", "science.org", "sciencedirect.com", "springer.com",
    "springeropen.com", "wiley.com", "tandfonline.com", "mdpi.com",
    "ncbi.nlm.nih.gov", "pubmed.ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov",
    "doi.org", "arxiv.org", "ssrn.com", "jstor.org", "cambridge.org",
    "oup.com", "ourworldindata.org",
}


def _host(url: str) -> str:
    """Lower-cased host of a URL, without a leading 'www.'."""
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _matches(host: str, domains: set[str]) -> bool:
    """True if ``host`` equals or is a subdomain of any domain in the set."""
    return any(host == d or host.endswith("." + d) for d in domains)


def _is_authoritative_host(host: str) -> bool:
    """True for government / education / military / IGO domains (US & international)."""
    if host.endswith(_AUTHORITATIVE_TLDS):
        return True
    parts = host.split(".")
    return (
        len(parts) >= 2
        and parts[-2] in _AUTHORITATIVE_SLDS
        and len(parts[-1]) == 2  # paired with a country-code TLD (gov.uk, go.jp);
        # the 2-char guard keeps ordinary hosts like go.com / ask.com out.
    )


def _env_domains(name: str) -> set[str]:
    return {d.strip().lower() for d in os.getenv(name, "").split(",") if d.strip()}


def _allow_wikipedia() -> bool:
    return os.getenv("CITEWISE_ALLOW_WIKIPEDIA", "false").lower() in {"1", "true", "yes"}


def is_citable(url: str) -> bool:
    """False for sources that must never appear in a formal report.

    Always blocks social media, video and forum domains. Also blocks tertiary
    wikis (Wikipedia, Britannica, …) unless CITEWISE_ALLOW_WIKIPEDIA is set.
    """
    host = _host(url)
    if not host:
        return False
    if _matches(host, _BLOCKED_DOMAINS) or _matches(host, _env_domains("CITEWISE_BLOCKED_DOMAINS")):
        return False
    if not _allow_wikipedia() and _matches(host, _TERTIARY_DOMAINS):
        return False
    return True


def domain_score(url: str) -> int:
    """Authority score for ranking citable sources (higher = more authoritative).

      3  authoritative primary source: gov/edu/mil/int domain, or a curated
         lab/agency/IGO/peer-reviewed publisher
      2  ordinary citable source (org / news / company site)
      1  tertiary wiki (only reachable when CITEWISE_ALLOW_WIKIPEDIA is set)
      0  no resolvable host
    """
    host = _host(url)
    if not host:
        return 0
    if _matches(host, _TERTIARY_DOMAINS):
        return 1
    if _is_authoritative_host(host):
        return 3
    if _matches(host, _TRUSTED_DOMAINS) or _matches(host, _env_domains("CITEWISE_TRUSTED_DOMAINS")):
        return 3
    return 2


def blocked_domains() -> list[str]:
    """Denylist to pass a search API's ``exclude_domains``, given current policy.

    Lets the search provider drop junk before it is ever returned; the client-side
    ``is_citable`` filter is still applied as the actual guarantee (it also catches
    subdomains the provider's matching may miss).
    """
    blocked = set(_BLOCKED_DOMAINS) | _env_domains("CITEWISE_BLOCKED_DOMAINS")
    if not _allow_wikipedia():
        blocked |= _TERTIARY_DOMAINS
    return sorted(blocked)
