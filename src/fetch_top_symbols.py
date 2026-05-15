#!/usr/bin/env python3
"""
Standalone script to fetch top capital symbols from online data source.
Generates universe_us_top300.yaml format file in config/ folder.
Usage: python fetch_top_symbols.py --source sp500 --top 300
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


def parse_company_data(html: str, top_n: int) -> list[dict[str, Any]]:
    """Parse HTML to extract company data from a SlickCharts table."""
    companies: list[dict[str, Any]] = []
    soup = BeautifulSoup(html, 'html.parser')
    table = soup.find('table')

    if not table:
        print('No table found on page')
        return companies

    rows = table.find_all('tr')[1:]
    print(f'Found {len(rows)} table rows')

    for row in rows[:top_n]:
        cols = [td.get_text(strip=True) for td in row.find_all('td')]
        if len(cols) < 3:
            continue

        try:
            rank = int(cols[0].replace('.', '').strip())
        except ValueError:
            continue

        ticker = cols[2]
        name = cols[1]

        if not ticker or not name:
            continue

        companies.append({
            'ticker': ticker,
            'name': name,
            'rank': rank,
            'country': 'USA',
        })

    print(f'Extracted {len(companies)} companies')
    return companies


def fetch_all_companies(source: str, total_companies: int = 300) -> list[dict[str, Any]]:
    """Fetch all companies from the selected source."""
    print(f"Fetching {SOURCE_CONFIG[source]['label']} companies from {SOURCE_CONFIG[source]['url']}...")
    try:
        html = fetch_page_data(source)
        companies = parse_company_data(html, total_companies)
        print(f"Successfully fetched {len(companies)} companies")
        return companies
    except Exception as e:
        print(f"Error fetching companies: {e}", file=sys.stderr)
        return []


def generate_yaml_content(companies: list[dict[str, Any]], source: str) -> str:
    """Generate YAML content in the same format as universe_us_top300.yaml."""
    from datetime import datetime

    metadata = {
        'source': f"SlickCharts {SOURCE_CONFIG[source]['label']}",
        'source_url': SOURCE_CONFIG[source]['url'],
        'as_of': datetime.now().strftime('%Y-%m-%d'),
        'description': 'Fixed editable U.S. stock universe file. Ranking is retained only as list order metadata.',
    }

    symbols = [
        {
            'ticker': company['ticker'],
            'name': company['name'],
            'rank': company['rank'],
            'country': company['country'],
        }
        for company in companies
    ]

    content = {
        'metadata': metadata,
        'symbols': symbols,
    }

    return yaml.dump(content, default_flow_style=False, allow_unicode=True, sort_keys=False)


def main() -> int:
    parser = argparse.ArgumentParser(description='Fetch top capital symbols from online data source')
    parser.add_argument(
        '--top',
        type=int,
        default=300,
        help='Number of top symbols to fetch (default: 300, max: 300)',
    )
    parser.add_argument(
        '--source',
        type=str,
        choices=list(SOURCE_CONFIG),
        default='sp500',
        help='Source to fetch from: sp500 or nasdaq (default: sp500)',
    )
    parser.add_argument(
        '--output',
        type=str,
        default=None,
        help='Output YAML file path. Default is config/universe_<source>_top300.yaml',
    )

    args = parser.parse_args()

    if args.output is None:
        args.output = f'config/universe_{args.source}_top{args.top}.yaml'

    if args.top < 1 or args.top > 300:
        print('Error: --top must be between 1 and 300', file=sys.stderr)
        return 1

    companies = fetch_all_companies(args.source, args.top)
    if len(companies) == 0:
        print('Error: No companies found', file=sys.stderr)
        return 1

    yaml_content = generate_yaml_content(companies, args.source)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(yaml_content)

    print(f'Generated YAML file: {output_path}')
    print(f'Total companies: {len(companies)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())