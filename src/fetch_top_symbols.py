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
import re
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


def parse_total_market_cap_b(html: str, source: str) -> float | None:
    """
    Extract the total index market cap from the page text and return it in
    billions USD.  Looks for patterns like "is $68.50T" or "is $1,234B".
    Returns None if the figure cannot be found.
    """
    text = BeautifulSoup(html, 'html.parser').get_text()
    # Match the figure that follows "total market cap" on the same line
    match = re.search(
        r'total market cap[^\n$]*\$(([\d,]+\.?\d*))\s*([TB])\b',
        text,
        re.IGNORECASE,
    )
    if match:
        value = float(match.group(1).replace(',', ''))
        suffix = match.group(3).upper()
        total_b = value * 1_000 if suffix == 'T' else value
        print(f'[{source}] Total index market cap: ${total_b:,.0f}B')
        return total_b
    print(f'[{source}] Could not parse total market cap from page', file=sys.stderr)
    return None


def parse_company_data(
    html: str,
    top_n: int,
    source: str,
    total_cap_b: float | None,
) -> list[dict[str, Any]]:
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

        # cols[3] is either a direct market cap ("$5.07T" — Nasdaq) or
        # an index-weight percentage ("7.40%" — S&P 500).  Parse both.
        market_cap_b: float | None = None
        if len(cols) >= 4:
            col3 = cols[3].strip()
            if col3.startswith('$'):
                # Direct market cap value from the Nasdaq table
                m = re.match(r'\$([\d,]+\.?\d*)\s*([TBM])', col3, re.IGNORECASE)
                if m:
                    value = float(m.group(1).replace(',', ''))
                    suffix = m.group(2).upper()
                    multipliers = {'T': 1_000, 'B': 1, 'M': 0.001}
                    market_cap_b = round(value * multipliers[suffix], 2)
            elif col3.endswith('%') and total_cap_b is not None:
                # Weight percentage from the S&P 500 table
                try:
                    weight_pct = float(col3.replace('%', '').replace(',', ''))
                    market_cap_b = round(total_cap_b * weight_pct / 100, 2)
                except ValueError:
                    pass

        companies.append({
            'ticker': ticker,
            'name': name,
            'rank': rank,
            'market_cap_b': market_cap_b,
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
        total_cap_b = parse_total_market_cap_b(html, source)
        return parse_company_data(html, top_n, source, total_cap_b)
    except Exception as exc:
        print(f'[{source}] Error: {exc}', file=sys.stderr)
        return []


def merge_companies(
    sp500: list[dict[str, Any]],
    nasdaq: list[dict[str, Any]],
    final_top_n: int,
) -> list[dict[str, Any]]:
    """
    Merge S&P 500 and Nasdaq lists into a single top-N ranking by market cap.

    Both source lists are already sorted by market cap within their index.
    For stocks present in both lists the best (lowest) rank from either source
    is used as the combined market-cap proxy, so they naturally float to the
    top.  After sorting by combined rank the list is sliced to final_top_n.
    """
    sp500_by_ticker: dict[str, dict[str, Any]] = {
        c['ticker'].upper(): c for c in sp500
    }
    nasdaq_by_ticker: dict[str, dict[str, Any]] = {
        c['ticker'].upper(): c for c in nasdaq
    }

    all_tickers = set(sp500_by_ticker) | set(nasdaq_by_ticker)
    merged: list[dict[str, Any]] = []

    for ticker in all_tickers:
        sp = sp500_by_ticker.get(ticker)
        nq = nasdaq_by_ticker.get(ticker)

        if sp and nq:
            # Use the Nasdaq direct market cap when available (parsed from "$X.XT"
            # column) as it is more precise than S&P 500 weight-based estimate.
            market_cap_b = nq.get('market_cap_b') or sp.get('market_cap_b')
            merged.append({**sp, 'market_cap_b': market_cap_b, 'source': 'S&P 500 + Nasdaq'})
        elif sp:
            merged.append({**sp, 'source': 'S&P 500'})
        else:
            assert nq is not None
            merged.append({**nq, 'source': 'Nasdaq'})

    # Sort by real market cap descending; fall back to source rank (ascending)
    # for any entries where market cap could not be computed.
    merged.sort(
        key=lambda c: (
            c.get('market_cap_b') is None,   # None values sink to the bottom
            -(c.get('market_cap_b') or 0),
        )
    )
    merged = merged[:final_top_n]

    # Assign sequential rank 1…N based on the sorted market-cap order
    for idx, company in enumerate(merged, start=1):
        company['rank'] = idx

    return merged


def generate_yaml_content(companies: list[dict[str, Any]], final_top_n: int) -> str:
    """Generate YAML content."""
    from datetime import datetime

    metadata = {
        'source': 'SlickCharts S&P 500 + Nasdaq (merged, sorted by real market cap)',
        'source_url': f"{SOURCE_CONFIG['sp500']['url']} + {SOURCE_CONFIG['nasdaq']['url']}",
        'as_of': datetime.now().strftime('%Y-%m-%d'),
        'total_symbols': len(companies),
        'description': (
            f'Top {final_top_n} market-cap stocks combined from S&P 500 and Nasdaq. '
            'Market cap (USD billions) is computed from each index weight % × total index market cap. '
            'S&P 500 members use S&P 500 market cap; Nasdaq-only members use Nasdaq market cap. '
            'Update monthly with: python src/fetch_top_symbols.py'
        ),
    }

    symbols = [
        {
            'ticker': company['ticker'],
            'name': company['name'],
            'rank': company['rank'],
            'market_cap_b': company.get('market_cap_b'),  # USD billions, None if unavailable
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
        help=f'Final number of symbols to keep (default: {DEFAULT_TOP_N})',
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

    # Fetch 2× the target from each source so we have enough candidates after
    # deduplication to fill the requested final count.
    fetch_per_source = max(args.top * 2, args.top + 100)
    sp500_companies = fetch_source('sp500', fetch_per_source)
    nasdaq_companies = fetch_source('nasdaq', fetch_per_source)

    if not sp500_companies and not nasdaq_companies:
        print('Error: no companies fetched from either source', file=sys.stderr)
        return 1

    merged = merge_companies(sp500_companies, nasdaq_companies, args.top)
    sp_only = sum(1 for c in merged if c['source'] == 'S&P 500')
    nq_only = sum(1 for c in merged if c['source'] == 'Nasdaq')
    both   = sum(1 for c in merged if c['source'] == 'S&P 500 + Nasdaq')
    print(f'\nFinal universe: {len(merged)} symbols '
          f'(S&P 500 only: {sp_only}, Nasdaq only: {nq_only}, both: {both})')

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
