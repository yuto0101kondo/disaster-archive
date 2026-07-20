#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spot に地名・施設名があるのに座標が無いレコードを全数解決するパッチ。

過去の各パスが安全側でスキップ/破棄した積み残し (例: ヒント無しPOIの
「小丸山小学校」、道路名「のと里山海道」、県名のみ「石川県」) を対象に、
以下の優先順で座標を付与する:

  1. 町丁目辞書 (Geolonia) の代表点            → area-centroid
  2. POI検索 (市区町村ヒントあり):
       施設名単体クエリ + 市区町村代表点から30km地理ゲート → building
  3. POI検索 (ヒント無し):
       GSI/OSMで名称が全国で一意に定まる場合のみ採用       → building
       (「小丸山小学校」「石川県庁」「八重洲いしかわテラス」等)
  4. 市区町村代表点 (spot、noto以外はdescからも抽出)        → area-centroid
  5. 都道府県名のみ → 県代表点                              → area-centroid

どの手段でも解決しないものは uncertain のまま残す (虚偽のピンは立てない)。

実行:
  python3 tools/resolve_missing_coords.py --dry-run
  python3 tools/resolve_missing_coords.py
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'tools'))
sys.path.insert(0, os.path.join(BASE, 'src'))

from regeocode import (Geocoder, UNCERTAIN_RE, PREFS,  # noqa: E402
                       build_query_text, extract_muni_with_pref,
                       extract_muni_loose, MUNI_WITH_PREF_RE)
from snap_to_town_centroid import Gazetteer, dist_km  # noqa: E402
from upgrade_poi_pass import find_poi, norm  # noqa: E402
from fix_address_matches import gsi_muni_variants  # noqa: E402
from regeocode import MUNI_ONLY_TEXT_RE  # noqa: E402
import unicodedata

# データセットの「ホーム地域」: ヒント無しPOIはこの圏内の候補を優先する
HOME_REGION = {
    'noto':    (35.8, 37.9, 135.8, 138.5),   # 北陸圏
    'yahoo':   (34.8, 41.8, 138.9, 142.6),   # 東日本
    'shinsai': (34.8, 41.8, 138.9, 142.6),
}
HOME_PREF = {'noto': '石川県'}


def in_region(lat, lon, box):
    return box[0] <= lat <= box[1] and box[2] <= lon <= box[3]


def osm_named_candidates(geo, query):
    res = geo.nominatim(query) or []
    return [c for c in res
            if norm(query) in norm(c.get('display_name', ''))
            or norm(query) == norm(c.get('name', ''))]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--csv', default=os.path.join(BASE, 'tools', 'latest.csv'))
    args = ap.parse_args()

    gaz = Gazetteer(args.csv)
    geo = Geocoder()
    muni_cache = {}

    def muni_point(m):
        if m not in muni_cache:
            muni_cache[m] = geo.area_centroid(m, m)
        return muni_cache[m]

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
            if d.get('has_coord'):
                continue
            spot = (d.get('spot') or '').strip()
            if not spot or spot in ('nan', '未登録') or UNCERTAIN_RE.search(spot):
                continue
            groups[(d['dataset'], spot)].append(d)

    print(f'対象: {len(groups)}ユニークspot / '
          f'{sum(len(v) for v in groups.values())}レコード')

    stats = Counter()
    report = []

    def resolve(ds, spot, recs):
        query = build_query_text(spot)

        # 1. 町丁目辞書
        r = gaz.parse(spot)
        if r:
            _, key, lat, lon, _ = r
            return lat, lon, 'area-centroid', f'town:{key}'

        # ヒント抽出 (notoはspotのみ / 他はdescも)
        hint = extract_muni_with_pref(spot)
        if not hint:
            loose = extract_muni_loose(spot)
            hint = loose[0] if loose else None
        if not hint and ds != 'noto':
            for d in recs[:3]:
                hint = extract_muni_with_pref(
                    f"{d.get('title') or ''} {d.get('desc') or ''}")
                if hint:
                    break

        # 2. POI (ヒントあり: 市区町村代表点から30kmゲート)
        if hint:
            mp = muni_point(hint)
            if mp:
                r = find_poi(geo, query, mp[0], mp[1], allow_water=False)
                if r:
                    lat, lon, prec, src, note = r
                    return lat, lon, prec, f'poi-hinted:{src}:{note[:30]}'

        # 3-a. 市町村名そのもの → 代表点
        ns = unicodedata.normalize('NFKC', spot).strip()
        if not hint and MUNI_ONLY_TEXT_RE.match(ns):
            mp = muni_point(ns)
            if mp:
                return mp[0], mp[1], 'area-centroid', f'muni:{ns}'

        # 3-b. POI (ヒント無し): ホーム県付きクエリ → 圏内候補優先 → 全国一意
        if not hint:
            box = HOME_REGION.get(ds)
            home_pref = HOME_PREF.get(ds)
            # ホーム県を付けたクエリ (「産業展示館 石川県」→金沢) を先に試す
            if home_pref:
                for q2 in (f'{query} {home_pref}', f'{home_pref}{query}'):
                    for c in osm_named_candidates(geo, q2):
                        lat, lon = float(c['lat']), float(c['lon'])
                        if box and in_region(lat, lon, box):
                            return (lat, lon, 'building',
                                    f'poi-homepref:osm:{c.get("display_name","")[:30]}')
                    for c in geo.gsi(q2) or []:
                        title = c.get('properties', {}).get('title', '')
                        coords = c.get('geometry', {}).get('coordinates')
                        if (coords and norm(query) in norm(title)
                                and box and in_region(coords[1], coords[0], box)):
                            return (coords[1], coords[0], 'building',
                                    f'poi-homepref:gsi:{title[:30]}')
            named = osm_named_candidates(geo, query)
            # 圏内候補があればそれを優先
            if box:
                in_box = [c for c in named
                          if in_region(float(c['lat']), float(c['lon']), box)]
                if in_box:
                    c = in_box[0]
                    return (float(c['lat']), float(c['lon']), 'building',
                            f'poi-region:osm:{c.get("display_name","")[:30]}')
            # 圏外は「全国一意 かつ 結果の都道府県名がテキストに出現」する
            # 場合のみ (遠隔の支援拠点・物産展等)。「小松駅」のように
            # Nominatimが同名POIの一部しか返さないケースの誤採用を防ぐ
            variants = gsi_muni_variants(geo, query, query)
            if len(named) == 1 and len(variants) <= 1:
                c = named[0]
                dn = c.get('display_name', '')
                pref = next((p for p in PREFS if p in dn), None)
                stem = pref.rstrip('都道府県') if pref else None
                text = ' '.join(f"{d.get('title') or ''} {d.get('desc') or ''}"
                                for d in recs[:3])
                if stem and stem in text:
                    return (float(c['lat']), float(c['lon']), 'building',
                            f'poi-unique:osm:{dn[:30]}')

        # 4. 市区町村代表点
        if hint:
            mp = muni_point(hint)
            if mp:
                return mp[0], mp[1], 'area-centroid', f'muni:{hint}'

        # 5. 県名のみ
        for p in PREFS:
            if spot.startswith(p) or spot == p:
                mp = muni_point(p)
                if mp:
                    return mp[0], mp[1], 'area-centroid', f'pref:{p}'
        return None

    for (ds, spot), recs in sorted(groups.items()):
        r = resolve(ds, spot, recs)
        if r is None:
            # 複合名 (「穴水中学校・穴水勤労者センター」等) は先頭施設で再試行
            import re as _re
            parts = _re.split(r'[・、,/／]| および | と ', spot)
            if len(parts) > 1 and len(parts[0]) >= 4:
                r = resolve(ds, parts[0].strip(), recs)
        if r is None:
            stats['unresolved'] += len(recs)
            report.append(f'UNRESOLVED [{ds}] "{spot}" ({len(recs)}件)')
            continue
        lat, lon, prec, how = r
        if not (24 <= lat <= 46 and 122 <= lon <= 146):
            stats['rejected-bounds'] += len(recs)
            continue
        for d in recs:
            d['has_coord'] = True
            d['lat'], d['lon'] = round(lat, 6), round(lon, 6)
            d['loc_precision'] = prec
        stats[how.split(':')[0]] += len(recs)
        report.append(f'{how.split(":")[0].upper()} [{ds}] "{spot}" '
                      f'({len(recs)}件) -> ({lat:.6f},{lon:.6f},{prec}) {how}')

    geo.save_cache()
    print('集計:', dict(stats))
    with open(os.path.join(BASE, 'tools', 'resolve_missing_report.txt'),
              'w', encoding='utf-8') as f:
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
