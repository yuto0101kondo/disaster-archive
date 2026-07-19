#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rebuild_eastjapan.py の poi-gated (ヒント無しPOI検索) 採用分の後処理修正。

問題1: spot に市区町村ヒントがあるのにヒント付き検索が失敗した場合、
  ヒント無し全国検索に落ちて「ファミリーマート(宮古市内)」→東京本社の
  ような矛盾マッチが起きていた。
  → ヒントがある spot の poi-gated 採用は市区町村代表点に置き換える。

問題2: ヒントが全く無い spot は東日本ゲート(関東含む)を通過した誤マッチ
  (「新地駅」→関東の同名地点など)がありうる。
  → 東北コア域(lat 36.6-41.8, lon 139.9-142.6)内はそのまま維持。
    コア域外は結果地点の都道府県名がレコードのテキスト(title/desc/spot)に
    現れる場合のみ維持(東京の帰宅困難者写真等は維持される)。
    それ以外は、テキストから被災地域の市区町村が特定できれば代表点へ、
    できなければ座標を破棄する。

実行:
  python3 tools/fix_gated_pois.py --dry-run
  python3 tools/fix_gated_pois.py
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'tools'))

from regeocode import Geocoder, get_muni_hint  # noqa: E402
from rebuild_desc_pass import build_whitelist, extract  # noqa: E402
from snap_to_town_centroid import dist_km  # noqa: E402

REPORT_IN = os.path.join(BASE, 'tools', 'rebuild_report.txt')
REPORT_OUT = os.path.join(BASE, 'tools', 'fix_gated_report.txt')
CORE = (36.6, 41.8, 139.9, 142.6)
REV_URL = ('https://mreversegeocoder.gsi.go.jp/reverse-geocoder/'
           'LonLatToAddress?lon={lon}&lat={lat}')
PREF_BY_CODE = {
    '01': '北海道', '02': '青森県', '03': '岩手県', '04': '宮城県', '05': '秋田県',
    '06': '山形県', '07': '福島県', '08': '茨城県', '09': '栃木県', '10': '群馬県',
    '11': '埼玉県', '12': '千葉県', '13': '東京都', '14': '神奈川県', '15': '新潟県',
    '16': '富山県', '17': '石川県', '18': '福井県', '19': '山梨県', '20': '長野県',
    '21': '岐阜県', '22': '静岡県', '23': '愛知県', '24': '三重県', '25': '滋賀県',
    '26': '京都府', '27': '大阪府', '28': '兵庫県', '29': '奈良県', '30': '和歌山県',
    '31': '鳥取県', '32': '島根県', '33': '岡山県', '34': '広島県', '35': '山口県',
    '36': '徳島県', '37': '香川県', '38': '愛媛県', '39': '高知県', '40': '福岡県',
    '41': '佐賀県', '42': '長崎県', '43': '熊本県', '44': '大分県', '45': '宮崎県',
    '46': '鹿児島県', '47': '沖縄県'}


def pref_of(lat, lon):
    url = REV_URL.format(lon=lon, lat=lat)
    req = urllib.request.Request(url, headers={'User-Agent': 'disaster-archive-fix/1.0'})
    try:
        res = json.loads(urllib.request.urlopen(req, timeout=15).read())
        cd = (res.get('results') or {}).get('muniCd', '')
        return PREF_BY_CODE.get(cd[:2])
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--csv', default=os.path.join(BASE, 'tools', 'latest.csv'))
    args = ap.parse_args()

    gated = {}   # (ds, spot) -> (lat, lon)
    pat = re.compile(r'POI-GATED \[(\w+)\] "(.*)" \(\d+件\) -> \(([\d.]+),([\d.]+)\)')
    with open(REPORT_IN, encoding='utf-8') as f:
        for line in f:
            m = pat.match(line)
            if m:
                gated[(m.group(1), m.group(2))] = (float(m.group(3)), float(m.group(4)))
    print(f'poi-gated 採用spot: {len(gated)}')

    whitelist = build_whitelist(args.csv)
    geo = Geocoder()

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
            if key in gated:
                groups.setdefault(key, []).append(d)

    muni_cache = {}
    pref_cache = {}
    stats = {'kept-core': 0, 'kept-pref-in-text': 0, 'kept-hint-consistent': 0,
             'to-muni-hint': 0, 'to-muni-text': 0, 'dropped': 0}
    report = []
    last_req = [0.0]

    def muni_point(muni):
        if muni not in muni_cache:
            muni_cache[muni] = geo.area_centroid(muni, muni)
        return muni_cache[muni]

    for (ds, spot), recs in groups.items():
        lat, lon = gated[(ds, spot)]
        pref_muni, muni = get_muni_hint(ds, spot, recs)
        hint = pref_muni or muni
        text = ' '.join(f"{d.get('title') or ''} {d.get('desc') or ''} {spot}"
                        for d in recs[:5])

        def apply(nlat, nlon, prec, tag):
            for d in recs:
                d['has_coord'] = True
                d['lat'], d['lon'] = round(nlat, 6), round(nlon, 6)
                d['loc_precision'] = prec
            stats[tag] += len(recs)
            report.append(f'{tag.upper()} [{ds}] "{spot}" ({len(recs)}件) '
                          f'({lat},{lon}) -> ({nlat:.6f},{nlon:.6f})')

        def drop():
            for d in recs:
                d['has_coord'] = False
                d['lat'] = None
                d['lon'] = None
                d['loc_precision'] = 'uncertain'
            stats['dropped'] += len(recs)
            report.append(f'DROPPED [{ds}] "{spot}" ({len(recs)}件) ({lat},{lon})')

        if hint:
            # 問題1: ヒントがあるのに全国検索に落ちていた。
            # ゲート結果がヒント市区町村の代表点から20km以内なら整合と
            # みなして維持し、矛盾する場合のみ市区町村代表点へ置き換える。
            r = muni_point(hint)
            if r and dist_km(lat, lon, r[0], r[1]) <= 20.0:
                stats['kept-hint-consistent'] += len(recs)
                continue
            if r:
                apply(r[0], r[1], 'area-centroid', 'to-muni-hint')
            elif CORE[0] <= lat <= CORE[1] and CORE[2] <= lon <= CORE[3]:
                stats['kept-core'] += len(recs)
            else:
                drop()
            continue

        if CORE[0] <= lat <= CORE[1] and CORE[2] <= lon <= CORE[3]:
            stats['kept-core'] += len(recs)
            continue

        # コア域外: 結果地点の都道府県名がテキストに現れるか
        key = (round(lat, 4), round(lon, 4))
        if key not in pref_cache:
            wait = 0.4 - (time.time() - last_req[0])
            if wait > 0:
                time.sleep(wait)
            last_req[0] = time.time()
            pref_cache[key] = pref_of(lat, lon)
        pref = pref_cache[key]
        if pref and pref in text:
            stats['kept-pref-in-text'] += len(recs)
            report.append(f'KEPT-PREF [{ds}] "{spot}" ({len(recs)}件) {pref}')
            continue
        wl_muni = extract(text, whitelist)
        if wl_muni:
            r = muni_point(wl_muni)
            if r:
                apply(r[0], r[1], 'area-centroid', 'to-muni-text')
                continue
        drop()

    geo.save_cache()
    print('集計(レコード数):', stats)
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
