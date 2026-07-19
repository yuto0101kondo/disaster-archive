#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
位置情報の再ジオコーディング一括処理スクリプト (v2)

テキストの詳細度に応じて処理を分岐する:
  1. ランドマーク名(施設・POI)を含む spot
       → Nominatim / 国土地理院API で name検索を行い、
         地物(POI)レベルの結果を優先的に採用 → loc_precision = 'building'
  2. 町名・字名など行政区域レベルの spot
       → 国土地理院 住所検索API の大字・町丁目代表点
         (位置参照情報ベース ≒ 区域代表点/重心) を採用
         → loc_precision = 'area-centroid'
  3. どちらとも判定できない / 「推定」を含む → 座標は変更せず
         loc_precision = 'uncertain'

v2 での安全策 (誤爆対策):
  ・市区町村名の手掛かり(muni_hint)は spot テキストを最優先で使う。
    noto データセットは desc に「対口支援」等で応援職員の出身自治体名
    (例:「宝達志水病院_上田市からの給水支援」の上田市)が混入するため、
    desc からの muni_hint 抽出は yahoo/shinsai データセットのみに限定する。
  ・muni_hint が全く得られない場合、既に座標が入っている(has_coord=true)
    レコードは上書きしない(誤った全国検索で正しい座標を壊さないため)。
    座標が無い(has_coord=false)レコードのみ、施設名の完全一致を条件に
    慎重に検索する。
  ・GSI住所検索のPOI一致判定は「クエリ名(ノイズ除去後)がタイトルに
    部分文字列として含まれる」ことを厳格に要求する(以前の実装は先頭
    数文字だけの緩い一致で「さくらドーム21」→無関係な「福島県福島市
    さくら」に誤爆する不具合があった)。
  ・市区町村名のみでの検索(都道府県なし)は同名の別都道府県に誤ヒット
    しうるため、可能な限り都道府県付きの文字列で検索する。
  ・結果が日本の範囲外(緯度24-46, 経度122-146)の場合は採用しない。

・API結果は cache ファイル(JSON)に保存され、中断後も再開できる
・レート制限: 1リクエストあたり最低0.9秒 (Nominatim 利用規約準拠)
・実行例:
    python3 tools/regeocode.py --dry-run --limit 100     # まず動作確認
    python3 tools/regeocode.py                            # 全件処理
    python3 tools/regeocode.py --dataset noto             # 能登データのみ
"""
import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(BASE, 'tools', 'geocode_cache_v2.json')
REPORT_PATH = os.path.join(BASE, 'tools', 'regeocode_report.txt')

GSI_URL = 'https://msearch.gsi.go.jp/address-search/AddressSearch?q={q}'
NOMINATIM_URL = ('https://nominatim.openstreetmap.org/search'
                 '?format=jsonv2&countrycodes=jp&limit=6&q={q}')
USER_AGENT = 'disaster-archive-regeocoder/2.0 (github.com/yuto0101kondo/disaster-archive)'
THROTTLE_SEC = 0.9
JAPAN_BOUNDS = (24.0, 46.0, 122.0, 146.0)  # lat_min, lat_max, lon_min, lon_max

NOMINATIM_POI_CLASSES = {
    'amenity', 'building', 'tourism', 'railway', 'man_made', 'leisure',
    'shop', 'office', 'historic', 'aeroway', 'emergency', 'healthcare',
    'natural', 'waterway',
}

# ---- テキスト詳細度の判定 ----
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
    r'サービスエリア|パーキングエリア|PA\b|SA\b|ヴィレッジ|スタジアム|'
    r'浜|海岸|岬|崎|高台'
)
AREA_RE = re.compile(
    r'都|道|府|県|市|町|村|区|郡|字|丁目|番地|'
    r'地区|地内|地先|沿岸|港湾|川|河口|山|島|半島|峠|湾|沖|街道|国道|県道|'
    r'市街|集落|付近|周辺'
)
UNCERTAIN_RE = re.compile(r'推定|不明|不詳|未特定')
# spot が市区町村名・大字名そのもの(+「内」「の被災地」等の接尾)だけのケース。
# LANDMARK_RE は「浜」「崎」「城」等を含むため、「七ヶ浜町」「多賀城市」の
# ような自治体名を施設と誤判定する。施設判定より先にこのパターンを見る。
MUNI_ONLY_TEXT_RE = re.compile(
    r'^(?:北海道|青森県|岩手県|宮城県|秋田県|山形県|福島県|茨城県|栃木県|群馬県|埼玉県|千葉県|東京都|'
    r'神奈川県|新潟県|富山県|石川県|福井県|山梨県|長野県|岐阜県|静岡県|愛知県|三重県|滋賀県|京都府|'
    r'大阪府|兵庫県|奈良県|和歌山県|鳥取県|島根県|岡山県|広島県|山口県|徳島県|香川県|愛媛県|高知県|'
    r'福岡県|佐賀県|長崎県|熊本県|大分県|宮崎県|鹿児島県|沖縄県)?'
    r'(?:[一-龥々ぁ-んァ-ヶ]{1,4}郡)?[一-龥々ぁ-んァ-ヶ]{1,6}[市町村区]'
    r'(?:内|内の[一-龥々ぁ-んァ-ヶ]{0,6}|の[一-龥々ぁ-んァ-ヶ]{0,4})?$')
NOISE_RE = re.compile(r'[（(].*?[)）]|付近|周辺|の様子|地先|地内|\s+')

PREFS = ['北海道', '青森県', '岩手県', '宮城県', '秋田県', '山形県', '福島県', '茨城県', '栃木県',
         '群馬県', '埼玉県', '千葉県', '東京都', '神奈川県', '新潟県', '富山県', '石川県', '福井県',
         '山梨県', '長野県', '岐阜県', '静岡県', '愛知県', '三重県', '滋賀県', '京都府', '大阪府',
         '兵庫県', '奈良県', '和歌山県', '鳥取県', '島根県', '岡山県', '広島県', '山口県', '徳島県',
         '香川県', '愛媛県', '高知県', '福岡県', '佐賀県', '長崎県', '熊本県', '大分県', '宮崎県',
         '鹿児島県', '沖縄県']
PREF_RE = '(?:' + '|'.join(PREFS) + ')'
MUNI_RE = re.compile(PREF_RE + r'([一-龥々ぁ-んァ-ヶ]{1,10}?[市町村区])')
MUNI_WITH_PREF_RE = re.compile('(' + PREF_RE + r'[一-龥々ぁ-んァ-ヶ]{1,10}?[市町村区])')
VALID_MUNI_RE = re.compile(r'^[一-龥々ァ-ヶ]{1,6}[市町村区]$')
BAD_PREFIX = ('の', 'に', 'へ', 'で', 'と', 'や', 'が', 'は', 'を', 'も', 'か', 'ら', 'り', 'た', 'っ', 'し')


def norm_muni(m):
    if '郡' in m:
        m = m.split('郡')[-1]
    return m


PAREN_RE = re.compile(r'[（(].*?[)）]')


def strip_parens(text):
    """括弧内は「支援元」「補足」等の付随情報であることが多く、本来の所在地と
    異なる市区町村名が書かれがち(例:「...(七尾市への給水支援拠点)」)なため、
    地名抽出の際はまず括弧の外側だけを見る。
    """
    return PAREN_RE.sub(' ', text or '')


def extract_muni_with_pref(text):
    text = text or ''
    m = MUNI_WITH_PREF_RE.search(strip_parens(text))
    if m:
        return m.group(1)
    m = MUNI_WITH_PREF_RE.search(text)
    return m.group(1) if m else None


def _ordered_unique(matches):
    seen = []
    for m in matches:
        m = norm_muni(m)
        if m.endswith('地区') or m.startswith(BAD_PREFIX):
            continue
        if VALID_MUNI_RE.match(m) and m not in seen:
            seen.append(m)
    return seen


def extract_muni(text):
    """都道府県プレフィックス必須の市区町村抽出(ノイズの多い自由文向け)。
    括弧の外側を優先し、テキスト中の出現順を保った候補リストを返す。
    """
    text = text or ''
    out = _ordered_unique(MUNI_RE.findall(strip_parens(text)))
    if out:
        return out
    return _ordered_unique(MUNI_RE.findall(text))


BARE_MUNI_RE = re.compile(r'([一-龥々ァ-ヶ]{1,6}?[市町村区])')


def extract_muni_loose(text):
    """都道府県プレフィックス不要の市区町村抽出(住所的で低ノイズな spot 向け)。
    括弧の外側を優先し、出現順を保った候補リストを返す。
    """
    text = text or ''
    out = _ordered_unique(BARE_MUNI_RE.findall(strip_parens(text)))
    if out:
        return out
    return _ordered_unique(BARE_MUNI_RE.findall(text))


def classify(text):
    text = (text or '').strip()
    if not text or text == 'nan':
        return 'uncertain'
    if UNCERTAIN_RE.search(text):
        return 'uncertain'
    if MUNI_ONLY_TEXT_RE.match(unicodedata.normalize('NFKC', text).strip()):
        return 'area-centroid'
    if LANDMARK_RE.search(text):
        return 'building'
    if AREA_RE.search(text):
        return 'area-centroid'
    if len(text) >= 3:
        return 'building'
    return 'uncertain'


def muni_matches(muni_hint, text):
    """muni_hint がテキスト中に「その市区町村として」現れるかを判定する。
    都道府県を伴わない裸の市区町村名(例:「金沢市」)は、無関係な住所中に
    偶然部分文字列として出現しうる(例:「愛知県知多市金沢市楽」)ため、
    直前に何らかの都道府県名が隣接していることを必須とする。
    """
    if not muni_hint or not text:
        return False
    if any(muni_hint.startswith(p) for p in PREFS):
        return muni_hint in text
    return re.search(PREF_RE + re.escape(muni_hint), text) is not None


def in_japan(lat, lon):
    lat_min, lat_max, lon_min, lon_max = JAPAN_BOUNDS
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


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
        self.n_calls = 0
        self.n_cache_hits = 0

    def _throttle(self):
        wait = THROTTLE_SEC - (time.time() - self.last_req)
        if wait > 0:
            time.sleep(wait)
        self.last_req = time.time()

    def save_cache(self):
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(self.cache, f, ensure_ascii=False)

    def _call(self, key, url):
        if key in self.cache:
            self.n_cache_hits += 1
            return self.cache[key]
        self._throttle()
        self.n_calls += 1
        try:
            result = http_json(url)
        except Exception as e:
            result = {'error': str(e)}
        self.cache[key] = result
        if self.n_calls % 25 == 0:
            self.save_cache()
        return result

    def gsi(self, query):
        key = 'gsi:' + query
        res = self._call(key, GSI_URL.format(q=urllib.parse.quote(query)))
        return res if isinstance(res, list) else None

    def nominatim(self, query):
        key = 'osm:' + query
        res = self._call(key, NOMINATIM_URL.format(q=urllib.parse.quote(query)))
        return res if isinstance(res, list) else None

    def poi_search(self, name, muni_hint):
        """名称でPOIレベルの座標を探す。
        muni_hint がある場合はそれを必須条件にして曖昧さを解消する。
        muni_hint が無い場合は施設名そのものが結果に完全一致することを要求する。
        戻り値: (lat, lon, precision, source, note) または None
        """
        query = f'{name} {muni_hint}' if muni_hint else name

        res = self.nominatim(query)
        if res:
            for cand in res:
                if cand.get('class') not in NOMINATIM_POI_CLASSES:
                    continue
                dn = cand.get('display_name', '')
                if muni_hint:
                    if not muni_matches(muni_hint, dn):
                        continue
                else:
                    if name not in dn:
                        continue
                lat, lon = float(cand['lat']), float(cand['lon'])
                if not in_japan(lat, lon):
                    continue
                return lat, lon, 'building', 'osm-poi', dn[:80]

        res = self.gsi(query)
        if res:
            for cand in res:
                title = cand.get('properties', {}).get('title', '')
                coords = cand.get('geometry', {}).get('coordinates')
                if not coords:
                    continue
                if muni_hint and not muni_matches(muni_hint, title):
                    continue
                # クエリ名(ノイズ除去後)がタイトルに部分文字列として
                # 含まれることを厳格に要求する(緩い先頭一致は誤爆の元)
                if name not in title:
                    continue
                lat, lon = coords[1], coords[0]
                if not in_japan(lat, lon):
                    continue
                return lat, lon, 'building', 'gsi-poi', title[:80]
        return None

    def area_centroid(self, address, muni_hint):
        """住所文字列(muni_hint必須)から大字・町丁目代表点(≒重心)を返す"""
        if not muni_hint:
            return None
        for q in (address, muni_hint):
            res = self.gsi(q)
            if res:
                for cand in res:
                    title = cand.get('properties', {}).get('title', '')
                    if not muni_matches(muni_hint, title):
                        continue
                    coords = cand.get('geometry', {}).get('coordinates')
                    if not coords:
                        continue
                    lat, lon = coords[1], coords[0]
                    if not in_japan(lat, lon):
                        continue
                    return lat, lon, 'area-centroid', 'gsi-addr', title[:80]
        for q in (address, muni_hint):
            res = self.nominatim(q)
            if res:
                for cand in res:
                    dn = cand.get('display_name', '')
                    if not muni_matches(muni_hint, dn):
                        continue
                    lat, lon = float(cand['lat']), float(cand['lon'])
                    if not in_japan(lat, lon):
                        continue
                    return lat, lon, 'area-centroid', 'osm-addr', dn[:80]
        return None


def build_query_text(spot):
    q = unicodedata.normalize('NFKC', spot or '')
    q = NOISE_RE.sub(' ', q).strip()
    return q or spot


def get_muni_hint(ds, spot, recs):
    """muni_hint (都道府県+市区町村を優先) を安全な情報源からのみ抽出する。
    noto データセットは desc に対口支援職員の出身自治体が混入するため、
    spot テキストのみを情報源とする。yahoo/shinsai は spot→desc の順に見る。
    """
    pref_muni = extract_muni_with_pref(spot)
    munis = extract_muni_loose(spot)
    if pref_muni or munis:
        return pref_muni, (munis[0] if munis else None)
    if ds == 'noto':
        return None, None
    for r in recs:
        pref_muni = extract_muni_with_pref(r.get('desc'))
        munis = extract_muni(r.get('desc'))
        if pref_muni or munis:
            return pref_muni, (munis[0] if munis else None)
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='ファイルを書き換えない')
    ap.add_argument('--limit', type=int, default=0, help='処理する固有spot数の上限')
    ap.add_argument('--dataset', choices=['noto', 'shinsai', 'yahoo'], help='対象データセット')
    ap.add_argument('--checkpoint-every', type=int, default=300, help='何件ごとに中間書き込みするか')
    args = ap.parse_args()

    files = []
    for n in range(1, 5):
        path = os.path.join(BASE, f'disaster_data_{n}.js')
        with open(path, encoding='utf-8') as f:
            txt = f.read()
        prefix = txt[:txt.index('[')]
        data = json.loads(txt[txt.index('['):].rstrip().rstrip(';'))
        files.append((path, prefix, data))

    all_records = [d for _, _, data in files for d in data]

    def usable(d):
        spot = (d.get('spot') or '').strip()
        if not spot or spot == 'nan':
            return False
        if UNCERTAIN_RE.search(spot):
            return False
        return True

    targets = [d for d in all_records if usable(d) and (not args.dataset or d['dataset'] == args.dataset)]
    print(f'対象レコード数: {len(targets)}')

    from collections import defaultdict
    groups = defaultdict(list)
    for d in targets:
        groups[(d['dataset'], (d.get('spot') or '').strip())].append(d)
    print(f'ユニークspot数: {len(groups)}')

    geo = Geocoder()
    n_spots = n_poi = n_area = n_items = n_new_coord = n_notfound = n_skipped_nohint = 0
    report_lines = []

    group_items = list(groups.items())
    if args.limit:
        group_items = group_items[:args.limit]

    for i, ((ds, spot), recs) in enumerate(group_items):
        n_spots += 1
        cls = classify(spot)
        pref_muni, muni_hint = get_muni_hint(ds, spot, recs)
        muni_query_hint = pref_muni or muni_hint
        query = build_query_text(spot)

        any_existing_coord = any(d.get('has_coord') for d in recs)

        if cls == 'building':
            if muni_query_hint is None:
                # 市区町村の手掛かりが全く無い場合は誤爆リスクが高いため
                # 新規付与・上書きのどちらも行わない
                n_skipped_nohint += len(recs)
                report_lines.append(f'SKIP(no-hint) [{ds}] "{spot}" ({len(recs)}件)')
                continue
            result = geo.poi_search(query, muni_query_hint)
            if result is None and not any_existing_coord:
                # POIが見つからない場合の住所代表点フォールバックは、座標がまだ
                # 無いレコードにのみ適用する。既に building 精度の座標がある
                # レコードを市区町村代表点(≒役場)へ格下げしてしまう regression を防ぐ
                result = geo.area_centroid(query, muni_query_hint)
        elif cls == 'area-centroid':
            if muni_query_hint is None:
                n_skipped_nohint += len(recs)
                report_lines.append(f'SKIP(no-hint) [{ds}] "{spot}" ({len(recs)}件)')
                continue
            result = geo.area_centroid(query, muni_query_hint)
        else:
            result = None

        if result is None:
            n_notfound += len(recs)
            for d in recs:
                if not d.get('has_coord'):
                    d['loc_precision'] = 'uncertain'
            report_lines.append(f'NOTFOUND [{ds}] "{spot}" ({len(recs)}件)')
        else:
            lat, lon, prec, source, note = result
            n_touched = n_protected = 0
            for d in recs:
                # 既に building 精度の座標があるレコードは、新しい結果も
                # building 精度でない限り上書きしない(市区町村代表点への
                # 格下げ regression を防ぐ)
                if d.get('has_coord') and d.get('loc_precision') == 'building' and prec != 'building':
                    n_protected += 1
                    continue
                actual_prec = prec if muni_query_hint else 'uncertain'
                if not d.get('has_coord'):
                    d['has_coord'] = True
                    n_new_coord += 1
                d['lat'], d['lon'] = round(lat, 6), round(lon, 6)
                d['loc_precision'] = actual_prec
                n_items += 1
                n_touched += 1
            if prec == 'building':
                n_poi += n_touched
            else:
                n_area += n_touched
            protected_note = f' (既存building精度のため{n_protected}件は保護)' if n_protected else ''
            report_lines.append(
                f'FIX [{ds}] "{spot}" ({n_touched}件{protected_note}) -> ({lat:.6f},{lon:.6f},{prec}) '
                f'hint={muni_query_hint} src={source} match="{note}"'
            )

        if (i + 1) % 20 == 0:
            print(f'... {i+1}/{len(group_items)} spots (API呼出:{geo.n_calls} cache:{geo.n_cache_hits})', flush=True)

        if not args.dry_run and (i + 1) % args.checkpoint_every == 0:
            geo.save_cache()
            for path, prefix, data in files:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(prefix + json.dumps(data, ensure_ascii=False, separators=(',', ':')) + ';')
            with open(REPORT_PATH, 'w', encoding='utf-8') as f:
                f.write('\n'.join(report_lines))
            print(f'--- checkpoint at {i+1}/{len(group_items)} written ---', flush=True)

    geo.save_cache()
    with open(REPORT_PATH, 'w', encoding='utf-8') as f:
        f.write('\n'.join(report_lines))

    print(f'\n固有spot処理数: {n_spots}, POI採用: {n_poi}件, 代表点採用: {n_area}件, '
          f'座標更新: {n_items}件, 新規座標付与: {n_new_coord}件, '
          f'未解決: {n_notfound}件, 手掛かり無しでスキップ: {n_skipped_nohint}件')

    if args.dry_run:
        print('(dry-run: ファイルは変更していません)')
        return

    for path, prefix, data in files:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(prefix + json.dumps(data, ensure_ascii=False, separators=(',', ':')) + ';')
        print('wrote', path)


if __name__ == '__main__':
    main()
