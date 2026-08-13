"""Fetch a fosstorrents.com-style project page's .torrent download and
compute its magnet link (info hash + trackers), using only the standard
library.
"""

import hashlib
import re
import urllib.request

import bencode

DEFAULT_USER_AGENT = 'P2P-Monitor-Scraper/1.0 (+https://github.com/) research/personal use'


def fetch(url, timeout=15, user_agent=DEFAULT_USER_AGENT):
    req = urllib.request.Request(url, headers={'User-Agent': user_agent})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_text(url, timeout=15, user_agent=DEFAULT_USER_AGENT):
    return fetch(url, timeout=timeout, user_agent=user_agent).decode('utf-8', errors='replace')


def resolve_torrent_url(thankyou_url, base_url, timeout=15, user_agent=DEFAULT_USER_AGENT):
    """The /thankyou/ page redirects via a JS `window.location = "..."` to the
    actual .torrent (or direct file) download. Extract that URL.
    """
    html = fetch_text(thankyou_url, timeout=timeout, user_agent=user_agent)
    m = re.search(r'window\.location\s*=\s*"([^"]+)"', html)
    if not m:
        return None
    path = m.group(1)
    if path.startswith('http'):
        return path
    return base_url.rstrip('/') + path


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

    magnet = f'magnet:?xt=urn:btih:{info_hash}'
    return {'infoHash': info_hash, 'name': name, 'trackers': trackers}


def build_magnet_uri(info_hash, name=None, trackers=None):
    import urllib.parse
    parts = [f'xt=urn:btih:{info_hash}']
    if name:
        parts.append('dn=' + urllib.parse.quote(name))
    for t in (trackers or []):
        parts.append('tr=' + urllib.parse.quote(t, safe=''))
    return 'magnet:?' + '&'.join(parts)
