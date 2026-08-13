"""Generic HTTP fetch + .torrent/magnet helpers shared by scraper.py across
all configured sites, using only the standard library.

None of this module is specific to any one site; per-site behaviour (which
pages to visit, how to find download links, how to resolve redirects) lives
entirely in scraper_config.json and is interpreted generically by scraper.py.
"""

import hashlib
import html
import re
import urllib.parse
import urllib.request

import bencode

MAGNET_RE = re.compile(r'magnet:\?[^\s"\'<>]+')

DEFAULT_USER_AGENT = 'P2P-Monitor-Scraper/1.0 (+https://github.com/) research/personal use'


def fetch(url, timeout=15, user_agent=DEFAULT_USER_AGENT):
    req = urllib.request.Request(url, headers={'User-Agent': user_agent})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_text(url, timeout=15, user_agent=DEFAULT_USER_AGENT):
    return fetch(url, timeout=timeout, user_agent=user_agent).decode('utf-8', errors='replace')


def resolve_url(url, base_url):
    """Resolve a possibly-relative URL (and normalize stray spaces/entities in
    query strings) against a site's base URL. Absolute URLs pass through.
    """
    url = url.replace('&amp;', '&')
    if url.startswith('http://') or url.startswith('https://'):
        return url
    return urllib.parse.urljoin(base_url.rstrip('/') + '/', urllib.parse.quote(url, safe='/?=&'))


def extract_redirect_target(html, redirect_pattern):
    """Apply a site-specific regex (e.g. matching a JS `window.location =
    "..."` redirect or an HTML meta-refresh tag) to an intermediate download
    page to find the real file URL. Returns None if no match.
    """
    m = re.search(redirect_pattern, html)
    if not m:
        return None
    return m.group(1)


def info_hash_from_magnet(magnet):
    """Extract the 40-char hex info hash from a magnet URI, if present."""
    m = re.search(r'btih:([a-fA-F0-9]{40})', magnet)
    return m.group(1).lower() if m else None


def name_from_magnet(magnet):
    """Extract the display name (`dn=`) from a magnet URI, if present."""
    query = magnet.split('?', 1)[1] if '?' in magnet else magnet
    params = urllib.parse.parse_qs(query)
    values = params.get('dn')
    return values[0] if values else None


def torrent_to_magnet(torrent_bytes, fallback_name=None):
    """Parse raw .torrent bytes and build a magnet URI."""
    decoded = bencode.decode(torrent_bytes)
    info = decoded.get(b'info')
    if info is None:
        return None

    info_hash = hashlib.sha1(bencode.encode(info)).hexdigest()

    name = info.get(b'name')
    name = name.decode('utf-8', errors='replace') if name else fallback_name

    trackers = []
    announce = decoded.get(b'announce')
    if announce:
        trackers.append(announce.decode('utf-8', errors='replace'))
    for tier in decoded.get(b'announce-list', []):
        for t in tier:
            url = t.decode('utf-8', errors='replace')
            if url not in trackers:
                trackers.append(url)

    return {'infoHash': info_hash, 'name': name, 'trackers': trackers}


def build_magnet_uri(info_hash, name=None, trackers=None):
    parts = [f'xt=urn:btih:{info_hash}']
    if name:
        parts.append('dn=' + urllib.parse.quote(name))
    for t in (trackers or []):
        parts.append('tr=' + urllib.parse.quote(t, safe=''))
    return 'magnet:?' + '&'.join(parts)


def find_magnets(page_html):
    """Scan a full page's raw HTML for any embedded magnet URIs.

    This is the primary, always-on discovery strategy: many sites (e.g.
    WordPress blogs) embed magnet links directly in the page body, with no
    .torrent file or redirect involved. HTML-entity-encoded ampersands
    (`&amp;`, `&#038;`) inside the magnet's query string are decoded so the
    full link (all `tr=`/`dn=` params) is captured, not just the `xt=` part.
    Returns a list of magnet URI strings, in document order, not de-duplicated.
    """
    return [html.unescape(m.group(0)) for m in MAGNET_RE.finditer(page_html)]
