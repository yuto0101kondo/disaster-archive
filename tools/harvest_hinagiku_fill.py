#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""harvest_hinagiku.py の補完収穫。

実測で判明した2つの取りこぼしを回収する:
  A. 窓超過スライス (県×2011年など >2,000件) を市町村名キーワードで再分割。
     location テキストは「宮城県 石巻市 …」形式でほぼ必ず市町村名を含むため、
     県内全市町村名のキーワードスライスで実質全量をカバーできる。
     さらに >2,000 の市 (石巻・仙台等) は区名・町名キーワードで再々分割。
  B. ページ重複によるスライス内の取りこぼし (~2-3%) は、ソート順を変えた
     マルチパス取得 (new→old→title→score) で取り切る。
  C. それでも欠ける「当アーカイブ対象の写真ID」は、写真IDキーワード検索
     (96-s 完全一致検証つき) で個別回収する。

実行:
  python3 tools/harvest_hinagiku_fill.py
"""
import argparse
import csv
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'src'))

from hinagiku.api import HinagikuClient, WINDOW_LIMIT  # noqa: E402
from hinagiku.models import normalize_yahoo  # noqa: E402
from hinagiku.mapping import our_id_to_photo_id  # noqa: E402
from hinagiku.utils import setup_logger, read_jsonl, write_jsonl  # noqa: E402

OUT_DIR = os.path.join(BASE, 'raw', 'hinagiku')
STATE = os.path.join(OUT_DIR, 'yahoo_shinsai_state.jsonl')
PREF_JA = {'Miyagi': '宮城県', 'Iwate': '岩手県', 'Fukushima': '福島県'}
SORTS = ('new', 'old', 'title', 'score')


def munis_of(pref_ja, csv_path):
    out = []
    seen = set()
    with open(csv_path, encoding='utf-8') as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            if row[1] != pref_ja:
                continue
            city = re.sub(r'^.+?郡', '', row[5])
            city = re.sub(r'^(.+?市).+?区$', r'\1', city)
            if city not in seen:
                seen.add(city)
                out.append(city)
    return out


def multi_pass(client, logger, keyword, filters, hit):
    """ソート順を変えながら取り切るまで反復取得。"""
    out = {}
    for s in SORTS:
        got = client.fetch_window(keyword=keyword, filters=filters, sort=s)
        before = len(out)
        out.update(got)
        logger.info(f'  pass sort={s}: +{len(out)-before} (計{len(out)}/{hit}) '
                    f'kw={keyword!r}')
        if len(out) >= hit:
            break
    return out


def slice_by_keywords(client, logger, filters, keywords, records, depth=0):
    """キーワード群でスライスし multi_pass で回収。>2,000のキーワードは
    タグ語 (震災前/震災後 等) でさらに分割する。"""
    for kw in keywords:
        hit = client.count(keyword=kw, filters=filters)
        if hit == 0:
            continue
        if hit > WINDOW_LIMIT and depth == 0:
            for sub in ('震災前', '震災後', '風景', '人物'):
                sub_kw = f'{kw} {sub}'
                slice_by_keywords(client, logger, filters, [sub_kw],
                                  records, depth=1)
            # サブタグに合致しない残余はマルチパスで可能な限り回収
            got = multi_pass(client, logger, kw, filters, hit)
            n_new = sum(1 for k in got if k not in records)
            records.update(got)
            logger.info(f'slice kw={kw!r} hit={hit} (窓超過, タグ分割+残余) '
                        f'新規{n_new} 累計{len(records)}')
            continue
        got = multi_pass(client, logger, kw, filters, hit)
        n_new = sum(1 for k in got if k not in records)
        records.update(got)
        logger.info(f'slice kw={kw!r} hit={hit} 新規{n_new} 累計{len(records)}')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--csv', default=os.path.join(BASE, 'tools', 'latest.csv'))
    ap.add_argument('--skip-per-id', action='store_true')
    args = ap.parse_args()

    logger = setup_logger('fill', os.path.join(OUT_DIR, 'fill.log'))
    client = HinagikuClient(logger=logger)

    records = {}
    if os.path.exists(STATE):
        for row in read_jsonl(STATE):
            if row.get('id'):
                records[row['id']] = row
    logger.info(f'既存収穫: {len(records)}件')
    state_f = open(STATE, 'a', encoding='utf-8')

    def persist(new_ids):
        for rid in new_ids:
            state_f.write(json.dumps(records[rid], ensure_ascii=False) + '\n')
        state_f.flush()

    # A+B: 窓超過スライスの市町村キーワード再分割
    oversized = [('Miyagi', '2011'), ('Miyagi', '2010'),
                 ('Iwate', '2011'), ('Fukushima', '2011')]
    for pref, year in oversized:
        filters = {'f-db': '+yahoo_shinsai', 'f-prefectures': '+' + pref,
                   'f-tempo_group': '+' + year}
        keywords = munis_of(PREF_JA[pref], args.csv)
        logger.info(f'== {pref}×{year}: {len(keywords)}市町村で再分割 ==')
        before = set(records)
        slice_by_keywords(client, logger, filters, keywords, records)
        persist(set(records) - before)

    # B': 取りこぼし対策として全県の再マルチパス (小スライスの2-3%回収)
    #     → 件数の多い上位県のみ年単位で追加パス
    total = client.count(filters={'f-db': '+yahoo_shinsai'})
    logger.info(f'A/B後: {len(records)}/{total}')

    # C: 当アーカイブ対象の写真IDで個別回収
    if not args.skip_per_id:
        harvested_pids = {normalize_yahoo(r).get('photo_id')
                          for r in records.values()}
        ours = []
        for n in range(1, 5):
            with open(os.path.join(BASE, f'disaster_data_{n}.js'),
                      encoding='utf-8') as f:
                txt = f.read()
            ours += [d['id'] for d in
                     json.loads(txt[txt.index('['):].rstrip().rstrip(';'))
                     if d['dataset'] == 'yahoo']
        missing = [our_id_to_photo_id(o) for o in ours]
        missing = sorted({p for p in missing if p and p not in harvested_pids},
                         key=int)
        logger.info(f'C: 当アーカイブ対象の未収穫写真ID {len(missing)}件を個別回収')
        found = 0
        for i, pid in enumerate(missing):
            try:
                data = client.search(keyword=pid, size=100,
                                     filters={'f-db': '+yahoo_shinsai'})
            except Exception as e:
                logger.warning(f'  pid={pid} 検索失敗: {e}')
                continue
            for it in data.get('list') or []:
                if str(it.get('yahoo_shinsai-96-s') or '') == pid:
                    rid = it.get('id')
                    if rid and rid not in records:
                        records[rid] = it
                        persist([rid])
                        found += 1
                    break
            if (i + 1) % 200 == 0:
                logger.info(f'  per-id {i+1}/{len(missing)} 回収{found}')
        logger.info(f'C完了: {found}/{len(missing)} 回収')

    state_f.close()
    logger.info(f'最終収穫: {len(records)}/{total}')
    write_jsonl(os.path.join(OUT_DIR, 'yahoo_shinsai_meta.jsonl.gz'),
                records.values())
    write_jsonl(os.path.join(OUT_DIR, 'yahoo_shinsai_normalized.jsonl.gz'),
                (normalize_yahoo(r) for r in records.values()))
    logger.info('raw/normalized を再書き出ししました')


if __name__ == '__main__':
    main()
