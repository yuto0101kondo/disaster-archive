#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
東日本大震災データ(yahoo/shinsai)の座標が海上に落ちていないかを
国土地理院 逆ジオコーディングAPIで判定する。

1次判定: 座標そのもので逆ジオコーディング。陸地上の住所が引ける座標は
  {"results": {...}} を返し、海上・陸地DB外の座標は {} を返す。
2次判定: 1次判定で「陸地DB外」となった座標について、周囲を同心円状に
  探索し最寄りの陸地(住所が引ける地点)までのおおよその距離を求める。
  港湾施設・水門・海岸の駅など、正当に海際にあるスポットは数百m以内に
  陸地が見つかるはずなので、これらを「海上ズレ」の誤検知から除外する。
  目安: 陸地まで2km超 → 海上ズレの可能性が高い

結果は tools/sea_points_report.json に書き出す(中断再開可能)。
"""
import json
import math
import os
import time
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_PATH = os.path.join(BASE, 'tools', 'sea_points_report.json')
CACHE_PATH = os.path.join(BASE, 'tools', 'sea_check_cache.json')
REV_URL = 'https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress?lon={lon}&lat={lat}'
UA = 'disaster-archive-seacheck/1.0 (contact: kondo20060101@gmail.com)'
THROTTLE = 0.35

# 同心円探索の半径(km)と各半径での探索方向数
RINGS = [(0.3, 8), (0.8, 8), (2.0, 12), (5.0, 12)]
FAR_THRESHOLD_KM = 2.0  # これを超えて陸地が無ければ「海上ズレ」と判定


def load_all():
    files, data = [], []
    for n in range(1, 5):
        path = os.path.join(BASE, f'disaster_data_{n}.js')
        with open(path, encoding='utf-8') as f:
            txt = f.read()
        prefix = txt[:txt.index('[')]
        d = json.loads(txt[txt.index('['):].rstrip().rstrip(';'))
        files.append((path, prefix, d))
        data.extend(d)
    return files, data


def http_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode('utf-8'))


class Throttled:
    def __init__(self, cache):
        self.cache = cache
        self.last_req = 0.0
        self.n_new = 0

    def check(self, lat, lon):
        """陸地上かどうかを判定。True=陸地, False=陸地DB外(海上候補), None=通信エラー"""
        key = f'{round(lat,5)},{round(lon,5)}'
        if key in self.cache:
            return self.cache[key]
        wait = THROTTLE - (time.time() - self.last_req)
        if wait > 0:
            time.sleep(wait)
        self.last_req = time.time()
        try:
            res = http_json(REV_URL.format(lon=lon, lat=lat))
            on_land = bool(res.get('results'))
        except Exception:
            on_land = None
        self.cache[key] = on_land
        self.n_new += 1
        return on_land


def offset(lat, lon, dist_km, bearing_deg):
    """簡易平面近似でのオフセット座標(短距離探索用途で十分な精度)"""
    dlat = (dist_km / 111.0) * math.cos(math.radians(bearing_deg))
    dlon = (dist_km / (111.0 * math.cos(math.radians(lat)))) * math.sin(math.radians(bearing_deg))
    return lat + dlat, lon + dlon


def nearest_land_km(th, lat, lon):
    """周囲を同心円状に探索し、最寄りの陸地までの距離(km)を返す。
    見つからなければ最大探索半径を返す(=それ以上遠い、という意味)。
    """
    for radius, n_dirs in RINGS:
        for i in range(n_dirs):
            bearing = 360.0 * i / n_dirs
            plat, plon = offset(lat, lon, radius, bearing)
            if th.check(plat, plon):
                return radius
    return RINGS[-1][0]  # 最大探索半径を超えても見つからず


def main():
    files, data = load_all()
    coords = {}
    for d in data:
        if d.get('has_coord') and d['dataset'] in ('yahoo', 'shinsai'):
            key = (round(d['lat'], 5), round(d['lon'], 5))
            coords.setdefault(key, []).append(d['id'])

    limit = int(os.environ.get('SEACHECK_LIMIT', '0'))
    if limit:
        coords = dict(list(coords.items())[:limit])

    print(f'unique coords to check: {len(coords)}')

    cache = {}
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, encoding='utf-8') as f:
            raw = json.load(f)
        cache = raw

    th = Throttled(cache)
    sea_points = []
    n_checked = 0
    n_stage1_flagged = 0

    for (lat, lon), ids in coords.items():
        on_land = th.check(lat, lon)
        n_checked += 1
        if on_land is False:
            n_stage1_flagged += 1
            dist = nearest_land_km(th, lat, lon)
            if dist > FAR_THRESHOLD_KM:
                sea_points.append({'lat': lat, 'lon': lon, 'ids': ids, 'nearest_land_km': dist})
        if th.n_new and th.n_new % 2000 == 0:
            with open(CACHE_PATH, 'w', encoding='utf-8') as f:
                json.dump(cache, f, ensure_ascii=False)
            print(f'... {n_checked}/{len(coords)} coords checked, '
                  f'{n_stage1_flagged} stage1-flagged, {len(sea_points)} confirmed far-from-land '
                  f'(API calls so far: {th.n_new})', flush=True)

    with open(CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False)

    print(f'\nチェック済み座標: {n_checked}, 陸地DB外(1次): {n_stage1_flagged}, '
          f'陸地まで{FAR_THRESHOLD_KM}km超(確定): {len(sea_points)}')
    sea_points.sort(key=lambda x: -x['nearest_land_km'])
    with open(REPORT_PATH, 'w', encoding='utf-8') as f:
        json.dump(sea_points, f, ensure_ascii=False, indent=1)
    print('wrote', REPORT_PATH)


if __name__ == '__main__':
    main()
