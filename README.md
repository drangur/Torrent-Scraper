# Torrent-Scraper

A standalone scraper that crawls torrent-listing sites (e.g.
[fosstorrents.com](https://fosstorrents.com), a legitimate site that
tracks/seeds official releases of FOSS software, games and Linux/BSD
distributions) and converts each project's `.torrent` download into a
magnet link (by downloading the `.torrent` file and computing its info
hash), saving new ones to an output file (`torrents.json` by default). No
file data is ever downloaded — only the small `.torrent` metadata files.

`torrents.json` is a JSON object mapping application name -> list of magnet
links, e.g.:

```json
{
  "AerynOS-2026.08-GNOME-live-x86_64.iso": [
    "magnet:?xt=urn:btih:...&dn=AerynOS-2026.08-GNOME-live-x86_64.iso&tr=..."
  ],
  "alpine-standard-3.23.3-x86_64.iso": [
    "magnet:?xt=urn:btih:...",
    "magnet:?xt=urn:btih:..."
  ]
}
```

The resulting `torrents.json` can be fed into a separate monitoring tool (e.g.
[P2P Monitor](https://github.com/)) to track seeder/leecher counts.

Requires Python 3.8+. No third-party dependencies — pure standard library.

## Generic, config-driven sites

The scraper has no site-specific code. Every site is described declaratively
in `scraper_config.json` as a **strategy**, and multiple sites can be listed
side by side — the scraper crawls all of them (or just one, via `--site`) in
a single run. Adding a new site is normally just adding a JSON entry, no
code changes.

A site definition has two parts:

1. **Discovery** — how to find individual project pages:
   - `"type": "sitemap"` — reads a `sitemap.xml`, keeps URLs under
     `category_paths`, drops any under a `skip_prefixes` prefix.
     Transparently follows sitemap *index* files (a "sitemap of sitemaps",
     common on WordPress sites) one level deep; set `child_sitemap_pattern`
     (a regex) to only follow child sitemaps whose URL matches it, e.g. to
     skip attachment/category/author sitemaps and keep only the ones that
     actually list posts.
   - `"type": "listing"` — crawls paginated listing pages starting at
     `start_paths`, collecting links matched by `project_link_pattern` and
     following `next_page_pattern` (if given) up to `max_pages`.
   - `"type": "search"` — for known torrent indexers (e.g. 1337x): queries
     `search_path_pattern` (with `{query}` substituted for the keyword given
     via `-k`/`--keyword` or an interactive prompt), collects result links
     matched by `result_link_pattern`, and paginates via `next_page_pattern`
     up to `max_pages`, same as `"listing"`. Sites of this type are skipped
     entirely if no keyword is given — there's nothing to search for.
2. **Extraction** — how to get a magnet link out of each project page:
   - **Primary strategy, always on, no config needed:** every fetched page is
     scanned for embedded `magnet:?...` links directly (HTML-entity-encoded
     ampersands like `&#038;` are decoded so the full link is captured). Most
     sites that embed magnets in the page body — this covers all of FitGirl
     Repacks, for example — need nothing more than discovery config.
   - **Fallback, only used if no magnets were found directly on the page:**
     if the site sets `download_link_pattern` (a regex whose first capture
     group is a candidate download href) and/or `redirect_pattern`, the
     scraper follows the old flow instead: extract a candidate link, follow
     an optional redirect page (matching `redirect_pattern` against e.g. a JS
     `window.location = "..."` redirect or meta-refresh) to the real file
     URL, then download the `.torrent` file and convert it to a magnet. Used
     by fosstorrents, which only exposes magnets after resolving a
     `/thankyou/` redirect to a `.torrent` file.
   - `magnet_pattern` (optional, rarely needed) — an extra site-specific
     regex checked alongside the built-in scan, for pages where the generic
     magnet regex doesn't match cleanly.

`require_torrent_suffix` (default `true`, only relevant to the fallback
strategy) filters out non-`.torrent` resolved links (e.g. direct
`.iso`/`.zip` downloads with no torrent).

`traverse_categories` (default `false`) makes discovery follow links, not
just list them. Category listing pages often nest — a top-level index (e.g.
`/distributions/`) links to subcategory pages (e.g.
`/distributions/arch-linux-distros/`), which link to the actual project
pages — and the sitemap doesn't always encode that structure explicitly.
When enabled: the bare category index page itself is included as a starting
point (normally skipped), and while crawling, every page's links that fall
under one of `category_paths` are followed too, however many levels deep,
until no new pages turn up. `-n`/`--limit` still caps the *total* number of
pages scanned when this is on, so testing stays cheap.

### Example: `scraper_config.json`

```json
{
  "output_file": "torrents.json",
  "delay": 1.5,
  "category": null,
  "limit": null,
  "site": null,
  "user_agent": "...",
  "sites": [
    {
      "name": "Fitgitl",
      "base_url": "https://fitgirl-repacks.site/",
      "discovery": {
        "type": "sitemap",
        "sitemap_path": "/sitemap_index.xml",
        "child_sitemap_pattern": "post-sitemap"
      }
    },
    {
      "name": "fosstorrents",
      "base_url": "https://fosstorrents.com",
      "discovery": {
        "type": "sitemap",
        "sitemap_path": "/sitemap.xml",
        "category_paths": ["/distributions/", "/games/", "/softwares/"],
        "skip_prefixes": ["/blog/", "/batches/", "/partnership/", "/donate", "/search/"]
      },
      "traverse_categories": true,
      "download_link_pattern": "href=\"(/thankyou/\\?[^\"]+)\"",
      "redirect_pattern": "window\\.location\\s*=\\s*\"([^\"]+)\"",
      "require_torrent_suffix": true
    },
    {
      "name": "example-listing-site",
      "base_url": "https://example.com",
      "discovery": {
        "type": "listing",
        "start_paths": ["/category/linux/"],
        "project_link_pattern": "href=\"(/torrent/[^\"]+)\"",
        "next_page_pattern": "href=\"([^\"]+)\">Next",
        "max_pages": 20
      }
    },
    {
      "name": "1337x",
      "base_url": "https://1337x.to",
      "discovery": {
        "type": "search",
        "search_path_pattern": "/search/{query}/1/",
        "result_link_pattern": "href=\"(/torrent/[0-9]+/[^\"]+)\"",
        "next_page_pattern": "href=\"([^\"]+)\"[^>]*>\\s*Next\\s*<",
        "max_pages": 3
      }
    }
  ]
}
```

Per-site fields fall back to the top-level `delay` / `user_agent` when not
set on the site itself. Edit this file to add sites, point at mirrors, tune
categories, or adjust delays/output. Any field left out falls back to a
built-in default.

## Usage

Run it with no arguments in a terminal (e.g. by double-clicking
`run_scraper.bat`) and it prompts you interactively:

```
Which site do you want to scrape?
  0. All sites
  1. fosstorrents
  2. Fitgitl
Select [default: 0]: 1

Which category do you want to scrape?
  0. All categories
  1. distributions
  2. games
  3. softwares
Select [default: 0]: 2

Enter a search keyword (e.g. "linux"): linux
```

The site prompt only appears when more than one site is configured; the
category prompt only appears when the selected site(s) define
`category_paths`; the keyword prompt only appears when a selected site is a
`"type": "search"` indexer (e.g. 1337x). Pressing Enter accepts the default
(crawl everything / skip search sites).

```
python scraper.py                          # interactive prompts, or crawls everything if not a terminal
python scraper.py --config other.json      # use a different config file
python scraper.py -s fosstorrents          # only crawl the site named "fosstorrents" (skips the site prompt)
python scraper.py -c distributions         # override: only project pages containing /distributions/ (skips the category prompt)
python scraper.py -n 5                     # override: only scan first 5 pages per site (testing)
python scraper.py -o my-list.json -d 2     # override: output file / request delay
python scraper.py -v                       # verbose: log every discovered/skipped link, redirect, etc.
python scraper.py --no-interactive         # never prompt, even in a terminal (for cron/automation)
python scraper.py -s 1337x -k linux        # search a known indexer for a keyword (skips the keyword prompt)
```

CLI flags (`-o`, `-d`, `-c`, `-n`, `-s`, `-k`) always take precedence over the
config file's values and skip the corresponding prompt, for quick one-off
overrides or non-interactive runs. Prompts are also skipped automatically
when stdin isn't a terminal (e.g. piped input, cron, CI).

By default the scraper logs one line per project page plus a per-site and
overall summary. Pass `-v`/`--verbose` for debug-level detail: sitemap/listing
discovery counts and filtering breakdowns, every candidate download link and
why it was kept or skipped, redirect resolution steps, and byte counts for
fetched pages/torrents.

Requests are sequential with a delay between them (default 1.5s) and a
descriptive User-Agent, to be a good citizen towards each site. Progress is
saved incrementally after every discovered torrent, so stopping with Ctrl+C
keeps everything found so far. Already-seen info hashes (from a previous run
in the output file) are skipped on the next run.

## Files

- `scraper.py` — generic, config-driven crawler / CLI entry point. Reads
  `scraper_config.json`, discovers project pages per site, extracts and
  resolves download links, and writes new magnet links incrementally.
- `torrent_utils.py` — generic fetch/parse helpers (fetch a URL, resolve a
  relative URL, follow a redirect regex, convert `.torrent` bytes to a
  magnet link, extract info hash/name from a magnet URI).
- `bencode.py` — pure-stdlib bencode encoder/decoder (used to parse `.torrent`
  files and compute info hashes).
- `scraper_config.json` — site definitions and default configuration.
