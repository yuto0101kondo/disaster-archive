#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
東日本大震災データ (yahoo/shinsai) のゼロベース再ジオコーディング

背景: 元データの座標はAI推定値で、施設名レコードですら中央値4km・
最大19km級の誤差があった(能登データはジオコーダ由来で正確)。
そのため yahoo/shinsai の既存座標は一切参照せず、テキスト(spot、
補助的に desc)のみから座標を再構築する。

解決ロジック (spot の分類に応じて優先順位を変える):
  施設名を含む spot:
    1. POI検索 (Nominatim/GSI、市区町村ヒント必須一致) → building
    2. 町丁目辞書 (Geolonia) の代表点              → area-centroid
    3. POI検索 (ヒント無し + 東日本域内ゲート)      → building
    4. 市区町村代表点 (GSI)                        → area-centroid
  住所・地区名の spot:
    1. 町丁目辞書 (Geolonia) の代表点              → area-centroid
    2. 市区町村代表点 (GSI)                        → area-centroid
  「推定」等を含む / どの手段でも解決できない spot:
    → has_coord=False, loc_precision='uncertain' (地図非表示)

・ヒント無しPOI検索は「クエリ名が結果名に完全含有」かつ「結果が
  東日本域内 (lat 34.8-41.8, lon 138.9-142.6)」の二重条件で誤爆を防ぐ。
・API結果は regeocode と同じキャッシュ (geocode_cache_v2.json) を共有し
  中断後も再開できる。

実行:
  python3 tools/rebuild_eastjapan.py --dry-run --limit 200
  python3 tools/rebuild_eastjapan.py
"""
import argparse
import json
import os
import sys
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'tools'))

from regeocode import (Geocoder, classify, get_muni_hint,  # noqa: E402
                       build_query_text, UNCERTAIN_RE)
from snap_to_town_centroid import Gazetteer  # noqa: E402

REPORT_PATH = os.path.join(BASE, 'tools', 'rebuild_report.txt')
EAST_JAPAN_BOUNDS = (34.8, 41.8, 138.9, 142.6)  # lat_min, lat_max, lon_min, lon_max


def in_east_japan(lat, lon):
    lat_min, lat_max, lon_min, lon_max = EAST_JAPAN_BOUNDS
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def resolve(spot, ds, recs, gaz, geo):
    """spot テキストから (lat, lon, precision, how) または None を返す"""
    cls = classify(spot)
    if cls == 'uncertain':
        return None
    pref_muni, muni = get_muni_hint(ds, spot, recs)
    hint = pref_muni or muni
    query = build_query_text(spot)

    def town():
        r = gaz.parse(spot)
        if r:
            nc, key, lat, lon, _ = r
            return lat, lon, 'area-centroid', f'town:{nc}{key}'
        return None

    def poi_hinted():
        if not hint:
            return None
        r = geo.poi_search(query, hint)
        if r:
            return r[0], r[1], 'building', f'poi:{r[3]}:{r[4][:40]}'
        return None

    def poi_gated():
        r = geo.poi_search(query, None)
        if r and in_east_japan(r[0], r[1]):
            return r[0], r[1], 'building', f'poi-gated:{r[3]}:{r[4][:40]}'
        return None

    def muni_point():
        if not hint:
            return None
        r = geo.area_centroid(query, hint)
        if r:
            return r[0], r[1], 'area-centroid', f'muni:{r[3]}:{r[4][:40]}'
        return None

    if cls == 'building':
        # 市区町村ヒントがある場合はヒント無し全国検索(poi_gated)に落とさない。
        # ヒント付き検索が失敗した施設をヒント無しで再検索すると、他地方の
        # 同名・類似POI(チェーン店本社など)に誤マッチするため。
        if hint:
            strategies = (poi_hinted, town, muni_point)
        else:
            strategies = (town, poi_gated)
    else:
        strategies = (town, muni_point)
    for s in strategies:
        r = s()
        if r:
            return r
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--csv', default=os.path.join(BASE, 'tools', 'latest.csv'))
    ap.add_argument('--checkpoint-every', type=int, default=500)
    args = ap.parse_args()

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

    groups = defaultdict(list)
    for _, _, data in files:
        for d in data:
            if d['dataset'] not in ('yahoo', 'shinsai'):
                continue
            spot = (d.get('spot') or '').strip()
            groups[(d['dataset'], spot)].append(d)
    print(f'対象レコード: {sum(len(v) for v in groups.values())}, '
          f'ユニークspot: {len(groups)}')

    items = list(groups.items())
    if args.limit:
        items = items[:args.limit]

    stats = defaultdict(int)
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
        result = None
        if spot and spot != 'nan' and not UNCERTAIN_RE.search(spot):
            result = resolve(spot, ds, recs, gaz, geo)
        if result:
            lat, lon, prec, how = result
            for d in recs:
                d['has_coord'] = True
                d['lat'], d['lon'] = round(lat, 6), round(lon, 6)
                d['loc_precision'] = prec
            stats[how.split(':')[0]] += len(recs)
            report.append(f'{how.split(":")[0].upper()} [{ds}] "{spot}" '
                          f'({len(recs)}件) -> ({lat:.6f},{lon:.6f}) {how}')
        else:
            for d in recs:
                d['has_coord'] = False
                d['lat'] = None
                d['lon'] = None
                d['loc_precision'] = 'uncertain'
            stats['unresolved'] += len(recs)
            report.append(f'UNRESOLVED [{ds}] "{spot}" ({len(recs)}件)')

        if (i + 1) % 100 == 0:
            print(f'... {i+1}/{len(items)} spots '
                  f'(API:{geo.n_calls} cache:{geo.n_cache_hits}) '
                  f'{dict(stats)}', flush=True)
        if not args.dry_run and (i + 1) % args.checkpoint_every == 0:
            write_all()
            print(f'--- checkpoint {i+1}/{len(items)} ---', flush=True)

    print('\n最終集計(レコード数):', dict(stats))
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
