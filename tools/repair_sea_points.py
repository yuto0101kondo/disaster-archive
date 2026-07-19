#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
find_sea_points.py が検出した海上ピンを陸地へ引き戻す。

修正の優先順位 (レコードごと):
  0. spot 名の POI 検索 (Nominatim/GSI) がヒットし、かつ結果が元の海上
     座標から30km以内
       → その地物の実座標へ (loc_precision='building')
         ※30kmゲートが同名他所への誤マッチを排除する(誤マッチは通常
           他県に飛ぶ)。元座標は「正しい地域の沖合」に落ちていることが
           多く、真のPOIは必ず近傍にある。
  1. spot から市区町村+大字・町丁目が特定できる
       → Geolonia 辞書の町丁目代表点へスナップ (loc_precision='area-centroid')
  2. spot から市区町村のみ特定できる
       → GSI住所検索 (regeocode のキャッシュ利用) の市区町村代表点へ
         スナップ (loc_precision='area-centroid')
  3. どちらも不可だが最寄り陸地(5km以内)が見つかっている
       → その陸地点へ移動 (loc_precision='uncertain'; 地図ではデフォルト非表示)
  4. 5km探索でも陸地なし (完全な誤座標)
       → has_coord=False, loc_precision='uncertain' として地図から除外

対象は nearest_land_km > --min-dist (既定0.3km) の座標。300m以内に陸地が
ある海際ピンは埠頭・港湾施設等の正当な位置でありうるため既定では触らない。

実行:
  python3 tools/repair_sea_points.py --dry-run
  python3 tools/repair_sea_points.py
"""
import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'tools'))

from snap_to_town_centroid import Gazetteer, dist_km  # noqa: E402
from regeocode import (Geocoder, extract_muni_with_pref,  # noqa: E402
                       extract_muni_loose, build_query_text)

POI_GATE_KM = 30.0  # POI検索結果が元座標からこの距離を超える場合は不採用

REPORT_IN = os.path.join(BASE, 'tools', 'sea_points_report.json')
REPORT_OUT = os.path.join(BASE, 'tools', 'sea_repair_report.txt')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--min-dist', type=float, default=0.3,
                    help='この距離(km)以内に陸地がある海際ピンは修正しない')
    ap.add_argument('--csv', default=os.path.join(BASE, 'tools', 'latest.csv'))
    args = ap.parse_args()

    with open(REPORT_IN, encoding='utf-8') as f:
        sea_points = json.load(f)

    targets = {}
    for p in sea_points:
        if p['nearest_land_km'] <= args.min_dist:
            continue
        for rid in p['ids']:
            targets[rid] = p
    print(f'海上判定座標: {len(sea_points)}, うち修正対象(>{args.min_dist}km): '
          f'{sum(1 for p in sea_points if p["nearest_land_km"] > args.min_dist)}座標 '
          f'{len(targets)}レコード')
    if not targets:
        return

    gaz = Gazetteer(args.csv)
    geo = Geocoder()

    files = []
    for n in range(1, 5):
        path = os.path.join(BASE, f'disaster_data_{n}.js')
        with open(path, encoding='utf-8') as f:
            txt = f.read()
        prefix = txt[:txt.index('[')]
        data = json.loads(txt[txt.index('['):].rstrip().rstrip(';'))
        files.append((path, prefix, data))

    n_poi = n_town = n_muni = n_land = n_dropped = 0
    report = []
    muni_cache = {}
    poi_cache = {}

    for path, prefix, data in files:
        for d in data:
            p = targets.get(d.get('id'))
            if p is None:
                continue
            spot = (d.get('spot') or '').strip()
            old = (d.get('lat'), d.get('lon'))

            # 0. POI検索 + 30kmゲート (同一spotはキャッシュ)
            if spot not in poi_cache:
                poi_cache[spot] = geo.poi_search(build_query_text(spot), None)
            r = poi_cache[spot]
            if r:
                glat, glon = r[0], r[1]
                if dist_km(glat, glon, old[0], old[1]) <= POI_GATE_KM:
                    d['lat'], d['lon'] = round(glat, 6), round(glon, 6)
                    d['loc_precision'] = 'building'
                    n_poi += 1
                    report.append(f'POI [{d["dataset"]}] {d["id"]} "{spot}" '
                                  f'{old} -> ({glat:.6f},{glon:.6f}) '
                                  f'src={r[3]} match="{r[4]}"')
                    continue

            # 1. 町丁目代表点
            r = gaz.parse(spot)
            if r:
                nc, key, glat, glon, _ = r
                # 引き戻し先が元の海上座標と同一(≒その代表点自体が海上)なら不採用
                if dist_km(glat, glon, old[0], old[1]) > 0.05:
                    d['lat'], d['lon'] = round(glat, 6), round(glon, 6)
                    d['loc_precision'] = 'area-centroid'
                    n_town += 1
                    report.append(f'TOWN [{d["dataset"]}] {d["id"]} "{spot}" '
                                  f'{old} -> ({glat:.6f},{glon:.6f}) {nc}{key}')
                    continue

            # 2. 市区町村代表点 (GSIキャッシュ)
            muni = extract_muni_with_pref(spot)
            if not muni:
                loose = extract_muni_loose(spot)
                muni = loose[0] if loose else None
            if muni:
                if muni not in muni_cache:
                    muni_cache[muni] = geo.area_centroid(muni, muni)
                res = muni_cache[muni]
                if res:
                    glat, glon = res[0], res[1]
                    if dist_km(glat, glon, old[0], old[1]) > 0.05:
                        d['lat'], d['lon'] = round(glat, 6), round(glon, 6)
                        d['loc_precision'] = 'area-centroid'
                        n_muni += 1
                        report.append(f'MUNI [{d["dataset"]}] {d["id"]} "{spot}" '
                                      f'{old} -> ({glat:.6f},{glon:.6f}) {muni}')
                        continue

            # 3. 最寄り陸地点
            if p.get('nearest_land'):
                glat, glon = p['nearest_land']
                d['lat'], d['lon'] = glat, glon
                d['loc_precision'] = 'uncertain'
                n_land += 1
                report.append(f'LAND [{d["dataset"]}] {d["id"]} "{spot}" '
                              f'{old} -> ({glat},{glon}) 最寄り陸地 '
                              f'{p["nearest_land_km"]}km')
                continue

            # 4. 座標取り消し
            d['has_coord'] = False
            d['lat'] = None
            d['lon'] = None
            d['loc_precision'] = 'uncertain'
            n_dropped += 1
            report.append(f'DROP [{d["dataset"]}] {d["id"]} "{spot}" {old} '
                          f'-> 座標取り消し (5km以内に陸地なし)')

    geo.save_cache()
    print(f'POI実位置: {n_poi}, 町丁目代表点: {n_town}, 市区町村代表点: {n_muni}, '
          f'最寄り陸地: {n_land}, 座標取り消し: {n_dropped}')
    with open(REPORT_OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(report))
    print('wrote', REPORT_OUT)

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
