#!/usr/bin/env python3
"""Crawl one or more configured torrent-listing sites and convert each
project's .torrent download into a magnet link, saving new ones to
torrents.json (a JSON object mapping "application name" -> [magnet link, ...])
for use with monitor.py.

This scraper has no site-specific logic built in. Every site is described
declaratively in scraper_config.json as a "strategy": how to discover project
pages (a sitemap, or paginated listing pages), how to find download links on
a project page (a regex), and how to resolve those into a final .torrent (or
magnet) URL (an optional redirect-following regex, or direct magnet links
embedded in the page). Adding support for a new site is normally just adding
a new entry to the "sites" list in scraper_config.json - no code changes.

Politeness: requests are sequential with a delay between them and a
descriptive User-Agent, so as not to hammer any site. Progress is written
incrementally so Ctrl+C at any point keeps what's been found so far.
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.parse

import torrent_utils

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(SCRIPT_DIR, 'scraper_config.json')

DEFAULT_TOP_LEVEL = {
    'output_file': 'torrents.json',
    'delay': 1.5,
    'category': None,
    'limit': None,
    'site': None,
    'user_agent': torrent_utils.DEFAULT_USER_AGENT,
    'sites': [],
}

logger = logging.getLogger('scraper')


def log(msg):
    """Back-compat shim for plain info-level logging."""
    logger.info(msg)


def configure_logging(verbose=False):
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)-7s %(message)s', datefmt='%H:%M:%S'))
    logger.setLevel(level)
    logger.handlers = [handler]
    logger.propagate = False


def load_config(config_path):
    """Load scraper_config.json, falling back to built-in defaults for any
    top-level field that's missing (or if the file doesn't exist at all).
    """
    config = dict(DEFAULT_TOP_LEVEL)
    if config_path and os.path.exists(config_path):
        logger.debug(f'Loading config from {config_path}')
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                user_config = json.load(f)
            config.update({k: v for k, v in user_config.items()
                           if v is not None or k in ('category', 'limit', 'site')})
            logger.debug(f'Config loaded: {len(config.get("sites") or [])} site(s) defined.')
        except (json.JSONDecodeError, OSError) as err:
            logger.warning(f'Failed to read {config_path} ({err}); using defaults.')
    elif config_path:
        logger.warning(f'No config file at {config_path}; using built-in defaults.')
    return config


def site_defaults(config, site):
    """Apply top-level fallbacks (delay/user_agent) to a site definition."""
    merged = {'delay': config['delay'], 'user_agent': config['user_agent']}
    merged.update(site)
    return merged


def get_project_pages(site):
    """Return the list of individual project page URLs for a site, per its
    configured discovery strategy ('sitemap' or 'listing').
    """
    discovery = site.get('discovery', {})
    dtype = discovery.get('type', 'sitemap')
    if dtype == 'sitemap':
        return _discover_via_sitemap(site, discovery)
    if dtype == 'listing':
        return _discover_via_listing(site, discovery)
    raise ValueError(f'Unknown discovery type {dtype!r} for site {site.get("name")!r}')


def _discover_via_sitemap(site, discovery):
    """Read a sitemap.xml, keep only URLs under the configured category
    paths, and drop any under a skip prefix. Bare category index pages
    (e.g. /distributions/ itself) are included only when the site has
    traverse_categories enabled, since they're only useful as a starting
    point for crawl()'s in-category link following.
    """
    base_url = site['base_url'].rstrip('/')
    sitemap_url = base_url + discovery.get('sitemap_path', '/sitemap.xml')
    category_paths = tuple(discovery.get('category_paths') or [''])
    skip_prefixes = tuple(discovery.get('skip_prefixes') or [])
    traverse_categories = site.get('traverse_categories', False)
    child_sitemap_pattern = discovery.get('child_sitemap_pattern')

    urls = _fetch_sitemap_urls(sitemap_url, site['user_agent'], child_sitemap_pattern)
    logger.debug(f'Sitemap contains {len(urls)} URL(s) total.')

    pages = []
    skipped_category = 0
    skipped_prefix = 0
    skipped_index = 0
    included_index = 0
    for url in urls:
        path = url.replace(base_url, '')
        if not path.startswith(category_paths):
            skipped_category += 1
            continue
        if any(path.startswith(p) for p in skip_prefixes):
            skipped_prefix += 1
            continue
        # bare category index pages (e.g. /distributions/) have nothing after the prefix
        if path in category_paths:
            if traverse_categories:
                pages.append(url)
                included_index += 1
            else:
                skipped_index += 1
            continue
        pages.append(url)
    logger.debug(f'Sitemap filtering: {len(pages)} kept, {skipped_category} outside category paths, '
                 f'{skipped_prefix} matched a skip prefix, {skipped_index} were bare category index pages '
                 f'(skipped), {included_index} were bare category index pages (included for traversal).')
    return pages


def _fetch_sitemap_urls(sitemap_url, user_agent, child_sitemap_pattern=None, _depth=0):
    """Fetch a sitemap.xml and return all page URLs it lists. Transparently
    follows sitemap *index* files (a "sitemap of sitemaps", common on
    WordPress sites) one level deep, fetching each child sitemap in turn. If
    `child_sitemap_pattern` is set, only child sitemaps whose URL matches it
    are followed (e.g. to skip attachment/category/author sitemaps and keep
    only the post sitemaps that actually contain project pages).
    """
    logger.debug(f'Fetching sitemap: {sitemap_url}')
    xml = torrent_utils.fetch_text(sitemap_url, user_agent=user_agent)
    if _depth == 0 and '<sitemapindex' in xml:
        child_sitemaps = re.findall(r'<loc>([^<]+)</loc>', xml)
        if child_sitemap_pattern:
            before = len(child_sitemaps)
            child_sitemaps = [u for u in child_sitemaps if re.search(child_sitemap_pattern, u)]
            logger.debug(f'{sitemap_url} is a sitemap index: {before} child sitemap(s), '
                         f'{len(child_sitemaps)} match child_sitemap_pattern.')
        else:
            logger.debug(f'{sitemap_url} is a sitemap index with {len(child_sitemaps)} child sitemap(s).')
        urls = []
        for child_url in child_sitemaps:
            urls.extend(_fetch_sitemap_urls(child_url, user_agent, child_sitemap_pattern, _depth=1))
        return urls
    return re.findall(r'<loc>([^<]+)</loc>', xml)


def _discover_via_listing(site, discovery):
    """Crawl paginated listing pages, following a "next page" link, and
    collect project page URLs matched by a regex on each listing page.
    """
    base_url = site['base_url'].rstrip('/')
    project_link_pattern = discovery['project_link_pattern']
    next_page_pattern = discovery.get('next_page_pattern')
    max_pages = discovery.get('max_pages', 50)

    pages = []
    seen_listing_pages = set()
    to_visit = list(discovery.get('start_paths', ['/']))

    while to_visit and len(seen_listing_pages) < max_pages:
        listing_path = to_visit.pop(0)
        listing_url = torrent_utils.resolve_url(listing_path, base_url)
        if listing_url in seen_listing_pages:
            continue
        seen_listing_pages.add(listing_url)

        logger.debug(f'Fetching listing page {len(seen_listing_pages)}/{max_pages}: {listing_url}')
        html = torrent_utils.fetch_text(listing_url, user_agent=site['user_agent'])
        time.sleep(site['delay'])

        new_links = 0
        for href in re.findall(project_link_pattern, html):
            url = torrent_utils.resolve_url(href, base_url)
            if url not in pages:
                pages.append(url)
                new_links += 1
        logger.debug(f'  found {new_links} new project link(s) on this listing page (total so far: {len(pages)}).')

        if next_page_pattern:
            m = re.search(next_page_pattern, html)
            if m:
                next_url = torrent_utils.resolve_url(m.group(1), base_url)
                if next_url not in seen_listing_pages:
                    to_visit.append(next_url)
                    logger.debug(f'  next page: {next_url}')
            else:
                logger.debug('  no next-page link found; stopping pagination.')

    if len(seen_listing_pages) >= max_pages and to_visit:
        logger.debug(f'Stopped after reaching max_pages={max_pages}; more listing pages may remain.')
    return pages


def extract_download_candidates(html, site):
    """Find candidate download hrefs on a project page via the site's
    configured regex (defaults to matching plain .torrent links).
    """
    pattern = site.get('download_link_pattern', r'href="([^"]+\.torrent[^"]*)"')
    return list(dict.fromkeys(re.findall(pattern, html)))


def _normalize_page_url(url):
    """Normalize a page URL for dedup purposes (ignore trailing slash)."""
    return url if url.endswith('/') else url + '/'


def _find_category_links(html, base_url, category_prefixes):
    """Find every href on a page that falls under one of the site's
    configured category paths (used to follow index/subcategory pages down
    to individual project pages, however deep the nesting goes).
    """
    found = []
    for href in re.findall(r'href="([^"#?]+)"', html):
        url = torrent_utils.resolve_url(href, base_url)
        path = url.replace(base_url.rstrip('/'), '')
        if path.startswith(category_prefixes):
            found.append(url)
    return found


def crawl(site):
    """Yields dicts: {'magnet': str, 'infoHash': str, 'name': str, 'source': url}"""
    base_url = site['base_url'].rstrip('/')
    delay = site['delay']
    user_agent = site['user_agent']
    name = site.get('name', base_url)
    traverse_categories = site.get('traverse_categories', False)
    category_prefixes = tuple(site.get('discovery', {}).get('category_paths') or ())

    logger.info(f'[{name}] Discovering project pages...')
    pages = get_project_pages(site)
    logger.debug(f'[{name}] {len(pages)} project page(s) before category/limit filters.')
    category = site.get('category')
    if category:
        before = len(pages)
        pages = [p for p in pages if f'/{category}/' in p]
        logger.debug(f'[{name}] Category filter "{category}": {before} -> {len(pages)} page(s).')
    if site.get('limit'):
        before = len(pages)
        pages = pages[:site['limit']]
        logger.debug(f'[{name}] Limit filter: {before} -> {len(pages)} page(s).')
    logger.info(f'[{name}] Found {len(pages)} project page(s) to scan'
                + (' (will also follow in-category links to discover more).' if traverse_categories else '.'))

    seen_hashes = set()
    site_found = 0
    total_scanned = 0
    limit = site.get('limit')
    visited = set()
    queued = {_normalize_page_url(p) for p in pages}
    queue = list(pages)

    while queue:
        if traverse_categories and limit and total_scanned >= limit:
            logger.debug(f'[{name}] Reached limit={limit} total page(s) scanned; stopping traversal '
                         f'({len(queue)} more page(s) left unscanned).')
            break
        page_url = queue.pop(0)
        normalized = _normalize_page_url(page_url)
        if normalized in visited:
            continue
        visited.add(normalized)
        total_scanned += 1

        logger.info(f'[{name}] [{total_scanned}] {page_url}')
        try:
            html = torrent_utils.fetch_text(page_url, user_agent=user_agent)
        except Exception as err:
            logger.error(f'  ! failed to fetch page: {err}')
            continue
        logger.debug(f'  fetched {len(html)} byte(s), sleeping {delay}s')
        time.sleep(delay)

        if traverse_categories and category_prefixes:
            new_links = 0
            for url in _find_category_links(html, base_url, category_prefixes):
                norm = _normalize_page_url(url)
                if norm in visited or norm in queued:
                    continue
                queued.add(norm)
                queue.append(url)
                new_links += 1
            if new_links:
                logger.debug(f'  discovered {new_links} more in-category link(s) '
                             f'(queue now has {len(queue)} page(s) left).')

        # Primary strategy: scan the whole page for any embedded magnet
        # links directly, regardless of site config. Most sites that embed
        # magnets need nothing more than this - no per-site regex required.
        page_magnets = torrent_utils.find_magnets(html)
        magnet_pattern = site.get('magnet_pattern')
        if magnet_pattern:
            matches = list(re.finditer(magnet_pattern, html))
            logger.debug(f'  {len(matches)} magnet link match(es) via site magnet_pattern.')
            page_magnets += [m.group('magnet') if 'magnet' in m.groupdict() else m.group(0)
                             for m in matches]

        if page_magnets:
            logger.debug(f'  {len(page_magnets)} magnet link(s) found directly embedded on page.')
            for magnet in page_magnets:
                info_hash = torrent_utils.info_hash_from_magnet(magnet)
                if not info_hash:
                    logger.debug(f'  - skipping magnet with no parsable info hash: {magnet[:60]}...')
                    continue
                if info_hash in seen_hashes:
                    logger.debug(f'  - skipping duplicate info hash {info_hash[:10]}...')
                    continue
                seen_hashes.add(info_hash)
                entry_name = (torrent_utils.name_from_magnet(magnet)
                              or urllib.parse.urlparse(page_url).path.strip('/').rsplit('/', 1)[-1]
                              or page_url)
                site_found += 1
                logger.info(f'  + {entry_name} ({info_hash[:10]}...)')
                yield {'magnet': magnet, 'infoHash': info_hash, 'name': entry_name, 'source': page_url}
            continue

        # Fallback strategy: no magnets embedded directly on this page, so
        # (if configured) follow the site's .torrent-download/redirect flow.
        if not site.get('download_link_pattern') and not site.get('redirect_pattern'):
            continue

        candidates = extract_download_candidates(html, site)
        logger.debug(f'  {len(candidates)} download link candidate(s) found.')
        if not candidates:
            continue

        redirect_pattern = site.get('redirect_pattern')
        require_torrent_suffix = site.get('require_torrent_suffix', True)

        for href in candidates:
            candidate_url = torrent_utils.resolve_url(href, base_url)
            logger.debug(f'  candidate: {candidate_url}')

            torrent_url = candidate_url
            if redirect_pattern:
                try:
                    intermediate_html = torrent_utils.fetch_text(candidate_url, user_agent=user_agent)
                except Exception as err:
                    logger.error(f'  ! failed to resolve download link: {err}')
                    continue
                time.sleep(delay)
                target = torrent_utils.extract_redirect_target(intermediate_html, redirect_pattern)
                if not target:
                    logger.debug('  - no redirect target found on intermediate page, skipping.')
                    continue
                torrent_url = torrent_utils.resolve_url(target, base_url)
                logger.debug(f'  resolved redirect -> {torrent_url}')

            if require_torrent_suffix and not torrent_url.lower().endswith('.torrent'):
                logger.debug(f'  - skipping non-.torrent link: {torrent_url}')
                continue  # skip direct .iso/.zip links with no torrent

            try:
                torrent_bytes = torrent_utils.fetch(torrent_url, user_agent=user_agent)
            except Exception as err:
                logger.error(f'  ! failed to download torrent file: {err}')
                continue
            logger.debug(f'  downloaded {len(torrent_bytes)} byte(s) torrent file.')
            time.sleep(delay)

            try:
                info = torrent_utils.torrent_to_magnet(torrent_bytes)
            except Exception as err:
                logger.error(f'  ! failed to parse torrent file: {err}')
                continue
            if not info:
                logger.debug('  - torrent file had no "info" dict, skipping.')
                continue
            if info['infoHash'] in seen_hashes:
                logger.debug(f'  - skipping duplicate info hash {info["infoHash"][:10]}...')
                continue
            seen_hashes.add(info['infoHash'])

            magnet = torrent_utils.build_magnet_uri(info['infoHash'], info['name'], info['trackers'])
            site_found += 1
            logger.info(f'  + {info["name"]} ({info["infoHash"][:10]}...)')
            yield {'magnet': magnet, 'infoHash': info['infoHash'], 'name': info['name'], 'source': page_url}

    logger.info(f'[{name}] Finished: {site_found} magnet(s) found across {total_scanned} page(s) scanned.')


def load_existing(torrents_file):
    """Load the existing torrents.json (name -> [magnet, ...]) if present.
    Returns (data_dict, set_of_known_info_hashes_lowercase).
    """
    data = {}
    if os.path.exists(torrents_file):
        logger.debug(f'Reading existing output file: {torrents_file}')
        try:
            with open(torrents_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.warning(f'{torrents_file} did not contain a JSON object; starting fresh.')
                data = {}
        except (json.JSONDecodeError, OSError) as err:
            logger.warning(f'Failed to read {torrents_file} ({err}); starting fresh.')
            data = {}
    else:
        logger.debug(f'No existing output file at {torrents_file}; starting fresh.')

    hashes = set()
    for links in data.values():
        for link in links:
            info_hash = torrent_utils.info_hash_from_magnet(link)
            if info_hash:
                hashes.add(info_hash)
    return data, hashes


def save_torrents(torrents_file, data):
    with open(torrents_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')
    logger.debug(f'Saved {torrents_file} ({len(data)} name(s)).')


def add_magnet(data, torrents_file, magnet, name):
    key = name or 'unknown'
    data.setdefault(key, [])
    if magnet not in data[key]:
        data[key].append(magnet)
    save_torrents(torrents_file, data)


def prompt_choice(title, options, allow_all_label=None):
    """Print a numbered menu of options and prompt the user to pick one.
    Returns the chosen option, or None if the user picked the "all" entry
    (only offered when allow_all_label is set) or just pressed Enter.
    """
    print(f'\n{title}')
    start = 0
    if allow_all_label:
        print(f'  0. {allow_all_label}')
        start = 0
    for i, opt in enumerate(options, 1):
        print(f'  {i}. {opt}')
    default = '0' if allow_all_label else '1'
    while True:
        raw = input(f'Select [default: {default}]: ').strip()
        if raw == '':
            raw = default
        if not raw.isdigit():
            print('Please enter a number from the list.')
            continue
        idx = int(raw)
        if allow_all_label and idx == 0:
            return None
        pick_idx = idx - 1
        if 0 <= pick_idx < len(options):
            return options[pick_idx]
        print('Invalid choice, try again.')


def interactive_select_sites(sites):
    """If more than one site is configured, let the user pick one (or all)."""
    if len(sites) <= 1:
        return sites
    names = [s.get('name', s.get('base_url', f'site {i}')) for i, s in enumerate(sites)]
    chosen = prompt_choice('Which site do you want to scrape?', names, allow_all_label='All sites')
    if chosen is None:
        return sites
    return [s for s, n in zip(sites, names) if n == chosen]


def interactive_select_category(sites):
    """Collect the distinct category paths available across the selected
    sites' sitemap-discovery config and let the user pick one (or all).
    """
    categories = []
    for site in sites:
        discovery = site.get('discovery', {})
        for path in discovery.get('category_paths') or []:
            name = path.strip('/')
            if name and name not in categories:
                categories.append(name)
    if not categories:
        return None
    return prompt_choice('Which category do you want to scrape?', categories, allow_all_label='All categories')


def main():
    parser = argparse.ArgumentParser(description='Scrape configured torrent-listing sites for magnet links.')
    parser.add_argument('--config', default=DEFAULT_CONFIG_PATH,
                         help='Path to config JSON (default: scraper_config.json next to this script)')
    parser.add_argument('-o', '--output', default=None,
                         help='File to append discovered magnet links to (overrides config output_file)')
    parser.add_argument('-d', '--delay', type=float, default=None,
                         help='Delay in seconds between HTTP requests (overrides config delay for all sites)')
    parser.add_argument('-c', '--category', default=None,
                         help='Only crawl project pages whose URL contains /<category>/ (overrides config category)')
    parser.add_argument('-n', '--limit', type=int, default=None,
                         help='Only scan the first N project pages per site (overrides config limit)')
    parser.add_argument('-s', '--site', default=None,
                         help='Only crawl the site with this "name" from the config (overrides config site)')
    parser.add_argument('-v', '--verbose', action='store_true',
                         help='Enable debug-level logging (per-page/per-link detail)')
    parser.add_argument('--no-interactive', action='store_true',
                         help='Never prompt for site/category selection, even in an interactive terminal '
                              '(useful for cron/automation)')
    args = parser.parse_args()

    configure_logging(verbose=args.verbose)

    config = load_config(args.config)
    if args.output is not None:
        config['output_file'] = args.output
    if args.category is not None:
        config['category'] = args.category
    if args.limit is not None:
        config['limit'] = args.limit
    if args.site is not None:
        config['site'] = args.site

    sites = config.get('sites') or []
    if not sites:
        logger.warning('No sites configured in scraper_config.json ("sites" is empty). Nothing to do.')
        return
    if config.get('site'):
        sites = [s for s in sites if s.get('name') == config['site']]
        if not sites:
            logger.error(f'No site named {config["site"]!r} found in config.')
            return

    interactive = not args.no_interactive and sys.stdin.isatty()
    if interactive and args.site is None and config.get('site') is None:
        sites = interactive_select_sites(sites)
    if interactive and config.get('category') is None:
        chosen_category = interactive_select_category(sites)
        if chosen_category:
            config['category'] = chosen_category

    logger.info(f'{len(sites)} site(s) queued to crawl: '
                f'{", ".join(s.get("name", s.get("base_url", "?")) for s in sites)}')

    output_file = config['output_file']
    if not os.path.isabs(output_file):
        output_file = os.path.join(SCRIPT_DIR, output_file)

    data, existing = load_existing(output_file)
    logger.info(f'{len(existing)} magnet(s) already present in {output_file}')

    found = 0
    added = 0
    per_site_stats = []
    try:
        for site in sites:
            merged = site_defaults(config, site)
            if args.delay is not None:
                merged['delay'] = args.delay
            if config.get('category'):
                merged['category'] = config['category']
            if config.get('limit'):
                merged['limit'] = config['limit']

            site_label = merged.get('name', merged['base_url'])
            logger.info(f'Target site: {site_label} ({merged["base_url"]})')
            site_found = 0
            site_added = 0
            for entry in crawl(merged):
                found += 1
                site_found += 1
                if entry['infoHash'].lower() in existing:
                    logger.debug(f'  already known, not re-added: {entry["name"]}')
                    continue
                add_magnet(data, output_file, entry['magnet'], entry['name'])
                existing.add(entry['infoHash'].lower())
                added += 1
                site_added += 1
            per_site_stats.append((site_label, site_found, site_added))
    except KeyboardInterrupt:
        logger.warning('Interrupted by user.')

    logger.info('Per-site summary:')
    for site_label, site_found, site_added in per_site_stats:
        logger.info(f'  {site_label}: {site_found} found, {site_added} new')
    logger.info(f'Done. Found {found} magnet link(s), added {added} new one(s) to {output_file}.')


if __name__ == '__main__':
    main()
