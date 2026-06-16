#!/usr/bin/env python3
"""
Fetch top market-cap stocks from S&P 500 and Nasdaq, merge them (deduplicating
by ticker), and write a combined YAML universe file.  Optionally patches
settings.yaml so that universe.static_file points to the new file.

Usage:
    python fetch_top_symbols.py                        # top 200 from each, combined
    python fetch_top_symbols.py --top 300              # top 300 from each source
    python fetch_top_symbols.py --output config/my_universe.yaml
    python fetch_top_symbols.py --no-update-config     # skip patching settings.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import requests
import yaml
from bs4 import BeautifulSoup

SOURCE_CONFIG = {
    'sp500': {
        'label': 'S&P 500',
        'url': 'https://www.slickcharts.com/sp500',
    },
    'nasdaq': {
        'label': 'Nasdaq',
        'url': 'https://www.slickcharts.com/nasdaq',
    },
}

DEFAULT_TOP_N = 200
DEFAULT_OUTPUT = 'config/universe_combined_top{n}.yaml'
SETTINGS_FILE = 'config/settings.yaml'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.google.com/',
}


def fetch_page_data(source: str) -> str:
    """Fetch HTML content for the requested source."""
    url = SOURCE_CONFIG[source]['url']
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def parse_company_data(html: str, top_n: int, source: str) -> list[dict[str, Any]]:
    """Parse HTML to extract company data from a SlickCharts table."""
    companies: list[dict[str, Any]] = []
    soup = BeautifulSoup(html, 'html.parser')
    table = soup.find('table')

    if not table:
        print(f'[{source}] No table found on page', file=sys.stderr)
        return companies

    rows = table.find_all('tr')[1:]
    print(f'[{source}] Found {len(rows)} table rows, parsing top {top_n}')

    for row in rows[:top_n]:
        cols = [td.get_text(strip=True) for td in row.find_all('td')]
        if len(cols) < 3:
            continue
        try:
            rank = int(cols[0].replace('.', '').strip())
        except ValueError:
            continue
        ticker = cols[2].strip()
        name = cols[1].strip()
        if not ticker or not name:
            continue
        companies.append({
            'ticker': ticker,
            'name': name,
            'rank': rank,
            'source': SOURCE_CONFIG[source]['label'],
            'country': 'USA',
        })

    print(f'[{source}] Extracted {len(companies)} companies')
    return companies


def fetch_source(source: str, top_n: int) -> list[dict[str, Any]]:
    """Fetch and parse one source; returns empty list on error."""
    print(f"Fetching {SOURCE_CONFIG[source]['label']} top {top_n} from {SOURCE_CONFIG[source]['url']}...")
    try:
        html = fetch_page_data(source)
        return parse_company_data(html, top_n, source)
    except Exception as exc:
        print(f'[{source}] Error: {exc}', file=sys.stderr)
        return []


def merge_companies(
    sp500: list[dict[str, Any]],
    nasdaq: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Merge two source lists, deduplicating by ticker.  The first occurrence
    (S&P 500 takes priority) is kept; duplicates from Nasdaq are annotated.
    The result is sorted by S&P 500 rank first, then Nasdaq rank.
    """
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []

    for company in sp500:
        ticker = company['ticker'].upper()
        if ticker not in seen:
            seen.add(ticker)
            merged.append({**company, 'source': 'S&P 500'})

    for company in nasdaq:
        ticker = company['ticker'].upper()
        if ticker not in seen:
            seen.add(ticker)
            # Offset rank so Nasdaq-only names sort after S&P 500 names
            merged.append({**company, 'rank': company['rank'] + 10000, 'source': 'Nasdaq'})

    merged.sort(key=lambda c: c['rank'])
    # Re-number sequentially
    for idx, company in enumerate(merged, start=1):
        company['rank'] = idx

    return merged


def generate_yaml_content(companies: list[dict[str, Any]], top_n: int) -> str:
    """Generate YAML content."""
    from datetime import datetime

    metadata = {
        'source': 'SlickCharts S&P 500 + Nasdaq (merged, deduplicated by ticker)',
        'source_url': f"{SOURCE_CONFIG['sp500']['url']} + {SOURCE_CONFIG['nasdaq']['url']}",
        'as_of': datetime.now().strftime('%Y-%m-%d'),
        'top_n_per_source': top_n,
        'total_symbols': len(companies),
        'description': (
            'Combined universe: top market-cap stocks from S&P 500 and Nasdaq. '
            'Deduplicated by ticker; S&P 500 entries take priority. '
            'Update monthly with: python src/fetch_top_symbols.py'
        ),
    }

    symbols = [
        {
            'ticker': company['ticker'],
            'name': company['name'],
            'rank': company['rank'],
            'source': company['source'],
            'country': company['country'],
        }
        for company in companies
    ]

    return yaml.dump(
        {'metadata': metadata, 'symbols': symbols},
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )


def patch_settings_yaml(settings_path: Path, new_static_file: str) -> None:
    """
    Update universe.static_file in settings.yaml to point to the newly
    generated file.  Removes universe.top_n if present.
    """
    if not settings_path.exists():
        print(f'settings.yaml not found at {settings_path}, skipping patch', file=sys.stderr)
        return

    with settings_path.open('r', encoding='utf-8') as fh:
        raw = yaml.safe_load(fh) or {}

    universe = raw.setdefault('universe', {})
    universe['static_file'] = new_static_file
    universe.pop('top_n', None)  # no longer needed

    with settings_path.open('w', encoding='utf-8') as fh:
        yaml.dump(raw, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)

    print(f'Updated {settings_path}: universe.static_file = {new_static_file}')


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Fetch top 200 market-cap stocks from S&P 500 + Nasdaq, merge, and write universe YAML',
    )
    parser.add_argument(
        '--top',
        type=int,
        default=DEFAULT_TOP_N,
        help=f'Number of top symbols to fetch per source (default: {DEFAULT_TOP_N})',
    )
    parser.add_argument(
        '--output',
        type=str,
        default=None,
        help='Output YAML file path (default: config/universe_combined_top<N>.yaml)',
    )
    parser.add_argument(
        '--no-update-config',
        action='store_true',
        default=False,
        help='Skip patching settings.yaml after writing the universe file',
    )

    args = parser.parse_args()

    if args.top < 1:
        print('Error: --top must be at least 1', file=sys.stderr)
        return 1

    output_path_str = args.output or DEFAULT_OUTPUT.format(n=args.top)
    output_path = Path(output_path_str)

    sp500_companies = fetch_source('sp500', args.top)
    nasdaq_companies = fetch_source('nasdaq', args.top)

    if not sp500_companies and not nasdaq_companies:
        print('Error: no companies fetched from either source', file=sys.stderr)
        return 1

    merged = merge_companies(sp500_companies, nasdaq_companies)
    print(f'\nMerged result: {len(merged)} unique symbols '
          f'({len(sp500_companies)} S&P 500 + {len(nasdaq_companies)} Nasdaq, deduplicated)')

    yaml_content = generate_yaml_content(merged, args.top)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml_content, encoding='utf-8')
    print(f'Written: {output_path}')

    if not args.no_update_config:
        # Resolve relative to project root (two levels above src/)
        project_root = Path(__file__).resolve().parent.parent
        settings_path = project_root / SETTINGS_FILE
        # Store path relative to project root so it matches existing convention
        try:
            relative = output_path.resolve().relative_to(project_root)
            config_value = str(relative).replace('\\', '/')
        except ValueError:
            config_value = str(output_path)
        patch_settings_yaml(settings_path, config_value)

    return 0


if __name__ == '__main__':
    sys.exit(main())
