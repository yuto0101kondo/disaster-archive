#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
同名地名の誤ジオコーディング補正ツール

「宮城県仙台市の錦町公園」なのにピンが熊本県の同名地点(錦町公園)に
刺さっている、といった誤りを補正する。テキスト(spot/desc)は正しい
県・市を書いているのに、曖昧な地名が別県の同名地点にマッチした結果である。

判定ロジック:
  各レコードについて、次の優先順で「正しい所在地」を解決する:
    1. spot の括弧内「（県名 市名…）」    (例: 富美岡荘（岩手県大船渡市）)
    2. spot が県名で始まる場合の spot 全体 (例: 宮城県河北町（現石巻市）)
    3. desc 中の唯一の「県名+市区町村」     (例: 錦町公園 → desc「宮城県仙台市」)
  解決した所在地(町丁目 or 市代表点)が、
    ・現在の座標と別の都道府県にあり、かつ
    ・現在の座標から STRICT_KM 以上離れており、かつ
    ・現在座標の実在県名が spot に書かれていない
       (「グランフロント大阪」等、遠方が正しいケースを除外)
  場合のみ、その所在地へスナップする。

市区町村ポリゴン(N03)で座標の実在県を判定するため、要 polys/。
データ取得は tools/fix_sea_pins.py / snap_to_town_centroid.py 参照。

実行:
  python3 tools/fix_homonym_pins.py --polys polys --csv latest.csv --stations station_db.json [--dry-run]
"""
import argparse
import csv
import glob
import json
import os
import re
import sys

from shapely.geometry import shape, Point
from shapely.strtree import STRtree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from snap_to_town_centroid import Gazetteer, dist_km, norm  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRICT_KM = 30.0

PREFS = ['北海道', '青森県', '岩手県', '宮城県', '秋田県', '山形県', '福島県', '茨城県', '栃木県',
         '群馬県', '埼玉県', '千葉県', '東京都', '神奈川県', '新潟県', '富山県', '石川県', '福井県',
         '山梨県', '長野県', '岐阜県', '静岡県', '愛知県', '三重県', '滋賀県', '京都府', '大阪府',
         '兵庫県', '奈良県', '和歌山県', '鳥取県', '島根県', '岡山県', '広島県', '山口県', '徳島県',
         '香川県', '愛媛県', '高知県', '福岡県', '佐賀県', '長崎県', '熊本県', '大分県', '宮崎県',
         '鹿児島県', '沖縄県']
CODE2PREF = {f'{i + 1:02d}': p for i, p in enumerate(PREFS)}
PREF_RE = re.compile('|'.join(PREFS))
PAREN_RE = re.compile(r'[（(]([^)）]*)[)）]')


def build_pref_tree(polys_dir):
    geoms, gpref = [], []
    for f in sorted(glob.glob(os.path.join(polys_dir, 'N03-21_*_210101.json'))):
        pref = CODE2PREF.get(os.path.basename(f).split('_')[1])
        for feat in json.load(open(f, encoding='utf-8'))['features']:
            try:
                geoms.append(shape(feat['geometry']))
                gpref.append(pref)
            except Exception:
                pass
    return STRtree(geoms), geoms, gpref


def pref_of(pt, tree, geoms, gpref):
    for i in tree.query(pt, predicate='intersects'):
        if geoms[i].covers(pt):
            return gpref[i]
    return None


def build_city_rep(g):
    rep = {}
    for nc, ts in g.towns.items():
        pts = list(ts.values())
        mlat = sum(p[0] for p in pts) / len(pts)
        mlon = sum(p[1] for p in pts) / len(pts)
        rep[nc] = min(pts, key=lambda p: (p[0] - mlat) ** 2 + (p[1] - mlon) ** 2)
    # 政令指定都市の区なし名(例:「仙台市」)→ 全区の町丁目の重心
    fam = {}
    for nc, ts in g.towns.items():
        m = re.match(r'(.+?市).+区$', nc)
        if m:
            fam.setdefault(norm(m.group(1)), []).extend(ts.values())
    for base, pts in fam.items():
        if base not in rep:
            mlat = sum(p[0] for p in pts) / len(pts)
            mlon = sum(p[1] for p in pts) / len(pts)
            rep[base] = min(pts, key=lambda p: (p[0] - mlat) ** 2 + (p[1] - mlon) ** 2)
    return rep


# 都道府県の短縮形(照合用): 京都府→京都, 東京都→東京, 〜県→〜, 北海道→北海道
def pref_short(p):
    if p == '北海道':
        return '北海道'
    return p[:-1] if p[-1] in '都府県' else p


def build_muni_pref():
    mp = {}
    for row in csv.reader(open(os.path.join(os.path.dirname(__file__), 'latest.csv'),
                               encoding='utf-8')):
        if len(row) > 13 and row[1] in PREFS:
            mp[norm(row[5])] = row[1]
    return mp


DISASTER_PREFS = {'青森県', '岩手県', '宮城県', '秋田県', '山形県', '福島県', '茨城県', '栃木県',
                  '群馬県', '埼玉県', '千葉県', '東京都', '神奈川県', '新潟県', '富山県', '石川県',
                  '福井県', '長野県', '北海道'}

# 被災地外に落ちた同名誤爆のうち、県名なし市名・ランドマーク名で一般ルールでは
# 解決できない既知地点。被災地外(far)判定のレコードにのみ適用する。
KNOWN_FAR = [
    (re.compile(r'高田松原'), (39.0059, 141.6255), 'building'),      # 陸前高田市(岩手)
    (re.compile(r'七尾.*府中町|本府中町'), (37.0432, 136.9531), 'area-centroid'),  # 七尾市府中町(石川)
    (re.compile(r'仙台吉野町'), (38.2758, 140.8942), 'area-centroid'),  # 仙台市宮城野区
]


def _resolve_str(cand, g, city_rep):
    """地名文字列 → (lat, lon, precision) or None"""
    r = g.parse(cand)
    if r:
        return (r[2], r[3], 'area-centroid' if r[1] else 'uncertain')
    m = PREF_RE.search(cand)
    if m:
        rest = cand[m.end():]
        cm = re.match(r'([一-龥々ぁ-んァ-ヶ]{1,4}郡)?([一-龥々ぁ-んァ-ヶ]{1,6}[市区町村])', rest)
        if cm:
            for key in (norm(cm.group(0)), norm((cm.group(1) or '') + cm.group(2))):
                if key in city_rep:
                    return (*city_rep[key], 'uncertain')
    return None


def resolve_from_spot(d, g, city_rep):
    """spot に県名が明記されている場合のみ解決 (対口支援の desc 混入を避ける)"""
    spot = d.get('spot') or ''
    cands = [m.group(1) for m in PAREN_RE.finditer(spot) if PREF_RE.search(m.group(1))]
    if PREF_RE.search(spot):
        cands.append(spot)
    for cand in cands:
        hit = _resolve_str(cand, g, city_rep)
        if hit:
            return (*hit, cand)
    return None


def resolve_for_faraway(d, g, city_rep, actual):
    """被災地外に落ちたピン専用。spot地名→desc単一被災県 の順で解決。
    実在県(actual)が spot/desc に現れる場合は「その遠方が正しい」とみなし棄却。"""
    spot = d.get('spot') or ''
    desc = d.get('desc') or ''
    # 実在県が spot/desc に(短縮形でも)現れる → その遠方が正しい所在地とみなす
    short = pref_short(actual)
    if short in spot or short in desc:
        return None
    # 既知地点テーブル
    for pat, (lat, lon), prec in KNOWN_FAR:
        if pat.search(spot) or pat.search(desc):
            return (lat, lon, prec, 'KNOWN:' + pat.pattern[:12])
    # spot の地名を gazetteer 解決 (県接頭が無くてもよいが、被災県に限る)
    hit = _resolve_str(spot, g, city_rep)
    if hit and pref_hint(hit) in DISASTER_PREFS:
        return (*hit, spot)
    # desc の唯一被災県 + 直後の市区町村
    found = [p for p in dict.fromkeys(PREF_RE.findall(desc))]
    dis = [p for p in found if p in DISASTER_PREFS]
    if len(set(dis)) == 1:
        p = dis[0]
        mm = re.search(p + r'[^。、\s]{0,12}?[市区町村]', desc)
        hit = _resolve_str(mm.group(0) if mm else p, g, city_rep)
        if hit:
            return (*hit, mm.group(0) if mm else p)
    return None


_PT = None


def pref_hint(hit):
    """解決座標の県 (グローバルツリーを参照)"""
    global _PT
    return pref_of(Point(hit[1], hit[0]), *_PT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--polys', default=os.path.join(BASE, 'polys'))
    ap.add_argument('--csv', default=os.path.join(BASE, 'tools', 'latest.csv'))
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    tree, geoms, gpref = build_pref_tree(args.polys)
    global _PT
    _PT = (tree, geoms, gpref)
    g = Gazetteer(args.csv)
    city_rep = build_city_rep(g)

    files = []
    for n in range(1, 5):
        path = os.path.join(BASE, f'disaster_data_{n}.js')
        txt = open(path, encoding='utf-8').read()
        files.append((path, txt[:txt.index('[')],
                      json.loads(txt[txt.index('['):].rstrip().rstrip(';'))))

    n1 = n2 = 0
    for _, _, data in files:
        for d in data:
            if not (d.get('has_coord') and d.get('lat')):
                continue
            spot = d.get('spot') or ''
            actual = pref_of(Point(d['lon'], d['lat']), tree, geoms, gpref)
            if actual is None:
                continue
            # --- ルール1: spot に県名明記 → その県の市へ (全域適用・対口支援回避) ---
            res = None
            if actual not in spot:
                res = resolve_from_spot(d, g, city_rep)
            rule = 1
            # --- ルール2: 被災地外のピン → spot地名/desc被災県 で解決 ---
            if not res and actual not in DISASTER_PREFS:
                res = resolve_for_faraway(d, g, city_rep, actual)
                rule = 2
            if not res:
                continue
            rlat, rlon, prec, src = res
            rpref = pref_of(Point(rlon, rlat), tree, geoms, gpref)
            if rpref is None or rpref == actual:
                continue
            if dist_km(d['lat'], d['lon'], rlat, rlon) < STRICT_KM:
                continue
            print(f'  [R{rule}] {d["id"]:18s} {actual}({d["lat"]:.2f},{d["lon"]:.2f}) → '
                  f'{rpref}({rlat:.2f},{rlon:.2f}) | spot={spot[:22]} | 手掛かり={src[:22]}')
            d['lat'], d['lon'] = round(rlat, 6), round(rlon, 6)
            d['loc_precision'] = prec
            if rule == 1:
                n1 += 1
            else:
                n2 += 1
    print(f'\nルール1(spot県明記): {n1}件, ルール2(被災地外): {n2}件, 計{n1 + n2}件')

    if args.dry_run:
        print('(dry-run)')
        return
    for path, prefix, data in files:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(prefix + json.dumps(data, ensure_ascii=False, separators=(',', ':')) + ';')
        print('wrote', path)


if __name__ == '__main__':
    main()
