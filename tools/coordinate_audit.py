#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
座標総点検ツール: 駅照合 / 座標なし回収 / 役場スタック分散

パスA: 駅座標の照合
  spot中の「〇〇駅」を駅データベース(Seo-4d696b75/station_database、
  廃駅含む9,372駅)と照合。同名駅は spot の町丁目/市代表点または現座標に
  最も近い候補で曖昧性解決(30km安全ガード)。現座標が駅から500m超
  ズレていれば駅座標へスナップ (loc_precision='building')。
  座標なしの駅レコードにも駅座標を付与。

パスB: 座標なしレコードの回収
  1. データ内の同一spot(正規化後)で信頼できる座標(最大広がり1km未満の
     クラスタ)を持つものがあれば、その中央値を継承
  2. 主要施設の個別登録(CURATED)

パスC: 役場・市代表点スタックの分散
  同一座標に50件以上積み上がった地点のうち、spotが市町村名のみの
  レコードについて、title/desc からその市町村の大字・町丁目名を検索。
  ちょうど1つの大字に絞れた場合のみ、その大字代表点へ移動
  (他市町村の地名は辞書構造上マッチしないため誤爆しない)。

データ取得:
  station_db.json: https://raw.githubusercontent.com/Seo-4d696b75/
                   station_database/main/out/main/station.json
  latest.csv:      tools/snap_to_town_centroid.py のヘッダ参照

実行:
  python3 tools/coordinate_audit.py --csv latest.csv --stations station_db.json [--dry-run]
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from statistics import median

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from snap_to_town_centroid import Gazetteer, dist_km, norm  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STATION_NAME_RE = re.compile(r'([一-龥々ぁ-んァ-ヶA-Za-z0-9ー]{1,12}?)駅')
DOU_NO_EKI_RE = re.compile(r'道の駅\S*')
STACK_MIN = 50
STATION_SNAP_M = 0.5      # km
STATION_GUARD_KM = 30.0

# 座標なしの主要施設 (目視で特定したもの)
CURATED = [
    (re.compile(r'小丸山小学校'), (37.0421, 136.9622), 'building'),      # 七尾市
    (re.compile(r'穴水中学校|穴水勤労者センター'), (37.2325, 136.9048), 'building'),
    (re.compile(r'八重洲いしかわテラス'), (35.6795, 139.7697), 'building'),  # 東京・八重洲
    (re.compile(r'山代温泉'), (36.2920, 136.3655), 'area-centroid'),     # 加賀市
    (re.compile(r'防災対策庁舎'), (38.6775, 141.4463), 'building'),      # 南三陸町
    (re.compile(r'大川小学校'), (38.5454, 141.4266), 'building'),        # 石巻市釜谷
    (re.compile(r'奇跡の一本松|高田松原.*一本松'), (39.0006, 141.6354), 'building'),
]


def load_stations(path):
    by_name = defaultdict(list)
    for s in json.load(open(path, encoding='utf-8')):
        if s.get('lat') and s.get('lng'):
            for nm in {s['name'], s.get('original_name') or s['name']}:
                by_name[norm(nm)].append((s['lat'], s['lng'], s['name']))
    return by_name


def extract_station_names(spot, by_name):
    """spotから駅名候補を抽出。捕捉文字列の最長サフィックスで辞書照合"""
    s = DOU_NO_EKI_RE.sub('', norm(spot))
    out = []
    for m in STATION_NAME_RE.finditer(s):
        cap = m.group(1)
        for i in range(len(cap)):
            key = cap[i:]
            if len(key) >= 2 and key in by_name:
                out.append(key)
                break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--csv', default=os.path.join(BASE, 'tools', 'latest.csv'))
    ap.add_argument('--stations', default=os.path.join(BASE, 'tools', 'station_db.json'))
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    g = Gazetteer(args.csv)
    by_name = load_stations(args.stations)

    files = []
    all_recs = []
    for n in range(1, 5):
        path = os.path.join(BASE, f'disaster_data_{n}.js')
        txt = open(path, encoding='utf-8').read()
        prefix = txt[:txt.index('[')]
        data = json.loads(txt[txt.index('['):].rstrip().rstrip(';'))
        files.append((path, prefix, data))
        all_recs.extend(data)

    # ---- 内部ガゼッティア (同一spotの信頼クラスタ) ----
    spot_groups = defaultdict(list)
    for d in all_recs:
        if d.get('has_coord') and d.get('lat') and (d.get('spot') or '').strip():
            spot_groups[norm(d['spot'])].append(d)
    internal = {}
    for k, recs in spot_groups.items():
        if len(recs) < 2:
            continue
        lats = [r['lat'] for r in recs]
        lons = [r['lon'] for r in recs]
        if dist_km(min(lats), min(lons), max(lats), max(lons)) < 1.0:
            prec = Counter(r.get('loc_precision') for r in recs).most_common(1)[0][0]
            internal[k] = (median(lats), median(lons), prec)

    # ---- スタック地点の把握 ----
    cc = Counter((round(d['lat'], 5), round(d['lon'], 5))
                 for d in all_recs if d.get('has_coord') and d.get('lat'))
    stack_pts = {k for k, n in cc.items() if n >= STACK_MIN}

    n_sta_fix = n_sta_add = n_int = n_cur = n_spread = 0
    for d in all_recs:
        spot = (d.get('spot') or '').strip()
        has = bool(d.get('has_coord') and d.get('lat'))

        # ---- パスA: 駅照合 ----
        if spot:
            names = extract_station_names(spot, by_name)
            if names:
                key = names[-1]
                cands = by_name[key]
                r = g.parse(spot)
                anchor = (r[2], r[3]) if r else ((d['lat'], d['lon']) if has else None)
                best = None
                if anchor:
                    for lat, lng, nm in cands:
                        dd = dist_km(anchor[0], anchor[1], lat, lng)
                        if best is None or dd < best[0]:
                            best = (dd, lat, lng)
                    if best and best[0] > STATION_GUARD_KM:
                        best = None
                elif len(cands) == 1:
                    best = (0.0, cands[0][0], cands[0][1])
                if best:
                    if not has:
                        d['lat'], d['lon'] = round(best[1], 6), round(best[2], 6)
                        d['has_coord'] = True
                        d['loc_precision'] = 'building'
                        n_sta_add += 1
                        continue
                    elif dist_km(d['lat'], d['lon'], best[1], best[2]) > STATION_SNAP_M:
                        d['lat'], d['lon'] = round(best[1], 6), round(best[2], 6)
                        d['loc_precision'] = 'building'
                        n_sta_fix += 1
                        continue
                    else:
                        continue

        # ---- 個別登録施設 (座標なし or 実座標から500m超ズレ) ----
        if spot:
            cur = next((t for t in CURATED if t[0].search(spot)), None)
            if cur:
                _, (clat, clon), prec = cur
                if not has or dist_km(d['lat'], d['lon'], clat, clon) > 0.5:
                    d['lat'], d['lon'] = clat, clon
                    d['has_coord'] = True
                    d['loc_precision'] = prec
                    n_cur += 1
                continue

        # ---- パスB: 座標なし回収 ----
        if not has:
            hit = internal.get(norm(spot)) if spot else None
            if hit:
                d['lat'], d['lon'] = round(hit[0], 6), round(hit[1], 6)
                d['has_coord'] = True
                d['loc_precision'] = hit[2] or 'building'
                n_int += 1
                continue
            continue

        # ---- パスC: スタック分散 (desc/titleから同一市町村内の大字を特定) ----
        if (round(d['lat'], 5), round(d['lon'], 5)) in stack_pts:
            r = g.parse(spot)
            if r and r[1]:
                continue  # spot自体に大字があるなら分散対象外
            s = re.sub(r'[（(].*?[)）]', '', norm(spot))
            m = g.city_re.search(s)
            if not m:
                continue
            cands = g.city_alias.get(m.group(0), set()) | \
                ({m.group(0)} if m.group(0) in g.towns else set())
            if len(cands) != 1:
                continue
            nc = next(iter(cands))
            text = norm((d.get('title') or '') + '　' + (d.get('desc') or ''))
            hits = {}
            for key, (tlat, tlon) in g.towns[nc].items():
                k2 = key[2:] if key.startswith('大字') else (key[1:] if key.startswith('字') else key)
                # 市町村名自体に含まれる大字名(例: 野田村の「大字野田」)は
                # descの市町村名表記に必ずマッチしてしまうため除外
                if len(k2) >= 2 and k2 not in nc and k2 in text:
                    hits[(tlat, tlon)] = key
            if len(hits) == 1:
                (tlat, tlon), key = next(iter(hits.items()))
                d['lat'], d['lon'] = tlat, tlon
                d['loc_precision'] = 'area-centroid'
                n_spread += 1

    print(f'A. 駅座標へ補正: {n_sta_fix}件, 駅座標を新規付与: {n_sta_add}件')
    print(f'B. 同名spotから継承: {n_int}件, 個別登録: {n_cur}件')
    print(f'C. スタックから大字へ分散: {n_spread}件')

    if args.dry_run:
        print('(dry-run)')
        return
    for path, prefix, data in files:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(prefix + json.dumps(data, ensure_ascii=False, separators=(',', ':')) + ';')
        print('wrote', path)


if __name__ == '__main__':
    main()
