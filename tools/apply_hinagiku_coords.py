#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ひなぎく原メタデータを disaster_data_*.js の yahoo レコードへ適用するCLI。

突合は ID完全一致のみ (yahoo_{N}_sr ↔ 元写真ID N)。曖昧検索は行わない。
更新内容:
  - lat/lon: ひなぎくの原座標 (GPS精度) を最優先で上書き
             loc_precision は 'building' (原典GPS) に設定
  - taken_at: 撮影日時 (ひなぎくを正とする / 新規フィールド)
  - title:    原文タイトル (現在のAI短縮タイトルを置き換え)
  - tags:     タグ配列 (風景/震災前 等 / 新規フィールド)
  - author:   投稿者 (新規フィールド)
  - provider: 提供元 (新規フィールド)
  - rights:   権利情報 (収集のみ / rights_type, rights_access)

実行例:
  python3 tools/apply_hinagiku_coords.py --dry-run
  python3 tools/apply_hinagiku_coords.py
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
JAPAN = (24.0, 46.0, 122.0, 146.0)


def valid_coord(lat, lon):
    return (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
            and JAPAN[0] <= lat <= JAPAN[1] and JAPAN[2] <= lon <= JAPAN[3])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--meta', default=DEFAULT_META)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    logger = setup_logger('apply')
    index, dup = build_photo_index(read_jsonl(args.meta))
    logger.info(f'ひなぎく索引: {len(index)}件 (photo_id重複 {dup}件)')

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
            pid = our_id_to_photo_id(d.get('id') or '')
            rec = index.get(pid) if pid else None
            if rec is None:
                stats['unmatched'] += 1
                continue
            stats['matched'] += 1

            lat, lon = rec.get('lat'), rec.get('lon')
            if valid_coord(lat, lon):
                had = bool(d.get('has_coord'))
                d['lat'], d['lon'] = round(lat, 7), round(lon, 7)
                d['has_coord'] = True
                d['loc_precision'] = 'building'
                stats['coords_updated'] += 1
                if not had:
                    stats['coords_added'] += 1
            if rec.get('taken_at'):
                if not d.get('taken_at'):
                    stats['taken_at_added'] += 1
                d['taken_at'] = rec['taken_at']
            if rec.get('title'):
                if (d.get('title') or '') != rec['title']:
                    stats['title_changed'] += 1
                d['title'] = rec['title']
            if rec.get('tags'):
                d['tags'] = rec['tags']
                stats['tags_set'] += 1
            if rec.get('author'):
                d['author'] = rec['author']
                stats['author_set'] += 1
            d['provider'] = rec.get('provider') or 'ヤフー株式会社'
            d['rights_type'] = rec.get('rights_type')
            d['rights_access'] = rec.get('rights_access')
            d['hinagiku_id'] = rec.get('hinagiku_id')

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
