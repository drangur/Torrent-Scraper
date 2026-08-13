# Torrent-Scraper

A standalone scraper that crawls [fosstorrents.com](https://fosstorrents.com)
— a legitimate site that tracks/seeds official releases of FOSS software,
games and Linux/BSD distributions — and converts each project's `.torrent`
download into a magnet link (by downloading the `.torrent` file and computing
its info hash), appending new ones to an output file (`torrents.txt` by
default). No file data is ever downloaded — only the small `.torrent`
metadata files.

The resulting `torrents.txt` can be fed into a separate monitoring tool (e.g.
[P2P Monitor](https://github.com/)) to track seeder/leecher counts.

Requires Python 3.8+. No third-party dependencies — pure standard library.

## Configuration

Site settings live in `scraper_config.json` (next to the script):

```json
{
  "base_url": "https://fosstorrents.com",
  "sitemap_path": "/sitemap.xml",
  "category_paths": ["/distributions/", "/games/", "/softwares/"],
  "skip_prefixes": ["/blog/", "/batches/", "/partnership/", "/donate", "/search/"],
  "output_file": "torrents.txt",
  "delay": 1.5,
  "category": null,
  "limit": null,
  "user_agent": "..."
}
```

Edit this file to point at a different `base_url` (e.g. a mirror), change the
categories crawled, or adjust the request delay/output file. Any field left
out falls back to a built-in default.

## Usage

```
python scrape_fosstorrents.py                        # uses scraper_config.json as-is
python scrape_fosstorrents.py --config other.json     # use a different config file
python scrape_fosstorrents.py -c distributions        # override: only Distributions category
python scrape_fosstorrents.py -n 5                     # override: only scan first 5 pages (testing)
python scrape_fosstorrents.py -o my-list.txt -d 2      # override: output file / request delay
```

CLI flags (`-o`, `-d`, `-c`, `-n`) always take precedence over the config
file's values, for quick one-off overrides.

Requests are sequential with a delay between them (default 1.5s) and a
descriptive User-Agent, to be a good citizen towards the site. Progress is
appended incrementally, so stopping with Ctrl+C keeps everything found so far.
Already-seen info hashes (from a previous run in the output file) are skipped
on the next run.

## Files

- `scrape_fosstorrents.py` — sitemap crawler / CLI entry point.
- `fosstorrents.py` — fetch/parse helpers (fetch page, resolve `.torrent` URL,
  convert `.torrent` to a magnet link).
- `bencode.py` — pure-stdlib bencode encoder/decoder (used to parse `.torrent`
  files and compute info hashes).
- `scraper_config.json` — default configuration.
