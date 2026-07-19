#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
東日本(yahoo/shinsai)レコードのうち、座標が東日本域外にあるものを検証・修正する。

背景: 市区町村フォールバックには地域ゲートが無く、施設名の一部を市町村名と
誤抽出したケース(「錦町公園」→熊本県錦町)が域外に飛んでいた。一方で
栄村・津南町(長野県北部地震)や札幌・関西の追悼行事など正当な域外レコード
も存在するため、一律削除はできない。

判定 (レコードごと):
  1. 座標地点の都道府県名 または 市区町村名が spot/title/desc に現れる
       → 正当とみなして維持
  2. 現れない → テキストから「都道府県+市区町村」を厳格抽出できれば
       その代表点へ (area-centroid)
  3. それも不可で、被災8県ホワイトリストに一意一致する市区町村名があれば
       その代表点へ (area-centroid)
  4. どれも不可 → 座標破棄 (uncertain)

実行:
  python3 tools/fix_outside_region.py --dry-run
  python3 tools/fix_outside_region.py
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

from regeocode import Geocoder, extract_muni_with_pref  # noqa: E402
from rebuild_desc_pass import build_whitelist  # noqa: E402
from rebuild_eastjapan import EAST_JAPAN_BOUNDS  # noqa: E402
from fix_gated_pois import PREF_BY_CODE  # noqa: E402

REPORT_PATH = os.path.join(BASE, 'tools', 'fix_outside_report.txt')
REV_URL = ('https://mreversegeocoder.gsi.go.jp/reverse-geocoder/'
           'LonLatToAddress?lon={lon}&lat={lat}')


def rev_geocode(lat, lon):
    """(都道府県名, muniCd, 大字・町丁目名) を返す。海上・失敗時は None×3"""
    url = REV_URL.format(lon=lon, lat=lat)
    req = urllib.request.Request(url, headers={'User-Agent': 'disaster-archive-fix/1.0'})
    try:
        res = json.loads(urllib.request.urlopen(req, timeout=15).read())
        r = res.get('results') or {}
        cd = r.get('muniCd') or ''
        return PREF_BY_CODE.get(cd[:2]), cd, r.get('lv01Nm')
    except Exception:
        return None, None, None


def build_muni_code_map(csv_path):
    """市区町村コード(5桁) → 市区町村名 (郡名を除いた短縮名も) の対応表"""
    import csv as _csv
    import re as _re
    m = {}
    with open(csv_path, encoding='utf-8') as f:
        r = _csv.reader(f)
        next(r)
        for row in r:
            code, city = row[4], row[5]
            short = _re.sub(r'^.+?郡', '', city)
            names = {short}
            g = _re.match(r'^(.+?市).+?区$', short)
            if g:
                names.add(g.group(1))  # 政令市の区 → 市名だけの変形も
            m[code] = names
    return m


# 「◯◯県◯◯市からの支援」等の支援元表現。写真の場所ではないため
# 照合の前にテキストから取り除く。
FROM_CLAUSE_RE = re.compile(
    r'(?:' + '|'.join(PREF_BY_CODE.values()) + r')?'
    r'[一-龥々ぁ-んァ-ヶ]{1,6}?[市町村区]?から')


def in_east_japan(lat, lon):
    b = EAST_JAPAN_BOUNDS
    return b[0] <= lat <= b[1] and b[2] <= lon <= b[3]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--csv', default=os.path.join(BASE, 'tools', 'latest.csv'))
    args = ap.parse_args()

    whitelist = build_whitelist(args.csv)
    # ホワイトリストは「名称 → 都道府県付き正式名」。座標地点の市区町村名
    # 照合には GSI 逆ジオコーディングの muniCd では名称が取れないため、
    # Geolonia 辞書の市区町村名を座標近傍照合の代わりにテキスト側で使う。
    geo = Geocoder()

    files = []
    targets = []
    for n in range(1, 5):
        path = os.path.join(BASE, f'disaster_data_{n}.js')
        with open(path, encoding='utf-8') as f:
            txt = f.read()
        prefix = txt[:txt.index('[')]
        data = json.loads(txt[txt.index('['):].rstrip().rstrip(';'))
        files.append((path, prefix, data))
        for d in data:
            if (d['dataset'] in ('yahoo', 'shinsai') and d.get('has_coord')
                    and not in_east_japan(d['lat'], d['lon'])):
                targets.append(d)
    print(f'東日本域外の対象レコード: {len(targets)}')

    rev_cache = {}
    muni_cache = {}
    stats = {'kept': 0, 'to-muni-strict': 0, 'to-muni-whitelist': 0, 'dropped': 0}
    report = []

    def muni_point(muni):
        if muni not in muni_cache:
            muni_cache[muni] = geo.area_centroid(muni, muni)
        return muni_cache[muni]

    muni_names = build_muni_code_map(args.csv)

    for d in targets:
        lat, lon = d['lat'], d['lon']
        key = (round(lat, 4), round(lon, 4))
        if key not in rev_cache:
            time.sleep(0.35)
            rev_cache[key] = rev_geocode(lat, lon)
        pref, muni_cd, town = rev_cache[key]
        name_set = muni_names.get(muni_cd or '', set())
        raw = f"{d.get('spot') or ''} {d.get('title') or ''} {d.get('desc') or ''}"
        # 支援元表現(「福井県大野市からの支援物資」等)は写真の場所ではない
        text = FROM_CLAUSE_RE.sub(' ', raw)

        # 1. テキストから「都道府県+市区町村」を厳格抽出できる場合はそれを最優先。
        #    座標の都道府県と一致すれば正当、食い違えばテキスト側へスナップ
        #    (「錦町公園」の座標が熊本県錦町でも desc に宮城県仙台市とあれば仙台市へ)
        muni = extract_muni_with_pref(text)
        if muni and pref and muni.startswith(pref):
            stats['kept'] += 1
            report.append(f'KEPT(strict一致) [{d["dataset"]}] {d["id"]} '
                          f'"{(d.get("spot") or "")[:20]}" {muni}')
            continue
        if muni:
            r = muni_point(muni)
            if r:
                d['lat'], d['lon'] = round(r[0], 6), round(r[1], 6)
                d['loc_precision'] = 'area-centroid'
                stats['to-muni-strict'] += 1
                report.append(f'TO-MUNI-STRICT [{d["dataset"]}] {d["id"]} '
                              f'"{(d.get("spot") or "")[:20]}" ({lat},{lon}) -> {muni}')
                continue

        # 2. 座標地点の都道府県名/市区町村名/町名がテキストに現れる → 正当
        if ((pref and pref in text)
                or any(nm in text for nm in name_set)
                or (town and len(town) >= 2 and town in text)):
            stats['kept'] += 1
            report.append(f'KEPT [{d["dataset"]}] {d["id"]} "{(d.get("spot") or "")[:20]}" '
                          f'{pref}/{"/".join(name_set)}/{town}')
            continue
        # 3. 被災8県ホワイトリスト
        wl = None
        import re as _re
        for m in _re.findall(r'([一-龥々ぁ-んァ-ヶ]{1,6}[市町村区])', text):
            if m in whitelist:
                wl = whitelist[m]
                break
        if wl:
            r = muni_point(wl)
            if r:
                d['lat'], d['lon'] = round(r[0], 6), round(r[1], 6)
                d['loc_precision'] = 'area-centroid'
                stats['to-muni-whitelist'] += 1
                report.append(f'TO-MUNI-WL [{d["dataset"]}] {d["id"]} '
                              f'"{(d.get("spot") or "")[:20]}" ({lat},{lon}) -> {wl}')
                continue
        # 4. 座標破棄
        d['has_coord'] = False
        d['lat'] = None
        d['lon'] = None
        d['loc_precision'] = 'uncertain'
        stats['dropped'] += 1
        report.append(f'DROPPED [{d["dataset"]}] {d["id"]} '
                      f'"{(d.get("spot") or "")[:20]}" ({lat},{lon}) 域外かつ根拠なし')

    geo.save_cache()
    print('集計:', stats)
    with open(REPORT_PATH, 'w', encoding='utf-8') as f:
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
