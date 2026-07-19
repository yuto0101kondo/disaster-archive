#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
地区代表点どまりの施設名スポットをPOI実位置へ格上げする一括パス。

背景: rebuild の poi_search は「施設名+市町村名」の複合クエリを投げていたが、
Nominatim は複合クエリに弱く、GSI のPOI結果(タイトルが施設名のみ)は
市町村名の文字列照合で全て弾かれていた。結果、「花山青少年自然の家」の
ような固有施設名の多く(約5,900 spot / 9,800レコード)が市区町村・town
代表点(area-centroid)どまりになっていた。

改善: 施設名単体でクエリし、市町村の整合は文字列でなく地理で検証する。
現在の座標(=その市区町村の代表点)から30km以内の候補のみ採用するため、
同名他所への誤マッチは排除される。

対象: loc_precision='area-centroid' かつ spot が施設名(classify=building)
採用時: loc_precision='building' に格上げ

実行:
  python3 tools/upgrade_poi_pass.py --dry-run --limit 100
  python3 tools/upgrade_poi_pass.py
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'tools'))

from regeocode import (Geocoder, classify, build_query_text,  # noqa: E402
                       NOMINATIM_POI_CLASSES, in_japan)
from snap_to_town_centroid import dist_km  # noqa: E402

REPORT_PATH = os.path.join(BASE, 'tools', 'upgrade_poi_report.txt')
GATE_KM = 30.0


def norm(s):
    s = unicodedata.normalize('NFKC', s or '')
    s = re.sub(r'\s+', '', s)
    return s.replace('ヶ', 'ケ').replace('が', 'ケ').replace('ガ', 'ケ')


def find_poi(geo, query, anchor_lat, anchor_lon):
    """施設名単体クエリ + 30km地理ゲートでPOIを探す。
    戻り値: (lat, lon, source, note) または None"""
    nq = norm(query)

    res = geo.nominatim(query)
    if res:
        for cand in res:
            if cand.get('class') not in NOMINATIM_POI_CLASSES:
                continue
            dn = cand.get('display_name', '')
            if nq not in norm(dn) and nq != norm(cand.get('name', '')):
                continue
            lat, lon = float(cand['lat']), float(cand['lon'])
            if not in_japan(lat, lon):
                continue
            if dist_km(lat, lon, anchor_lat, anchor_lon) > GATE_KM:
                continue
            return lat, lon, 'osm-poi', dn[:60]

    res = geo.gsi(query)
    if res:
        for cand in res:
            title = cand.get('properties', {}).get('title', '')
            coords = cand.get('geometry', {}).get('coordinates')
            if not coords or nq not in norm(title):
                continue
            lat, lon = coords[1], coords[0]
            if not in_japan(lat, lon):
                continue
            if dist_km(lat, lon, anchor_lat, anchor_lon) > GATE_KM:
                continue
            return lat, lon, 'gsi-poi', title[:60]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--checkpoint-every', type=int, default=500)
    args = ap.parse_args()

    geo = Geocoder()
    files = []
    groups = defaultdict(list)
    for n in range(1, 5):
        path = os.path.join(BASE, f'disaster_data_{n}.js')
        with open(path, encoding='utf-8') as f:
            txt = f.read()
        prefix = txt[:txt.index('[')]
        data = json.loads(txt[txt.index('['):].rstrip().rstrip(';'))
        files.append((path, prefix, data))
        for d in data:
            if d.get('loc_precision') == 'area-centroid' and d.get('has_coord'):
                spot = (d.get('spot') or '').strip()
                if spot and classify(spot) == 'building':
                    groups[(d['dataset'], spot)].append(d)

    items = list(groups.items())
    if args.limit:
        items = items[:args.limit]
    print(f'対象: {len(items)} ユニークspot / '
          f'{sum(len(recs) for _, recs in items)} レコード')

    n_up = n_miss = 0
    report = []

    def write_all():
        geo.save_cache()
        for path, prefix, data in files:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(prefix + json.dumps(data, ensure_ascii=False,
                                            separators=(',', ':')) + ';')
        with open(REPORT_PATH, 'w', encoding='utf-8') as f:
            f.write('\n'.join(report))

    for i, ((ds, spot), recs) in enumerate(items):
        anchor = (recs[0]['lat'], recs[0]['lon'])
        r = find_poi(geo, build_query_text(spot), anchor[0], anchor[1])
        if r:
            lat, lon, src, note = r
            for d in recs:
                d['lat'], d['lon'] = round(lat, 6), round(lon, 6)
                d['loc_precision'] = 'building'
            n_up += len(recs)
            report.append(f'UP [{ds}] "{spot}" ({len(recs)}件) '
                          f'{anchor} -> ({lat:.6f},{lon:.6f}) {src} "{note}"')
        else:
            n_miss += len(recs)
            report.append(f'MISS [{ds}] "{spot}" ({len(recs)}件)')

        if (i + 1) % 100 == 0:
            print(f'... {i+1}/{len(items)} spots (API:{geo.n_calls} '
                  f'cache:{geo.n_cache_hits}) 格上げ:{n_up} 未発見:{n_miss}',
                  flush=True)
        if not args.dry_run and (i + 1) % args.checkpoint_every == 0:
            write_all()
            print(f'--- checkpoint {i+1}/{len(items)} ---', flush=True)

    print(f'\n最終: POI格上げ {n_up}件, 見つからず(代表点のまま) {n_miss}件')
    if args.dry_run:
        geo.save_cache()
        with open(REPORT_PATH, 'w', encoding='utf-8') as f:
            f.write('\n'.join(report))
        print('(dry-run: データファイルは変更していません)')
        return
    write_all()
    for path, _, _ in files:
        print('wrote', path)


if __name__ == '__main__':
    main()
