#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
町丁目代表点スナップによる位置精度向上ツール（オフライン実行可）

Geolonia 住所データ（国土交通省 位置参照情報 + 国勢調査境界データ由来、
全国277,543件の大字・町丁目代表点）を辞書として、spot テキストから
市区町村+大字・町丁目を抽出し、以下のルールで座標を補正する:

  1. 町丁目が特定でき、現座標が代表点から 2km 超ズレている
       → 代表点へスナップし loc_precision='area-centroid' に設定
         （POI級の座標が2km以上ズレているなら、正しい町の代表点の方が正確）
  2. spot が町名そのもの（末尾が「付近」「周辺」等のみ）で、
     現座標が代表点から 0.3km 超ズレている area-centroid レコード
       → 公式代表点に揃える
  3. 区名のみのマッチ（例: 原町区）や 0.3km 以内、番地・施設名など
     町名より詳細な情報を含む近距離レコードは変更しない

データ取得:
  curl -L -o latest.csv \
    https://raw.githubusercontent.com/geolonia/japanese-addresses/master/data/latest.csv

実行:
  python3 tools/snap_to_town_centroid.py --csv latest.csv [--dry-run]
"""
import argparse
import csv
import json
import math
import os
import re
import unicodedata
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KANJI_NUM = {'一': '1', '二': '2', '三': '3', '四': '4', '五': '5',
             '六': '6', '七': '7', '八': '8', '九': '9', '十': '10'}
TRAILING_NOISE = ('付近', '周辺', '地内', '地先', '沿い', '内')


def norm(s):
    s = unicodedata.normalize('NFKC', s or '')
    s = s.replace(' ', '').replace('　', '')
    s = re.sub(r'([一二三四五六七八九十])丁目',
               lambda m: KANJI_NUM.get(m.group(1), m.group(1)) + '丁目', s)
    for a, b in (('ヶ', 'ケ'), ('が', 'ケ'), ('ガ', 'ケ'),
                 ('ノ', 'の'), ('之', 'の'), ('ツ', 'ッ')):
        s = s.replace(a, b)
    return s


def dist_km(a, b, c, d):
    R = 6371
    la1, lo1, la2, lo2 = map(math.radians, (a, b, c, d))
    return R * math.acos(min(1, math.sin(la1) * math.sin(la2) +
                             math.cos(la1) * math.cos(la2) * math.cos(lo1 - lo2)))


class Gazetteer:
    def __init__(self, csv_path):
        self.towns = defaultdict(dict)
        self.city_alias = defaultdict(set)
        prefs = set()
        with open(csv_path, encoding='utf-8') as f:
            r = csv.reader(f)
            next(r)
            for row in r:
                pref, city, town = row[1], row[5], row[8]
                try:
                    lat, lon = float(row[12]), float(row[13])
                except ValueError:
                    continue
                nc, nt = norm(city), norm(town)
                self.towns[nc][nt] = (lat, lon)
                prefs.add(pref)
                m = re.match(r'^(.+?郡)(.+)$', city)
                if m:
                    self.city_alias[norm(m.group(2))].add(nc)
                m = re.match(r'^(.+?市)(.+?区)$', city)
                if m:
                    self.city_alias[norm(m.group(1))].add(nc)
        self.prefs = sorted((norm(p) for p in prefs), key=len, reverse=True)
        keys = set(self.towns.keys()) | set(self.city_alias.keys())
        self.city_re = re.compile('|'.join(
            re.escape(k) for k in sorted(keys, key=len, reverse=True)))

    def parse(self, spot):
        """spot → (city, town_key, lat, lon, rest_exact) / None (町丁目一致のみ)"""
        s = norm(spot)
        s = re.sub(r'[（(].*?[)）]', '', s)
        for p in self.prefs:
            if s.startswith(p):
                s = s[len(p):]
                break
        m = self.city_re.search(s)
        if not m:
            return None
        ck, rest = m.group(0), s[m.end():]
        cands = self.city_alias.get(ck, set()) | ({ck} if ck in self.towns else set())
        variants = [(rest, 0)]
        if rest.startswith('大字'):
            variants.append((rest[2:], 0))
        elif rest.startswith('字'):
            variants.append((rest[1:], 0))
        variants += [('字' + rest, 1), ('大字' + rest, 2)]
        best = None
        for nc in cands:
            ts = self.towns[nc]
            for v, pad in variants:
                for ln in range(min(len(v), 14), 0, -1):
                    key = v[:ln]
                    if key in ts:
                        consumed = ln - pad
                        remainder = v[ln:]
                        rest_exact = remainder in TRAILING_NOISE or remainder == ''
                        if best is None or consumed > best[0]:
                            best = (consumed, nc, key, ts[key], rest_exact)
                        break
        if not best:
            return None
        consumed, nc, key, (lat, lon), rest_exact = best
        if key.endswith('区') and len(key) <= 4:   # 区名のみは不採用
            return None
        if consumed < 2:
            return None
        return (nc, key, lat, lon, rest_exact)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--csv', default=os.path.join(BASE, 'tools', 'latest.csv'))
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    g = Gazetteer(args.csv)
    gross = align = 0
    for n in range(1, 5):
        path = os.path.join(BASE, f'disaster_data_{n}.js')
        with open(path, encoding='utf-8') as f:
            txt = f.read()
        prefix = txt[:txt.index('[')]
        data = json.loads(txt[txt.index('['):].rstrip().rstrip(';'))
        for d in data:
            if not (d.get('has_coord') and d.get('lat') and d.get('lon')):
                continue
            r = g.parse(d.get('spot') or '')
            if not r:
                continue
            nc, key, glat, glon, rest_exact = r
            dd = dist_km(d['lat'], d['lon'], glat, glon)
            if dd > 2.0:
                d['lat'], d['lon'] = glat, glon
                d['loc_precision'] = 'area-centroid'
                gross += 1
            elif rest_exact and dd > 0.3 and d.get('loc_precision') == 'area-centroid':
                d['lat'], d['lon'] = glat, glon
                align += 1
        if not args.dry_run:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(prefix + json.dumps(data, ensure_ascii=False,
                                            separators=(',', ':')) + ';')
            print('wrote', path)
    print(f'gross(>2km)スナップ: {gross}件, 町名一致の代表点整列: {align}件')


if __name__ == '__main__':
    main()
