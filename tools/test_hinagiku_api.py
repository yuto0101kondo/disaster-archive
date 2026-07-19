#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ひなぎく (NDL東日本大震災アーカイブ) API 接続テスト

OpenSearch API を数件だけ叩き、ブロックされずに取得できるか確認する。
仕様: https://kn.ndl.go.jp/static/api?language=ja

実行:
  python3 tools/test_hinagiku_api.py [検索キーワード]
"""
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = 'disaster-archive-hinagiku-test/1.0 (contact: kondo20060101@gmail.com)'
ENDPOINT = 'https://kn.ndl.go.jp/api/opensearch'

NS = {
    'atom': 'http://www.w3.org/2005/Atom',
    'dc': 'http://purl.org/dc/elements/1.1/',
    'rss': '',
}


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, dict(r.headers), r.read()


def main():
    keyword = sys.argv[1] if len(sys.argv) > 1 else '気仙沼 津波'
    params = {'keyword': keyword, 'cnt': 3}
    url = ENDPOINT + '?' + urllib.parse.urlencode(params)
    print('URL:', url)
    try:
        status, headers, body = fetch(url)
    except Exception as e:
        print('接続失敗:', e)
        sys.exit(1)
    print('HTTP status:', status)
    print('Content-Type:', headers.get('Content-Type'))
    print('取得サイズ:', len(body), 'bytes')
    print('---')

    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        print('XMLパース失敗:', e)
        print(body[:500].decode('utf-8', 'replace'))
        sys.exit(1)

    # RSS 2.0 (channel/item) と Atom の両対応で軽くパース
    items = root.findall('.//item')
    if not items:
        items = root.findall('.//atom:entry', NS)
    print(f'ヒット item 数 (このページ): {len(items)}')
    total = root.find('.//{http://a9.com/-/spec/opensearch/1.1/}totalResults')
    if total is not None:
        print('総ヒット数:', total.text)
    print('---')
    for i, it in enumerate(items, 1):
        def get(tag):
            el = it.find(tag, NS)
            return (el.text or '').strip() if el is not None and el.text else ''
        title = get('title') or get('atom:title')
        link = get('link') or get('atom:id')
        print(f'[{i}] {title[:60]}')
        print(f'    link: {link[:90]}')
        for child in it:
            tag = child.tag.split('}')[-1]
            if tag in ('enclosure',):
                print(f'    enclosure: {child.attrib}')
            elif tag in ('thumbnail', 'content'):
                print(f'    {tag}: {(child.text or child.attrib)}')
        print()


if __name__ == '__main__':
    main()
