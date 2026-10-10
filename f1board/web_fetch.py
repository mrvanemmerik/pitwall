import html
import ipaddress
import re
import socket
import urllib.parse
import urllib.request

USER_AGENT = "pitwall/1.0 (+https://github.com/mvanemmerik/pitwall)"
MAX_BYTES = 400_000
MAX_PAGE_CHARS = 5_000
MAX_WEB_CHARS = 4_500
RESULTS_INDEX = "https://www.formula1.com/en/results/2026/races"
_URL = re.compile(r"https://[^\s<>\"]+")
_SKIP = re.compile(
    r"^(hi|hello|hey|thanks|thank you|do you know my name\??|what is my name\??|who am i\??|tell me a joke)\W*$",
    re.IGNORECASE,
)


class WebFetchError(ValueError):
    pass


def gather_web(question, fetch=None, search=None, season=None, load_index=None):
    """Search or fetch only when the board is not the right source."""
    if _SKIP.match(question.strip()):
        return ""
    fetch = fetch or fetch_page
    search = search or search_news
    parts = []
    url = _first_url(question)
    if url:
        try:
            parts.append("Fetched " + url + ":\n" + fetch(url))
        except Exception:
            parts.append("Fetched " + url + ": unavailable")
    if _needs_result_page(question):
        official = official_results(question, season, fetch, load_index or _results_index)
        if official:
            parts.append(official)
    if not _needs_web(question):
        return "\n\n".join(parts).strip()[:MAX_WEB_CHARS]
    try:
        hits = search(question) or []
    except Exception:
        hits = []
    if hits:
        lines = ["News:"]
        for hit in hits[:3]:
            when = hit.get("snippet") or ""
            lines.append("- " + hit["title"] + ((" (" + when + ")") if when else ""))
            if hit.get("summary"):
                lines.append(hit["summary"])
        parts.append("\n".join(lines))
    return "\n\n".join(parts).strip()[:MAX_WEB_CHARS]


def fetch_page(url, urlopen=None):
    check_url(url)
    if urlopen is None:
        urlopen = urllib.request.build_opener(_SafeRedirect()).open
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain"})
    with urlopen(request, timeout=5) as response:
        raw = response.read(MAX_BYTES + 1)
        ctype = response.headers.get("content-type", "") if getattr(response, "headers", None) else ""
    if not raw:
        raise WebFetchError("empty page")
    raw = raw[:MAX_BYTES]
    text = raw.decode("utf-8", "replace")
    if "html" in ctype.lower() or "<html" in text[:800].lower() or "<!doctype" in text[:200].lower():
        tables = [html_to_text(table) for table in re.findall(r"(?is)<table\b.*?</table>", text)]
        tables = [table for table in tables if table]
        if tables:
            longest = max(tables, key=len)
            kept = [table for table in tables if len(table) > 40] or [longest]
            text = "\n".join(kept)
        else:
            text = html_to_text(text)
    text = " ".join(text.split())
    if not text:
        raise WebFetchError("empty page")
    return text[:MAX_PAGE_CHARS]


def search_news(question, urlopen=None):
    urlopen = urlopen or urllib.request.urlopen
    query = " ".join(_URL.sub(" ", question).split())[:180]
    if not query:
        return []
    if "2026" not in query:
        query = "2026 Formula 1 " + query
    search_url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
        "q": query,
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    })
    xml = _read(search_url, urlopen).decode("utf-8", "replace")
    hits = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S)[:3]:
        title = html.unescape(_tag(item, "title"))
        when = html.unescape(_tag(item, "pubDate"))
        if title:
            hits.append({"title": title, "url": "", "snippet": when, "summary": ""})
    return hits


def check_url(url):
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").rstrip(".").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password:
        raise WebFetchError("unsupported url")
    if parts.port not in (None, 443):
        raise WebFetchError("unsupported url")
    if host in {"localhost", "metadata.google.internal", "instance-data"} or host.endswith(".local") or host.endswith(".internal"):
        raise WebFetchError("unsupported url")
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebFetchError("unsupported url") from exc
    seen = set()
    for entry in addresses:
        address = entry[4][0]
        if address in seen:
            continue
        seen.add(address)
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise WebFetchError("unsupported url")


def html_to_text(html):
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    for old, new in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
        text = text.replace(old, new)
    return " ".join(text.split())


def _needs_web(question):
    text = question.lower()
    markers = (
        "news", "latest", "today", "yesterday", "weather", "tire", "tyre", "radio",
        "said", "quote", "practice", "qualifying", "penalty", "injured", "rumour",
        "rumor", "update", "article", "wikipedia", "search", "look up",
        "won", "winner", "sprint", "morning", "podium", "finished", "retired",
    )
    return any(marker in text for marker in markers)


def _needs_result_page(question):
    text = question.lower()
    return any(marker in text for marker in ("won", "winner", "sprint", "podium", "finished", "retired"))


def official_results(question, season, fetch, load_index):
    try:
        html = load_index()
    except Exception:
        return ""
    url = _result_url(question, season, html)
    if not url:
        return ""
    try:
        page = fetch(url)
    except Exception:
        return "Official results " + url + ": unavailable"
    return "Official results " + url + ":\n" + page


def _results_index():
    return _read(RESULTS_INDEX, urllib.request.urlopen).decode("utf-8", "replace")


def _result_url(question, season, html):
    found = re.findall(r'href="(/en/results/2026/races/\d+/([a-z0-9-]+)/race-result)"', html)
    slugs = []
    for href, slug in found:
        if slug not in [item[0] for item in slugs]:
            slugs.append((slug, href))
    if not slugs:
        return ""
    text = question.lower()
    chosen = ""
    for slug, href in slugs:
        if _slug_in(slug, text):
            chosen = href
            break
    if not chosen:
        chosen = _slug_from_season(season, slugs)
    if not chosen:
        return ""
    kind = "sprint-results" if "sprint" in text else "race-result"
    return "https://www.formula1.com" + chosen[: -len("race-result")] + kind


def _slug_from_season(season, slugs):
    schedule = (season or {}).get("schedule") or []
    finished = {row.get("round") for row in (season or {}).get("results") or []}
    pending = [row for row in schedule if row.get("round") not in finished]
    target = pending[0] if pending else (schedule[-1] if schedule else None)
    if not target:
        return ""
    name = str(target.get("name") or "")
    for slug, href in slugs:
        if _slug_in(slug, name.lower()):
            return href
    return ""


def _slug_in(slug, text):
    parts = [part for part in slug.split("-") if len(part) > 3]
    return any(part in text for part in parts)


def _tag(item, name):
    match = re.search(r"<" + name + r"[^>]*>(.*?)</" + name + r">", item, re.S)
    return " ".join(match.group(1).split()) if match else ""


def _first_url(question):
    match = _URL.search(question)
    if not match:
        return ""
    return match.group(0).rstrip(".,)")


def _read(url, urlopen):
    check_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=5) as response:
        return response.read(MAX_BYTES)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)
