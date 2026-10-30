#!/usr/bin/env python3
"""Saves the dashboard's Yahoo Finance charts, economic events and news to one JSON file.

None of these sources can be read directly from a browser, so a GitHub Action
runs this on a schedule and index.html loads the result.

Usage: fetch_prices.py <output.json>
"""
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

# Tickers come from the ASSETS list in index.html, so that's the only place to edit them.
# Lines starting with // are skipped, which leaves out the examples in its comment.
PAGE = (Path(__file__).parent.parent / 'index.html').read_text()
SYMBOLS = re.findall(r"^\s*\{.*symbol:\s*'([^']+)'", PAGE, re.MULTILINE)

URL = 'https://query1.finance.yahoo.com/v8/finance/chart/{}?interval=1d&range=1y'
HISTORY_URL = 'https://query1.finance.yahoo.com/v8/finance/chart/{}?interval=15m&range=1mo'
DAY_SECONDS = 24 * 60 * 60
META_FIELDS = ['regularMarketPrice', 'previousClose', 'chartPreviousClose']

EVENTS_URL = 'https://nfs.faireconomy.media/ff_calendar_thisweek.json'
EVENT_FIELDS = ['title', 'date', 'impact', 'forecast', 'previous']
NEWS_FEEDS = {
    'Bloomberg': 'https://feeds.bloomberg.com/markets/news.rss',
    'CNBC': 'https://www.cnbc.com/id/100003114/device/rss/rss.html',
}
CREATOR_TAG = '{http://purl.org/dc/elements/1.1/}creator'
PUBLISHED_URL = 'https://raw.githubusercontent.com/musap0w3r-alt/Dashboard/data/prices.json'


def open_url(url):
    return urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20)


def read_json(url):
    with open_url(url) as response:
        return json.load(response)


def fetch_chart(symbol):
    result = read_json(URL.format(quote(symbol, safe='=')))['chart']['result'][0]

    # Same shape as Yahoo's response, with only the fields the page reads
    meta = {field: result['meta'][field] for field in META_FIELDS if field in result['meta']}
    close = result['indicators']['quote'][0]['close']
    return {'chart': {'result': [{
        'meta': meta,
        'timestamp': result['timestamp'],
        'indicators': {'quote': [{'close': close}]},
    }]}}


def fetch_history(symbol):
    result = read_json(HISTORY_URL.format(quote(symbol, safe='=')))['chart']['result'][0]
    bars = [
        (timestamp, close)
        for timestamp, close in zip(result['timestamp'], result['indicators']['quote'][0]['close'])
        if close is not None
    ]

    # Counted back from the last bar, so a closed market still shows its last session
    last = bars[-1][0]
    day = [close for timestamp, close in bars if timestamp >= last - DAY_SECONDS]
    week = [close for timestamp, close in bars if timestamp >= last - 7 * DAY_SECONDS]
    return {
        'day': [round(close, 4) for close in day],
        # Hourly is plenty for a week, stepping back from the end to keep the latest bar
        'week': [round(close, 4) for close in week[::-4][::-1]],
    }


def fetch_events():
    return [
        {field: event[field] for field in EVENT_FIELDS}
        for event in read_json(EVENTS_URL)
        if event['country'] == 'USD' and event['impact'] in ('High', 'Medium')
    ]


def fetch_news():
    articles = []
    for source, url in NEWS_FEEDS.items():
        try:
            with open_url(url) as response:
                items = ET.parse(response).getroot().iter('item')
                articles += [{
                    'title': item.findtext('title', 'Untitled').strip(),
                    'source': item.findtext(CREATOR_TAG) or source,
                    'url': item.findtext('link'),
                    'publishedAt': parsedate_to_datetime(item.findtext('pubDate')).isoformat(),
                } for item in items]
        except Exception as error:
            print(f'{source} feed failed: {error}', file=sys.stderr)
    if not articles:
        raise ValueError('No news feeds could be read')
    return articles


def load_or_keep(fetch, key):
    # On failure, fall back to the last published list
    try:
        return fetch()
    except Exception as error:
        print(f'{key} failed: {error}', file=sys.stderr)
    try:
        return read_json(PUBLISHED_URL).get(key, [])
    except Exception as error:
        print(f'No previous {key}: {error}', file=sys.stderr)
        return []


def main():
    charts = {}
    for symbol in SYMBOLS:
        try:
            charts[symbol] = fetch_chart(symbol)
            charts[symbol]['history'] = fetch_history(symbol)
        except Exception as error:
            print(f'{symbol} failed: {error}', file=sys.stderr)

    # Leave the last published file in place rather than replacing it with nothing
    if not charts:
        sys.exit('No symbols could be fetched')

    with open(sys.argv[1], 'w') as output:
        json.dump({
            'updatedAt': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'charts': charts,
            'events': load_or_keep(fetch_events, 'events'),
            'news': load_or_keep(fetch_news, 'news'),
        }, output, separators=(',', ':'))
    print(f'Saved {len(charts)} of {len(SYMBOLS)} symbols')


if __name__ == '__main__':
    main()
