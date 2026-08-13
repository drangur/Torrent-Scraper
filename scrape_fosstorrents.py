#!/usr/bin/env python3
"""Crawl a fosstorrents.com-style site and convert every project's .torrent
download into a magnet link, appending new ones to torrents.txt for use with
monitor.py.

fosstorrents.com is a legitimate site that tracks and seeds official
releases of free/open-source software, games and Linux/BSD distributions —
it does not host copyrighted or pirated content.

Site details (base URL, sitemap, category paths, output file, delay, etc.)
are read from scraper_config.json next to this script; CLI flags override
individual config values for one-off runs.

Politeness: requests are sequential with a delay between them and a
descriptive User-Agent, so as not to hammer the site. Progress is written
incrementally so Ctrl+C at any point keeps what's been found so far.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

import bencode
import fosstorrents

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(SCRIPT_DIR, 'scraper_config.json')

DEFAULT_CONFIG = {
    'base_url': 'https://fosstorrents.com',
    'sitemap_path': '/sitemap.xml',
    'category_paths': ['/distributions/', '/games/', '/softwares/'],
    'skip_prefixes': ['/blog/', '/batches/', '/partnership/', '/donate', '/search/'],
    'output_file': 'torrents.txt',
    'delay': 1.5,
    'category': None,
    'limit': None,
    'user_agent': fosstorrents.DEFAULT_USER_AGENT,
}


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def load_config(config_path):
    """Load scraper_config.json, falling back to built-in defaults for any
    field that's missing (or if the file doesn't exist at all).
    """
    config = dict(DEFAULT_CONFIG)
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                user_config = json.load(f)
            config.update({k: v for k, v in user_config.items() if v is not None or k in ('category', 'limit')})
        except (json.JSONDecodeError, OSError) as err:
            log(f'Warning: failed to read {config_path} ({err}); using defaults.')
    elif config_path:
        log(f'No config file at {config_path}; using built-in defaults.')
    return config


def get_project_pages(config):
    """Return the list of individual project page URLs from the sitemap,
    filtered to the configured category paths and excluding skip prefixes.
    """
    base_url = config['base_url'].rstrip('/')
    sitemap_url = base_url + config['sitemap_path']
    category_paths = tuple(config['category_paths'])
    skip_prefixes = tuple(config['skip_prefixes'])

    xml = fosstorrents.fetch_text(sitemap_url, user_agent=config['user_agent'])
    urls = re.findall(r'<loc>([^<]+)</loc>', xml)
    pages = []
    for url in urls:
        path = url.replace(base_url, '')
        if not path.startswith(category_paths):
            continue
        if any(path.startswith(p) for p in skip_prefixes):
            continue
        # bare category index pages (e.g. /distributions/) have nothing after the prefix
        if path in category_paths:
            continue
        pages.append(url)
    return pages


def extract_download_buttons(html):
    """Find every /thankyou/?... download button link on a project page."""
    return list(dict.fromkeys(re.findall(r'href="(/thankyou/\?[^"]+)"', html)))


def crawl(config):
    """Yields dicts: {'magnet': str, 'infoHash': str, 'name': str, 'source': url}"""
    base_url = config['base_url'].rstrip('/')
    delay = config['delay']
    user_agent = config['user_agent']

    log('Fetching sitemap...')
    pages = get_project_pages(config)
    if config.get('category'):
        pages = [p for p in pages if f'/{config["category"]}/' in p]
    if config.get('limit'):
        pages = pages[:config['limit']]
    log(f'Found {len(pages)} project page(s) to scan.')

    seen_hashes = set()

    for i, page_url in enumerate(pages, 1):
        log(f'[{i}/{len(pages)}] {page_url}')
        try:
            html = fosstorrents.fetch_text(page_url, user_agent=user_agent)
        except Exception as err:
            log(f'  ! failed to fetch page: {err}')
            continue
        time.sleep(delay)

        buttons = extract_download_buttons(html)
        if not buttons:
            continue

        for button_path in buttons:
            # Query params may contain raw spaces (e.g. "cat=Latest Edition"); quote them.
            thankyou_url = base_url + urllib.parse.quote(
                button_path.replace('&amp;', '&'), safe='/?=&'
            )
            try:
                torrent_url = fosstorrents.resolve_torrent_url(thankyou_url, base_url, user_agent=user_agent)
            except Exception as err:
                log(f'  ! failed to resolve download link: {err}')
                continue
            time.sleep(delay)

            if not torrent_url or not torrent_url.lower().endswith('.torrent'):
                continue  # skip direct .iso/.zip links with no torrent

            try:
                torrent_bytes = fosstorrents.fetch(torrent_url, user_agent=user_agent)
            except Exception as err:
                log(f'  ! failed to download torrent file: {err}')
                continue
            time.sleep(delay)

            try:
                info = fosstorrents.torrent_to_magnet(torrent_bytes)
            except Exception as err:
                log(f'  ! failed to parse torrent file: {err}')
                continue
            if not info or info['infoHash'] in seen_hashes:
                continue
            seen_hashes.add(info['infoHash'])

            magnet = fosstorrents.build_magnet_uri(info['infoHash'], info['name'], info['trackers'])
            log(f'  + {info["name"]} ({info["infoHash"][:10]}...)')
            yield {'magnet': magnet, 'infoHash': info['infoHash'], 'name': info['name'], 'source': page_url}


def load_existing_hashes(torrents_file):
    hashes = set()
    if not os.path.exists(torrents_file):
        return hashes
    with open(torrents_file, 'r', encoding='utf-8') as f:
        for line in f:
            m = re.search(r'btih:([a-fA-F0-9]{40})', line)
            if m:
                hashes.add(m.group(1).lower())
    return hashes


def append_magnet(torrents_file, magnet, name, source):
    with open(torrents_file, 'a', encoding='utf-8') as f:
        f.write(f'\n# {name or "unknown"} — via {source}\n{magnet}\n')


def main():
    parser = argparse.ArgumentParser(description='Scrape a fosstorrents.com-style site for magnet links.')
    parser.add_argument('--config', default=DEFAULT_CONFIG_PATH,
                         help='Path to config JSON (default: scraper_config.json next to this script)')
    parser.add_argument('-o', '--output', default=None,
                         help='File to append discovered magnet links to (overrides config output_file)')
    parser.add_argument('-d', '--delay', type=float, default=None,
                         help='Delay in seconds between HTTP requests (overrides config delay)')
    parser.add_argument('-c', '--category', choices=['distributions', 'games', 'softwares'], default=None,
                         help='Only crawl one category instead of the whole site (overrides config category)')
    parser.add_argument('-n', '--limit', type=int, default=None,
                         help='Only scan the first N project pages (overrides config limit)')
    args = parser.parse_args()

    config = load_config(args.config)
    if args.output is not None:
        config['output_file'] = args.output
    if args.delay is not None:
        config['delay'] = args.delay
    if args.category is not None:
        config['category'] = args.category
    if args.limit is not None:
        config['limit'] = args.limit

    output_file = config['output_file']
    if not os.path.isabs(output_file):
        output_file = os.path.join(SCRIPT_DIR, output_file)

    log(f'Target site: {config["base_url"]}')

    existing = load_existing_hashes(output_file)
    log(f'{len(existing)} magnet(s) already present in {output_file}')

    found = 0
    added = 0
    try:
        for entry in crawl(config):
            found += 1
            if entry['infoHash'].lower() in existing:
                continue
            append_magnet(output_file, entry['magnet'], entry['name'], entry['source'])
            existing.add(entry['infoHash'].lower())
            added += 1
    except KeyboardInterrupt:
        log('\nInterrupted by user.')

    log(f'\nDone. Found {found} magnet link(s), added {added} new one(s) to {output_file}.')


if __name__ == '__main__':
    main()

