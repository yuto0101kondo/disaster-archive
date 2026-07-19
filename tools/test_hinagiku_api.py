#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ひなぎく (NDL東日本大震災アーカイブ) API 接続テスト

リニューアル後のひなぎく(Nuxt SPA)が内部で使う検索APIを数件だけ叩き、
ブラウザ外からブロックされずに取得できるか確認する。
エンドポイントは実際の検索画面のXHRから特定したもの:
  GET https://kn.ndl.go.jp/api/item/search-so/hina-cross

実行:
  python3 tools/test_hinagiku_api.py [検索キーワード]
"""
import json
import sys
import urllib.parse
import urllib.request

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
      'disaster-archive-hinagiku-test/1.0 (contact: kondo20060101@gmail.com)')
ENDPOINT = 'https://kn.ndl.go.jp/api/item/search-so/hina-cross'


def main():
    keyword = sys.argv[1] if len(sys.argv) > 1 else '気仙沼 津波'
    params = {'csid': 'hina-cross', 'keyword': keyword, 'from': 0, 'size': 3}
    url = ENDPOINT + '?' + urllib.parse.urlencode(params)
    print('URL:', url)
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Accept': 'application/json',
        'Referer': 'https://kn.ndl.go.jp/csearch/hina-cross',
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status = r.status
            ctype = r.headers.get('Content-Type')
            body = r.read()
    except Exception as e:
        print('接続失敗:', e)
        sys.exit(1)
    print('HTTP status:', status)
    print('Content-Type:', ctype)
    print('取得サイズ:', len(body), 'bytes')
    print('---')
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        print('JSONではないレスポンス。先頭500文字:')
        print(body[:500].decode('utf-8', 'replace'))
        sys.exit(1)

    # レスポンス構造は未知なのでトップレベル構造を表示してから item を探す
    if isinstance(data, dict):
        print('トップレベルキー:', list(data.keys()))
        hits = (data.get('hits') or {})
        total = hits.get('total') if isinstance(hits, dict) else None
        if total is not None:
            print('総ヒット数:', total)
        items = None
        for k in ('hits', 'items', 'results', 'list', 'docs'):
            v = data.get(k)
            if isinstance(v, list):
                items = v
                break
            if isinstance(v, dict) and isinstance(v.get('hits'), list):
                items = v['hits']
                break
        if items is None:
            print('item配列が見つからないため構造を表示:')
            print(json.dumps(data, ensure_ascii=False, indent=1)[:1500])
            return
        print(f'取得 item 数: {len(items)}')
        print('---')
        for i, it in enumerate(items, 1):
            src = it.get('_source', it) if isinstance(it, dict) else {}
            print(f'[{i}] keys: {list(src.keys())[:12]}')
            for key in ('title', 'dc_title', 'identifier', 'id', '_id',
                        'thumbnail', 'thumbnailUrl', 'permalink', 'landingPage'):
                val = src.get(key) or (it.get(key) if isinstance(it, dict) else None)
                if val:
                    print(f'    {key}: {str(val)[:90]}')
            print()
    else:
        print(json.dumps(data, ensure_ascii=False, indent=1)[:1500])


if __name__ == '__main__':
    main()
