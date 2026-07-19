#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
poi-gated 採用のうち「住所タイトル」へのマッチを検証・修正する。

背景: ヒント無しPOI検索のGSI branchは、施設ではなく住所エントリ
(「北海道松前町小浜」等)にもマッチする。曖昧な地名(「小浜」「海岸」
「宮城」)が全国の同名住所に飛び、しかも building 精度が付いていた。

判定 (spot単位):
  1. マッチしたタイトルが施設語(館・港・公園等)を含む → 施設名が
     県名で始まるだけ(「宮城県慶長使節船ミュージアム」) → 維持
  2. 住所マッチ: タイトル中の市区町村名 または 都道府県名がレコードの
     テキスト(支援元表現を除去済み)に現れる → 位置は妥当として維持、
     ただし精度は area-centroid に訂正 (住所代表点であって施設ではない)
  3. 現れない → 曖昧な地名の誤マッチとみなし座標破棄 (uncertain)

実行:
  python3 tools/fix_address_matches.py --dry-run
  python3 tools/fix_address_matches.py
"""
import argparse
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'tools'))

from regeocode import (PREFS, LANDMARK_RE, MUNI_WITH_PREF_RE,  # noqa: E402
                       Geocoder)
from fix_outside_region import FROM_CLAUSE_RE  # noqa: E402
import unicodedata


def norm(s):
    s = unicodedata.normalize('NFKC', s or '')
    return re.sub(r'\s+', '', s)


def gsi_muni_variants(geo, query, name):
    """GSI検索結果のうち name を含むタイトルの (都道府県+市区町村) 集合。
    1つだけなら全国で一意な地名、複数なら曖昧。"""
    res = geo.gsi(query) or []
    munis = set()
    for cand in res:
        title = cand.get('properties', {}).get('title', '')
        if norm(name) not in norm(title):
            continue
        m = MUNI_WITH_PREF_RE.match(title)
        if m:
            munis.add(m.group(1))
    return munis

REPORT_IN = os.path.join(BASE, 'tools', 'rebuild_report.txt')
REPORT_OUT = os.path.join(BASE, 'tools', 'fix_address_report.txt')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    pat = re.compile(r'POI-GATED \[(\w+)\] "(.*)" \(\d+件\) -> '
                     r'\(([\d.]+),([\d.]+)\) poi-gated:(?:gsi|osm)-poi:(.*)')
    gated = {}
    with open(REPORT_IN, encoding='utf-8') as f:
        for line in f:
            m = pat.match(line)
            if m:
                gated[(m.group(1), m.group(2))] = (
                    float(m.group(3)), float(m.group(4)), m.group(5).strip())

    files = []
    groups = {}
    for n in range(1, 5):
        path = os.path.join(BASE, f'disaster_data_{n}.js')
        with open(path, encoding='utf-8') as f:
            txt = f.read()
        prefix = txt[:txt.index('[')]
        data = json.loads(txt[txt.index('['):].rstrip().rstrip(';'))
        files.append((path, prefix, data))
        for d in data:
            key = (d['dataset'], (d.get('spot') or '').strip())
            if key in gated and d.get('has_coord'):
                # poi-gated の結果座標のままのレコードだけが対象
                # (後続パスで動いたものは対象外)
                glat, glon, _ = gated[key]
                if abs(d['lat'] - glat) < 1e-4 and abs(d['lon'] - glon) < 1e-4:
                    groups.setdefault(key, []).append(d)

    stats = {'kept-facility': 0, 'kept-addr-downgraded': 0, 'dropped': 0}
    report = []
    geo = Geocoder()
    for (ds, spot), recs in groups.items():
        glat, glon, title = gated[(ds, spot)]
        # 「都道府県+市区町村」で始まるタイトルは住所マッチとみなす。
        # (「宮城県慶長使節船ミュージアム」のような施設名は市区町村名が
        #  続かないためここにマッチせず、施設として維持される。
        #  LANDMARK_RE での免除は「北海道松前町小浜」の「浜」に誤反応
        #  するため使わない)
        m = MUNI_WITH_PREF_RE.match(title)
        muni_full = m.group(1) if m else None            # 例: 北海道松前町
        if muni_full is None:
            stats['kept-facility'] += len(recs)
            continue
        muni_short = re.sub(r'^(?:' + '|'.join(PREFS) + r')', '', muni_full) if muni_full else None
        pref = next((p for p in PREFS if title.startswith(p)), None)
        text = FROM_CLAUSE_RE.sub(' ', ' '.join(
            f"{d.get('spot') or ''} {d.get('title') or ''} {d.get('desc') or ''}"
            for d in recs[:5]))
        muni_stem = re.sub(r'[市町村区]$', '', muni_short) if muni_short else None
        supported = ((muni_short and len(muni_short) >= 3 and muni_short in text)
                     or (muni_stem and len(muni_stem) >= 3 and muni_stem in text)
                     or (muni_full and muni_full in text)
                     or (pref and pref in text))
        if not supported:
            # spot が住所の大字名そのもの(自己一致)の場合、全国で一意なら妥当
            variants = gsi_muni_variants(geo, spot, spot)
            if len(variants) == 1:
                supported = True
        if supported:
            for d in recs:
                d['loc_precision'] = 'area-centroid'
            stats['kept-addr-downgraded'] += len(recs)
            report.append(f'KEEP-ADDR [{ds}] "{spot}" ({len(recs)}件) '
                          f'{title[:40]} -> area-centroid に訂正')
        else:
            for d in recs:
                d['has_coord'] = False
                d['lat'] = None
                d['lon'] = None
                d['loc_precision'] = 'uncertain'
            stats['dropped'] += len(recs)
            report.append(f'DROP [{ds}] "{spot}" ({len(recs)}件) '
                          f'誤マッチ疑い: {title[:40]}')

    print('集計:', stats)
    with open(REPORT_OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(report))
    if args.dry_run:
        print('(dry-run: データファイルは変更していません)')
        return
    for path, prefix, data in files:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(prefix + json.dumps(data, ensure_ascii=False,
                                        separators=(',', ':')) + ';')
        print('wrote', path)


if __name__ == '__main__':
    main()
