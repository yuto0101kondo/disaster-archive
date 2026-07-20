#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
海上ピン検出・補正ツール

市区町村ポリゴン(国土数値情報N03、smartnews-smri/japan-topography の
簡略化GeoJSON)で全マーカーの陸地判定を行い、陸地から250m超離れた
「海上ピン」を次の優先順位で補正する:

  1. spot に「沖」「海上」「船」「フェリー」等が含まれる → 海上が正しい
     ので変更しない
  2. spot が小島・陸繋島(簡略化ポリゴンから欠落している田代島・蕪島・
     浦戸諸島など)を指す → 島の公式代表点から800m以内なら正しい位置と
     みなして維持、それ以遠なら島の代表点へスナップ
  3. spot から町丁目代表点が特定できる(Geolonia住所データ)
     → 代表点へスナップ
  4. それ以外 → 最寄りの海岸線(ポリゴン境界の最近傍点)へスナップ

データ取得:
  polys/ : https://raw.githubusercontent.com/smartnews-smri/japan-topography/
           main/data/municipality/geojson/s0010/N03-21_{01..47}_210101.json
  latest.csv : tools/snap_to_town_centroid.py のヘッダ参照

実行:
  python3 tools/fix_sea_pins.py --polys polys/ --csv latest.csv [--dry-run]
"""
import argparse
import glob
import json
import math
import os
import re
import sys

from shapely.geometry import shape, Point
from shapely.strtree import STRtree
from shapely.ops import nearest_points

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from snap_to_town_centroid import Gazetteer  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEA_OK_RE = re.compile(r'沖\b|沖$|沖）|海上|船|フェリー|湾内|洋上|上空')
THRESHOLD_M = 250.0

# 簡略化ポリゴンから欠落しがちな小島・陸繋島とその公式代表点
# (Geolonia住所データの大字代表点、蕪島のみ著名ランドマーク座標)
ISLANDS = [
    (re.compile(r'蕪島|蕪嶋'), (40.5386, 141.5577)),
    (re.compile(r'島越'), (39.904876, 141.922931)),      # 田野畑村島越
    (re.compile(r'机島|机浜'), (39.961224, 141.93605)),  # 田野畑村机
    (re.compile(r'田代島'), (38.298565, 141.417588)),    # 石巻市田代浜
    (re.compile(r'網地島'), (38.273056, 141.471298)),    # 石巻市網地浜
    (re.compile(r'長渡'), (38.259218, 141.481513)),
    (re.compile(r'桂島'), (38.333954, 141.091222)),      # 塩竈市浦戸桂島
    (re.compile(r'野々島'), (38.337967, 141.110782)),
    (re.compile(r'寒風沢'), (38.337109, 141.126865)),
    (re.compile(r'朴島'), (38.331076, 141.102616)),      # 浦戸石浜
    (re.compile(r'宮戸'), (38.33812, 141.15014)),        # 東松島市宮戸
    (re.compile(r'出島'), (38.450909, 141.523646)),      # 女川町出島
    (re.compile(r'江島'), (38.399273, 141.593873)),      # 女川町江島
    (re.compile(r'金華山'), (38.2967, 141.5657)),
    (re.compile(r'気仙沼大島|大島.*亀山|亀山.*大島|小田の浜'), (38.866555, 141.617459)),
    # 島ではないが、簡略化ポリゴンに含まれない砂州上の著名ランドマーク
    (re.compile(r'一本松|高田松原'), (39.0006, 141.6354)),   # 陸前高田・気仙川河口の砂州
]
ISLAND_KEEP_M = 800.0


def approx_m(lat1, lon1, lat2, lon2):
    ky = 111320.0
    kx = ky * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lat1 - lat2) * ky, (lon1 - lon2) * kx)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--polys', required=True)
    ap.add_argument('--csv', required=True)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    geoms = []
    for f in sorted(glob.glob(os.path.join(args.polys, '*.json'))):
        gj = json.load(open(f, encoding='utf-8'))
        for feat in gj['features']:
            try:
                geoms.append(shape(feat['geometry']))
            except Exception:
                pass
    tree = STRtree(geoms)
    print(f'polygons: {len(geoms)}', file=sys.stderr)
    gaz = Gazetteer(args.csv)

    n_sea = n_seaok = n_town = n_coast = 0
    n_island_snap = n_island_keep = 0
    for n in range(1, 5):
        path = os.path.join(BASE, f'disaster_data_{n}.js')
        txt = open(path, encoding='utf-8').read()
        prefix = txt[:txt.index('[')]
        data = json.loads(txt[txt.index('['):].rstrip().rstrip(';'))
        for d in data:
            if not (d.get('has_coord') and d.get('lat') and d.get('lon')):
                continue
            pt = Point(d['lon'], d['lat'])
            onland = any(geoms[i].covers(pt)
                         for i in tree.query(pt, predicate='intersects'))
            if onland:
                continue
            # 陸地までの実距離 (近傍ポリゴンとの最近傍点で概算)
            box = pt.buffer(0.3)
            best = None  # (dist_m, nlat, nlon)
            for i in tree.query(box, predicate='intersects'):
                p1, _ = nearest_points(geoms[i], pt)
                dm = approx_m(d['lat'], d['lon'], p1.y, p1.x)
                if best is None or dm < best[0]:
                    best = (dm, p1.y, p1.x)
            if best is None or best[0] <= THRESHOLD_M:
                continue
            n_sea += 1
            spot = d.get('spot') or ''
            if SEA_OK_RE.search(spot):
                n_seaok += 1
                continue
            island = next((pt for pat, pt in ISLANDS if pat.search(spot)), None)
            if island:
                if approx_m(d['lat'], d['lon'], island[0], island[1]) > ISLAND_KEEP_M:
                    d['lat'], d['lon'] = island
                    d['loc_precision'] = 'area-centroid'
                    n_island_snap += 1
                else:
                    n_island_keep += 1
                continue
            r = gaz.parse(spot)
            if r:
                d['lat'], d['lon'] = r[2], r[3]
                d['loc_precision'] = 'area-centroid'
                n_town += 1
            else:
                d['lat'], d['lon'] = round(best[1], 6), round(best[2], 6)
                n_coast += 1
                print(f'  coast-snap {best[0]:7.0f}m {d["id"]:22s} {spot[:40]}',
                      file=sys.stderr)
        if not args.dry_run:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(prefix + json.dumps(data, ensure_ascii=False,
                                            separators=(',', ':')) + ';')
            print('wrote', path, file=sys.stderr)
    print(f'海上ピン: {n_sea}件 (海上が正しい: {n_seaok}, 島の位置を維持: {n_island_keep}, '
          f'島の代表点へ: {n_island_snap}, 町丁目代表点へ: {n_town}, 海岸線へ: {n_coast})')


if __name__ == '__main__':
    main()
