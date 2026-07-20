#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""yahoo レコードの画像URL・リンクをひなぎく由来の値へ一括置換するCLI。

方針 (画像のローカル保存は行わない):
  - image_thumb: ひなぎくの thumbnailUrl (_tn / 約5KB)。一覧・クラスタ表示が軽くなる
  - image_full:  ひなぎくのスクリーン画像 (_sr)。ポップアップ表示用
  - link:        Yahooの生画像URL → ひなぎく項目ページ (恒久パーマリンク)
                 https://kn.ndl.go.jp/item/{hinagiku_id}

注意: _tn/_sr のホストはYahooストレージのままである (ひなぎくは画像本体を
ホストしていない)。Yahoo閉鎖後は画像は表示されなくなるが、link 先の
ひなぎく項目ページ (メタデータ) はNDLに恒久保存される。

実行:
  python3 tools/rewrite_image_urls.py --dry-run
  python3 tools/rewrite_image_urls.py
"""
import argparse
import json
import os
import sys
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'src'))

from hinagiku.mapping import build_photo_index, our_id_to_photo_id  # noqa: E402
from hinagiku.utils import setup_logger, read_jsonl  # noqa: E402

DEFAULT_META = os.path.join(BASE, 'raw', 'hinagiku',
                            'yahoo_shinsai_normalized.jsonl.gz')
ITEM_PAGE = 'https://kn.ndl.go.jp/item/{hid}'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--meta', default=DEFAULT_META)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    logger = setup_logger('rewrite')
    index, _ = build_photo_index(read_jsonl(args.meta))

    stats = Counter()
    files = []
    for n in range(1, 5):
        path = os.path.join(BASE, f'disaster_data_{n}.js')
        with open(path, encoding='utf-8') as f:
            txt = f.read()
        prefix = txt[:txt.index('[')]
        data = json.loads(txt[txt.index('['):].rstrip().rstrip(';'))
        files.append((path, prefix, data))
        for d in data:
            if d.get('dataset') != 'yahoo':
                continue
            stats['yahoo_records'] += 1
            rec = index.get(our_id_to_photo_id(d.get('id') or '') or '')
            if rec is None:
                stats['unmatched'] += 1
                continue
            if rec.get('url_thumbnail') and d.get('image_thumb') != rec['url_thumbnail']:
                d['image_thumb'] = rec['url_thumbnail']
                stats['thumb_updated'] += 1
            if rec.get('url_screen') and d.get('image_full') != rec['url_screen']:
                d['image_full'] = rec['url_screen']
                stats['full_updated'] += 1
            hid = rec.get('hinagiku_id')
            if hid:
                page = ITEM_PAGE.format(hid=hid)
                if d.get('link') != page:
                    d['link'] = page
                    stats['link_updated'] += 1

    logger.info('集計: ' + json.dumps(dict(stats), ensure_ascii=False))
    if args.dry_run:
        logger.info('(dry-run: データファイルは変更していません)')
        return
    for path, prefix, data in files:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(prefix + json.dumps(data, ensure_ascii=False,
                                        separators=(',', ':')) + ';')
        logger.info(f'wrote {path}')


if __name__ == '__main__':
    main()
