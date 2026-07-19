#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rebuild_eastjapan.py の補完パス:
spot が空/nan/不明 で座標未解決になった yahoo/shinsai レコードについて、
desc・title テキストから市区町村を抽出し、市区町村代表点を付与する。

抽出は2段構え:
  1. 「都道府県名+市区町村名」の厳格パターン (extract_muni_with_pref)
  2. 裸の市区町村名 (「久慈市内」「洋野町の…」等)。ただし誤爆防止のため
     被災地域8県 (青森/岩手/宮城/秋田/山形/福島/茨城/千葉) の市区町村名
     ホワイトリスト (Geolonia辞書由来) に「一意に」一致する場合のみ採用。
     複数県に同名の市区町村がある場合は不採用。

付与する精度は area-centroid (市区町村レベルの粗い位置)。

実行:
  python3 tools/rebuild_desc_pass.py --dry-run
  python3 tools/rebuild_desc_pass.py
"""
import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'tools'))

from regeocode import Geocoder, extract_muni_with_pref, UNCERTAIN_RE  # noqa: E402

REPORT_PATH = os.path.join(BASE, 'tools', 'rebuild_desc_report.txt')
TARGET_PREFS = ('青森県', '岩手県', '宮城県', '秋田県', '山形県',
                '福島県', '茨城県', '千葉県')
BARE_MUNI_RE = re.compile(r'([一-龥々ぁ-んァ-ヶ]{1,6}[市町村区])')


def build_whitelist(csv_path):
    """被災地域8県の市区町村名 → 都道府県付き正式名。同名が複数県に
    ある場合は曖昧なので除外する。郡名は取り除いた別名も登録する。"""
    cands = defaultdict(set)
    with open(csv_path, encoding='utf-8') as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            pref, city = row[1], row[5]
            if pref not in TARGET_PREFS:
                continue
            full = pref + city
            cands[city].add(full)
            m = re.match(r'^(.+?郡)(.+)$', city)
            if m:
                cands[m.group(2)].add(full)
            m = re.match(r'^(.+?市)(.+?区)$', city)
            if m:
                cands[m.group(1)].add(pref + m.group(1))
    return {name: next(iter(fulls)) for name, fulls in cands.items()
            if len(fulls) == 1}


def no_spot(d):
    spot = (d.get('spot') or '').strip()
    return (not spot) or spot == 'nan' or bool(UNCERTAIN_RE.search(spot))


def extract(text, whitelist):
    muni = extract_muni_with_pref(text)
    if muni:
        return muni
    for m in BARE_MUNI_RE.findall(text):
        if m in whitelist:
            return whitelist[m]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--csv', default=os.path.join(BASE, 'tools', 'latest.csv'))
    args = ap.parse_args()

    whitelist = build_whitelist(args.csv)
    print(f'ホワイトリスト市区町村名: {len(whitelist)}')

    geo = Geocoder()
    files = []
    for n in range(1, 5):
        path = os.path.join(BASE, f'disaster_data_{n}.js')
        with open(path, encoding='utf-8') as f:
            txt = f.read()
        prefix = txt[:txt.index('[')]
        data = json.loads(txt[txt.index('['):].rstrip().rstrip(';'))
        files.append((path, prefix, data))

    muni_cache = {}
    n_fixed = n_nomuni = 0
    report = []
    for path, prefix, data in files:
        for d in data:
            if d['dataset'] not in ('yahoo', 'shinsai'):
                continue
            if d.get('has_coord') or not no_spot(d):
                continue
            text = f"{d.get('title') or ''} {d.get('desc') or ''}"
            muni = extract(text, whitelist)
            if not muni:
                n_nomuni += 1
                continue
            if muni not in muni_cache:
                muni_cache[muni] = geo.area_centroid(muni, muni)
            res = muni_cache[muni]
            if not res:
                n_nomuni += 1
                continue
            d['has_coord'] = True
            d['lat'], d['lon'] = round(res[0], 6), round(res[1], 6)
            d['loc_precision'] = 'area-centroid'
            n_fixed += 1
            report.append(f'DESC [{d["dataset"]}] {d["id"]} {muni} '
                          f'-> ({res[0]:.6f},{res[1]:.6f}) desc="{(d.get("desc") or "")[:50]}"')

    geo.save_cache()
    print(f'descから市区町村を特定して座標付与: {n_fixed}件, 特定できず: {n_nomuni}件')
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
