#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
位置情報の再ジオコーディング一括処理スクリプト

テキストの詳細度に応じて処理を分岐する:
  1. ランドマーク名(施設・POI)を含む spot
       → 国土地理院 住所検索API / Nominatim で名称検索し、
         地物(POI)レベルの座標を優先採用 → loc_precision = 'building'
  2. 町名・字名など行政区域レベルの spot
       → 国土地理院 住所検索API の大字・町丁目代表点
         (位置参照情報ベース ≒ 区域代表点/重心) を採用
         → loc_precision = 'area-centroid'
  3. どちらとも判定できない / 「推定」を含む → 座標は変更せず
         loc_precision = 'uncertain'

・API結果は cache ファイル(JSON)に保存され、中断後も再開できる
・レート制限: 1リクエスト/秒 (Nominatim の利用規約準拠)
・実行例:
    python3 tools/regeocode.py --dry-run --limit 100     # まず動作確認
    python3 tools/regeocode.py                            # 全件処理
    python3 tools/regeocode.py --dataset noto             # 能登データのみ

※ このスクリプトはネットワークから国土地理院/OSMのAPIに到達できる
   環境で実行してください。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(BASE, 'tools', 'geocode_cache.json')

GSI_URL = 'https://msearch.gsi.go.jp/address-search/AddressSearch?q={q}'
NOMINATIM_URL = ('https://nominatim.openstreetmap.org/search'
                 '?format=jsonv2&countrycodes=jp&limit=5&q={q}')
USER_AGENT = 'disaster-archive-regeocoder/1.0 (github.com/yuto0101kondo/disaster-archive)'

# POI とみなす Nominatim の class
NOMINATIM_POI_CLASSES = {
    'amenity', 'building', 'tourism', 'railway', 'man_made', 'leisure',
    'shop', 'office', 'historic', 'aeroway', 'emergency', 'healthcare',
}

# ---- テキスト詳細度の判定 (index.html の loc_precision と同一ロジック) ----
LANDMARK_RE = re.compile(
    r'寺|神社|神宮|大社|八幡宮|教会|聖堂|'
    r'学校|小学校|中学校|高等学校|高校|大学|幼稚園|保育園|保育所|学園|'
    r'駅|停留所|バス停|空港|飛行場|港|漁港|埠頭|岸壁|桟橋|マリンゲート|ターミナル|'
    r'病院|医院|診療所|クリニック|'
    r'役場|役所|市庁|町庁|村庁|支所|出張所|庁舎|合同庁舎|'
    r'公民館|集会所|体育館|アリーナ|ドーム|グラウンド|球場|競技場|プール|武道館|'
    r'公園|緑地|広場|キャンプ場|'
    r'センター|会館|ホール|プラザ|'
    r'ホテル|旅館|民宿|荘|ペンション|'
    r'橋|大橋|水門|樋門|閘門|堰|ダム|'
    r'灯台|城|温泉|市場|魚市場|道の駅|インターチェンジ|IC\b|トンネル|峠|'
    r'美術館|博物館|資料館|記念館|図書館|水族館|動物園|伝承館|'
    r'工場|発電所|変電所|浄水場|処理場|水質管理センター|終末処理場|クリーンセンター|'
    r'店|ストア|マート|スーパー|コンビニ|セブンイレブン|ファミリーマート|ローソン|イオン|TSUTAYA|ツタヤ|'
    r'銀行|信用金庫|郵便局|交番|駐在所|消防署|消防本部|警察署|税務署|保健所|'
    r'団地|アパート|マンション|ビル|タワー|'
    r'農協|JA\b|漁協|漁業協同組合|営業所|事業所|事務所|本社|支店|'
    r'海水浴場|キャンパス|斎場|霊園|慰霊碑|記念碑|モニュメント|鳥居|'
    r'酒造|造船|製作所|製鉄|製紙|倉庫|給油所|ガソリンスタンド|SS\b|'
    r'サービスエリア|パーキングエリア|PA\b|SA\b|ヴィレッジ|スタジアム'
)
AREA_RE = re.compile(
    r'都|道|府|県|市|町|村|区|郡|字|丁目|番地|'
    r'地区|地内|地先|沿岸|海岸|浜\b|港湾|川|河口|山|島|半島|岬|峠|湾|沖|街道|国道|県道|'
    r'高台|市街|集落|付近|周辺'
)
UNCERTAIN_RE = re.compile(r'推定|不明|不詳|未特定')
# 検索クエリから除去するノイズ (接尾辞・注記)
NOISE_RE = re.compile(r'[（(].*?[)）]|付近|周辺|の様子|地先|地内')


def classify(text):
    text = (text or '').strip()
    if not text:
        return 'uncertain'
    if UNCERTAIN_RE.search(text):
        return 'uncertain'
    if LANDMARK_RE.search(text):
        return 'building'
    if AREA_RE.search(text):
        return 'area-centroid'
    if len(text) >= 3:
        return 'building'  # 地域語を含まない単独固有名詞は施設名とみなす
    return 'uncertain'


def http_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode('utf-8'))


class Geocoder:
    def __init__(self):
        self.cache = {}
        if os.path.exists(CACHE_PATH):
            with open(CACHE_PATH, encoding='utf-8') as f:
                self.cache = json.load(f)
        self.last_req = 0.0

    def _throttle(self):
        wait = 1.0 - (time.time() - self.last_req)
        if wait > 0:
            time.sleep(wait)
        self.last_req = time.time()

    def save_cache(self):
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(self.cache, f, ensure_ascii=False)

    def gsi(self, query):
        """国土地理院 住所検索API。GeoJSON風の候補リストを返す"""
        key = 'gsi:' + query
        if key not in self.cache:
            self._throttle()
            try:
                self.cache[key] = http_json(GSI_URL.format(q=urllib.parse.quote(query)))
            except Exception as e:
                print(f'  ! GSI error for {query}: {e}', file=sys.stderr)
                return None
        return self.cache[key]

    def nominatim(self, query):
        key = 'osm:' + query
        if key not in self.cache:
            self._throttle()
            try:
                self.cache[key] = http_json(NOMINATIM_URL.format(q=urllib.parse.quote(query)))
            except Exception as e:
                print(f'  ! Nominatim error for {query}: {e}', file=sys.stderr)
                return None
        return self.cache[key]

    def poi_search(self, name):
        """名称でPOIレベルの座標を探す。見つかれば (lat, lon, source) を返す"""
        # 1) GSI: 候補 title が検索語とよく一致するものを優先
        res = self.gsi(name)
        if res:
            for cand in res:
                title = cand.get('properties', {}).get('title', '')
                coords = cand.get('geometry', {}).get('coordinates')
                # 施設名がタイトルに含まれる候補のみ採用 (住所のみの候補は除外)
                if coords and title and (name in title or title in name):
                    return coords[1], coords[0], 'gsi-poi'
        # 2) Nominatim: POIクラスの候補のみ採用
        res = self.nominatim(name)
        if res:
            for cand in res:
                if cand.get('class') in NOMINATIM_POI_CLASSES:
                    return float(cand['lat']), float(cand['lon']), 'osm-poi'
        return None

    def area_centroid(self, address):
        """住所文字列から大字・町丁目代表点(≒重心)を返す"""
        res = self.gsi(address)
        if res:
            coords = res[0].get('geometry', {}).get('coordinates')
            if coords:
                return coords[1], coords[0], 'gsi-addr'
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='ファイルを書き換えない')
    ap.add_argument('--limit', type=int, default=0, help='処理する固有spot数の上限')
    ap.add_argument('--dataset', choices=['noto', 'shinsai', 'yahoo'], help='対象データセット')
    args = ap.parse_args()

    files = []
    for n in range(1, 5):
        path = os.path.join(BASE, f'disaster_data_{n}.js')
        with open(path, encoding='utf-8') as f:
            txt = f.read()
        prefix = txt[:txt.index('[')]
        data = json.loads(txt[txt.index('['):].rstrip().rstrip(';'))
        files.append((path, prefix, data))

    geo = Geocoder()
    spot_result = {}   # spot文字列 -> (lat, lon, precision, source) / None
    n_spots = n_poi = n_area = n_items = 0

    try:
        for path, prefix, data in files:
            for d in data:
                if args.dataset and d.get('dataset') != args.dataset:
                    continue
                spot = (d.get('spot') or '').strip()
                if not spot or not d.get('has_coord'):
                    d['loc_precision'] = classify(spot)
                    continue
                cls = classify(spot)
                d['loc_precision'] = cls
                if cls == 'uncertain':
                    continue
                if spot not in spot_result:
                    if args.limit and n_spots >= args.limit:
                        continue
                    n_spots += 1
                    query = NOISE_RE.sub('', spot).strip() or spot
                    if cls == 'building':
                        hit = geo.poi_search(query)
                        if hit is None:
                            # POIが引けなければ住所として代表点を試す
                            hit = geo.area_centroid(query)
                            if hit:
                                hit = (hit[0], hit[1], hit[2])
                                spot_result[spot] = (*hit[:2], 'area-centroid', hit[2])
                            else:
                                spot_result[spot] = None
                        else:
                            spot_result[spot] = (*hit[:2], 'building', hit[2])
                    else:  # area-centroid
                        hit = geo.area_centroid(query)
                        spot_result[spot] = (*hit[:2], 'area-centroid', hit[2]) if hit else None
                    if n_spots % 50 == 0:
                        geo.save_cache()
                        print(f'... {n_spots} spots processed')
                res = spot_result.get(spot)
                if res:
                    lat, lon, prec, source = res
                    if abs(lat - d['lat']) > 1e-6 or abs(lon - d['lon']) > 1e-6:
                        d['lat'], d['lon'] = round(lat, 6), round(lon, 6)
                        n_items += 1
                    d['loc_precision'] = prec
                    if prec == 'building':
                        n_poi += 1
                    else:
                        n_area += 1
    finally:
        geo.save_cache()

    print(f'\n固有spot処理数: {n_spots}, POI採用: {n_poi}件, 代表点採用: {n_area}件, 座標更新: {n_items}件')

    if args.dry_run:
        print('(dry-run: ファイルは変更していません)')
        return
    for path, prefix, data in files:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(prefix + json.dumps(data, ensure_ascii=False, separators=(',', ':')) + ';')
        print('wrote', path)


if __name__ == '__main__':
    main()
