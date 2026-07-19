#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ひなぎく検索API (kn.ndl.go.jp/api/item/search-so/hina-cross) の実地調査。

1. ページネーション検証 (sizeの上限 / fromの上限 / 重複 / totalHits整合)
2. レスポンス構造の解析 (フィールド出現率)
3. ライセンス分布 (contentsRightsType / rights / access系)
4. facets の確認 (データベース一覧 = Yahoo系DBの有無)

リクエストは1秒間隔で丁寧に。結果は tools/hinagiku_survey_result.json に保存。
"""
import json
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
      'disaster-archive-hinagiku-survey/1.0 (contact: kondo20060101@gmail.com)')
EP = 'https://kn.ndl.go.jp/api/item/search-so/hina-cross'
OUT = 'tools/hinagiku_survey_result.json'

_last = [0.0]
N_REQ = [0]


def call(params, timeout=40):
    wait = 1.0 - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    N_REQ[0] += 1
    url = EP + '?' + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return r.status, json.loads(body), time.time() - t0, len(body)
    except urllib.error.HTTPError as e:
        return e.code, None, time.time() - t0, 0
    except Exception as e:
        return str(e), None, time.time() - t0, 0


def flatten(d, prefix=''):
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(flatten(v, f'{prefix}{k}.'))
    else:
        out[prefix[:-1]] = d
    return out


result = {}
KW = '気仙沼 津波'

# ---- 1. size の上限 ----
print('== 1. size上限テスト ==')
size_tests = {}
for size in (100, 200, 500, 1000):
    status, data, dt, nbytes = call({'csid': 'hina-cross', 'keyword': KW, 'from': 0, 'size': size})
    got = len(data.get('list', [])) if data else None
    size_tests[size] = {'status': status, 'returned': got, 'sec': round(dt, 2), 'bytes': nbytes}
    print(f'  size={size}: status={status} 取得={got}件 {dt:.1f}s {nbytes}B')
result['size_tests'] = size_tests

# ---- 2. from の上限 (深いページ) ----
print('== 2. from上限テスト ==')
from_tests = {}
for frm in (1000, 2500, 5000, 9900, 10000, 20000):
    status, data, dt, _ = call({'csid': 'hina-cross', 'keyword': KW, 'from': frm, 'size': 10})
    got = len(data.get('list', [])) if data else None
    hit = data.get('hit') if data else None
    from_tests[frm] = {'status': status, 'returned': got, 'hit': hit}
    print(f'  from={frm}: status={status} 取得={got} hit={hit}')
result['from_tests'] = from_tests

# ---- 3. ページネーション整合性 (100件×5ページ、重複・totalHits) ----
print('== 3. ページ重複・整合性テスト ==')
ids = []
hits_seen = set()
for page in range(5):
    status, data, dt, _ = call({'csid': 'hina-cross', 'keyword': KW,
                                'from': page * 100, 'size': 100})
    if not data:
        print(f'  page{page}: status={status} 取得失敗')
        continue
    hits_seen.add(data.get('hit'))
    ids += [it.get('id') for it in data.get('list', [])]
print(f'  総取得: {len(ids)}件, ユニークID: {len(set(ids))}件, '
      f'重複: {len(ids)-len(set(ids))}件, totalHitsの揺れ: {hits_seen}')
result['pagination'] = {'fetched': len(ids), 'unique': len(set(ids)),
                        'total_hits_values': sorted(hits_seen)}

# ---- 4. ソート指定の安定性 (rand以外が使えるか) ----
print('== 4. sortパラメータ ==')
sort_tests = {}
for sort in ('rand', 'score', 'title', 'new', 'old', ''):
    p = {'csid': 'hina-cross', 'keyword': KW, 'from': 0, 'size': 3}
    if sort:
        p['sort'] = sort
    status, data, dt, _ = call(p)
    first = data['list'][0]['id'] if data and data.get('list') else None
    sort_tests[sort or '(none)'] = {'status': status, 'first_id': first}
    print(f'  sort={sort or "(none)"}: status={status} first={first}')
result['sort_tests'] = sort_tests

# ---- 5. フィールド出現率 + ライセンス分布 (幅広いサンプル400件) ----
print('== 5. フィールド・ライセンス調査 ==')
field_count = Counter()
common_count = Counter()
rights_dist = Counter()
access_dist = Counter()
c_access_dist = Counter()
apitype_dist = Counter()
db_dist = Counter()
coord_have = 0
thumb_have = 0
sample_n = 0
samples = []
for kw in ('気仙沼 津波', '石巻', '陸前高田', '写真'):
    for page in range(2):
        status, data, dt, _ = call({'csid': 'hina-cross', 'keyword': kw,
                                    'from': page * 100, 'size': 50})
        if not data:
            continue
        for it in data.get('list', []):
            sample_n += 1
            for k in it.keys():
                # provider固有プレフィックスを正規化
                base = k.split('-', 1)[-1] if '-' in k and not k.startswith('common') else k
                field_count[base] += 1
            c = it.get('common', {})
            for k, v in c.items():
                if v not in (None, '', []):
                    common_count[k] += 1
            rights_dist[str(c.get('contentsRightsType'))] += 1
            access_dist[str(c.get('access'))] += 1
            c_access_dist[str(c.get('contentsAccess'))] += 1
            apitype_dist[str(c.get('apiType'))] += 1
            db_dist[str(c.get('database'))] += 1
            if c.get('coordinates'):
                coord_have += 1
            if c.get('thumbnailUrl'):
                thumb_have += 1
            if len(samples) < 3:
                samples.append(it)
print(f'  サンプル: {sample_n}件, 座標あり: {coord_have}, サムネあり: {thumb_have}')
result['field_stats'] = {
    'sample_n': sample_n,
    'common_fields': dict(common_count.most_common()),
    'item_fields_normalized': dict(field_count.most_common(40)),
    'rights_type': dict(rights_dist),
    'access': dict(access_dist),
    'contents_access': dict(c_access_dist),
    'api_type': dict(apitype_dist),
    'database': dict(db_dist.most_common(20)),
    'coordinates_have': coord_have,
    'thumbnail_have': thumb_have,
}
result['sample_items'] = samples

# ---- 6. facets 構造 (DB一覧・Yahoo系DBの有無) ----
print('== 6. facets (データベース一覧) ==')
status, data, dt, _ = call({'csid': 'hina-cross', 'keyword': '', 'from': 0, 'size': 1})
if data:
    result['total_all'] = data.get('hit')
    print('  全件 hit:', data.get('hit'))
    facets = data.get('facets')
    result['facets'] = facets
    if isinstance(facets, dict):
        for fk, fv in list(facets.items())[:10]:
            if isinstance(fv, list):
                print(f'  facet[{fk}]: {[str(x)[:40] for x in fv[:8]]}')
            else:
                print(f'  facet[{fk}]: {str(fv)[:150]}')

with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=1)
print(f'\n総リクエスト数: {N_REQ[0]}  ->  {OUT}')
