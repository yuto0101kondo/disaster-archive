#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
位置情報テキストの詳細度から精度レベル(loc_precision)を判定し、全データに付与する。
  building      : 具体的なランドマーク名(施設・POI)を含む → 建物・施設単位の座標が期待できる
  area-centroid : 町名・字名など行政区域レベルの記述のみ → 地区代表点(重心)レベル
  uncertain     : 座標なし / 「推定」「不明」を含む / 判定不能な自由記述
"""
import json, re

BASE = '/home/user/disaster-archive'

# ランドマーク(POI)を示す語彙。末尾一致でなく部分一致で判定する。
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

# 行政区域・地区レベルの記述(都道府県・市区町村・大字/字・浜/海岸など)
AREA_RE = re.compile(
    r'都|道|府|県|市|町|村|区|郡|字|丁目|番地|'
    r'地区|地内|地先|沿岸|海岸|浜\b|港湾|川|河口|山|島|半島|岬|峠|湾|沖|街道|国道|県道|'
    r'高台|市街|集落|付近|周辺'
)

# 精度を疑うべきキーワード
UNCERTAIN_RE = re.compile(r'推定|不明|不詳|未特定')


def classify(d):
    """1レコードの loc_precision を返す"""
    if not (d.get('has_coord') and d.get('lat') and d.get('lon')):
        return 'uncertain'
    text = (d.get('spot') or '').strip()
    if not text:
        text = (d.get('title') or '').strip()
    if not text:
        return 'uncertain'
    if UNCERTAIN_RE.search(text):
        return 'uncertain'
    if LANDMARK_RE.search(text):
        return 'building'
    if AREA_RE.search(text):
        return 'area-centroid'
    # 地域語を一切含まない単独の固有名詞は施設名とみなす
    # (AI抽出のspotは地区レベルなら必ず県・市・町などを含むため)
    if len(text) >= 3:
        return 'building'
    return 'uncertain'


def main():
    from collections import Counter
    stats = Counter()
    samples = {'building': [], 'area-centroid': [], 'uncertain': []}
    for n in range(1, 5):
        path = f'{BASE}/disaster_data_{n}.js'
        with open(path, encoding='utf-8') as f:
            txt = f.read()
        prefix = txt[:txt.index('[')]
        data = json.loads(txt[txt.index('['):].rstrip().rstrip(';'))
        for d in data:
            p = classify(d)
            d['loc_precision'] = p
            stats[p] += 1
            if len(samples[p]) < 8 and d.get('has_coord'):
                samples[p].append(d.get('spot') or d.get('title') or '')
        out = prefix + json.dumps(data, ensure_ascii=False, separators=(',', ':')) + ';'
        with open(path, 'w', encoding='utf-8') as f:
            f.write(out)
        print(f'wrote {path}')
    print('\nloc_precision counts:', dict(stats))
    for k, v in samples.items():
        print(f'\n[{k}] sample spots:')
        for s in v:
            print('  -', s[:50])


if __name__ == '__main__':
    main()
