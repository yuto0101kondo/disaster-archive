#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ひなぎく突合の検証CLI。Markdownレポートを生成する。

集計項目: 一致率 / 不一致ID / 座標追加・変更 / 撮影日追加 / タイトル差分 /
provider差分 / tag差分。

実行例:
  python3 tools/verify_hinagiku_mapping.py
  python3 tools/verify_hinagiku_mapping.py --out docs/hinagiku_mapping_report.md
"""
import argparse
import json
import math
import os
import sys
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'src'))

from hinagiku.mapping import build_photo_index, our_id_to_photo_id  # noqa: E402
from hinagiku.utils import read_jsonl  # noqa: E402

DEFAULT_META = os.path.join(BASE, 'raw', 'hinagiku',
                            'yahoo_shinsai_normalized.jsonl.gz')


def dist_km(a, b, c, d):
    R = 6371
    la1, lo1, la2, lo2 = map(math.radians, (a, b, c, d))
    return R * math.acos(min(1, math.sin(la1) * math.sin(la2) +
                             math.cos(la1) * math.cos(la2) * math.cos(lo1 - lo2)))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--meta', default=DEFAULT_META)
    ap.add_argument('--out', default=os.path.join(BASE, 'docs',
                                                  'hinagiku_mapping_report.md'))
    args = ap.parse_args()

    index, dup = build_photo_index(read_jsonl(args.meta))

    ours = []
    for n in range(1, 5):
        with open(os.path.join(BASE, f'disaster_data_{n}.js'), encoding='utf-8') as f:
            txt = f.read()
        ours += [d for d in json.loads(txt[txt.index('['):].rstrip().rstrip(';'))
                 if d.get('dataset') == 'yahoo']

    n_ours = len(ours)
    matched = unmatched = 0
    unmatched_ids = []
    coord_have_hina = coord_added = 0
    move_dist = []
    taken_added = 0
    title_same = title_diff = 0
    provider_dist = Counter()
    tag_dist = Counter()

    for d in ours:
        pid = our_id_to_photo_id(d.get('id') or '')
        rec = index.get(pid) if pid else None
        if rec is None:
            unmatched += 1
            if len(unmatched_ids) < 30:
                unmatched_ids.append(d.get('id'))
            continue
        matched += 1
        lat, lon = rec.get('lat'), rec.get('lon')
        if lat is not None and lon is not None:
            coord_have_hina += 1
            if not d.get('has_coord'):
                coord_added += 1
            elif d.get('lat') is not None:
                move_dist.append(dist_km(d['lat'], d['lon'], lat, lon))
        if rec.get('taken_at') and not d.get('taken_at'):
            taken_added += 1
        if (d.get('title') or '').strip() == (rec.get('title') or '').strip():
            title_same += 1
        else:
            title_diff += 1
        provider_dist[rec.get('provider') or '?'] += 1
        for t in rec.get('tags') or []:
            tag_dist[t] += 1

    move_dist.sort()

    def pct(x, base):
        return f'{x/base:.1%}' if base else '-'

    med = f'{move_dist[len(move_dist)//2]:.2f}km' if move_dist else '-'
    p90 = f'{move_dist[int(len(move_dist)*0.9)]:.2f}km' if move_dist else '-'
    over2 = sum(1 for x in move_dist if x > 2)

    lines = [
        '# ひなぎく突合 検証レポート', '',
        f'- ひなぎく yahoo_shinsai 収穫件数: **{len(index)}** (photo_id重複 {dup})',
        f'- 当アーカイブ yahoo レコード: **{n_ours}**', '',
        '## 一致率', '',
        f'- ID完全一致: **{matched}件 ({pct(matched, n_ours)})**',
        f'- 不一致: {unmatched}件 ({pct(unmatched, n_ours)})',
        f'- 不一致ID例 (先頭30件): {", ".join(unmatched_ids) or "なし"}', '',
        '## 座標', '',
        f'- ひなぎく側に座標あり: {coord_have_hina}件 ({pct(coord_have_hina, matched)})',
        f'- 座標が新規に付与されるレコード: **{coord_added}件**',
        f'- 既存座標からの移動距離: 中央値 {med} / 90%ile {p90} / 2km超 {over2}件',
        '  (移動距離が大きい = 従来のテキスト由来座標が粗かったことを意味する)', '',
        '## 撮影日時', '',
        f'- 撮影日時が新規付与されるレコード: **{taken_added}件**', '',
        '## タイトル', '',
        f'- 完全一致: {title_same}件 / 差分あり: {title_diff}件',
        '  (当アーカイブ側は生成時に短縮・加工されているため差分が正常)', '',
        '## provider 分布', '',
    ]
    for k, v in provider_dist.most_common(5):
        lines.append(f'- {k}: {v}件')
    lines += ['', '## tag 分布', '']
    for k, v in tag_dist.most_common(10):
        lines.append(f'- {k}: {v}件')
    lines.append('')

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print('\n'.join(lines))
    print('wrote', args.out)


if __name__ == '__main__':
    main()
