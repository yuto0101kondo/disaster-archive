#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ひなぎくメタデータの全件収穫CLI。

県別(+年代グループ別)スライスで2,000件窓を回避しつつ、指定DBの全レコードを
収穫して raw/hinagiku/ に JSONL(gzip) で保存する。スライス単位で中断再開可能。

実行例:
  python3 tools/harvest_hinagiku.py --dry-run           # 件数とスライス計画のみ
  python3 tools/harvest_hinagiku.py                     # yahoo_shinsai 全件収穫
  python3 tools/harvest_hinagiku.py --db miyagi_shinsai # 他DBも収穫可能
"""
import argparse
import gzip
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'src'))

from hinagiku.api import HinagikuClient, WINDOW_LIMIT  # noqa: E402
from hinagiku.models import normalize_yahoo  # noqa: E402
from hinagiku.utils import setup_logger, write_jsonl, read_jsonl, progress  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db', default='yahoo_shinsai')
    ap.add_argument('--out-dir', default=os.path.join(BASE, 'raw', 'hinagiku'))
    ap.add_argument('--dry-run', action='store_true',
                    help='件数とスライス計画の表示のみ (取得しない)')
    ap.add_argument('--no-gzip', action='store_true')
    ap.add_argument('--throttle', type=float, default=1.0)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    logger = setup_logger('harvest',
                          os.path.join(args.out_dir, 'harvest.log'))
    client = HinagikuClient(throttle=args.throttle, logger=logger)
    base_filter = {'f-db': '+' + args.db}

    total = client.count(filters=base_filter)
    prefs = client.facet('prefectures', filters=base_filter)
    tempo = client.facet('tempo_group', filters=base_filter)
    logger.info(f'db={args.db} 総件数={total}')
    logger.info(f'県別: {dict(sorted(prefs.items(), key=lambda x:-x[1])[:10])} ...')
    over = [p for p, n in prefs.items() if n > WINDOW_LIMIT]
    logger.info(f'2,000件超の県 (年代グループで再分割): {over}')
    if args.dry_run:
        logger.info(f'年代グループ: {tempo}')
        logger.info('(dry-run: 取得は行いません)')
        return

    # スライス収穫 (県 → 年代グループ の順で再帰分割 + 各軸の残余スライス)
    slicers = [('f-prefectures', sorted(prefs, key=prefs.get, reverse=True)),
               ('f-tempo_group', sorted(tempo, key=tempo.get, reverse=True))]

    state_path = os.path.join(args.out_dir, f'{args.db}_state.jsonl')
    done_slices = set()
    records = {}
    if os.path.exists(state_path):
        for row in read_jsonl(state_path):
            if row.get('_slice_done'):
                done_slices.add(row['_slice_done'])
            elif row.get('id'):
                records[row['id']] = row
        logger.info(f'再開: 済スライス{len(done_slices)}件, 取得済{len(records)}件')

    state_f = open(state_path, 'a', encoding='utf-8')

    def on_slice(keyword, filters, hit, got):
        sig = json.dumps(filters, ensure_ascii=False, sort_keys=True)
        logger.info(f'slice hit={hit} got={got} {sig[:120]}')

    def harvest_with_state(filters, slicers):
        sig = json.dumps(filters, ensure_ascii=False, sort_keys=True)
        if sig in done_slices:
            return
        n = client.count(filters=filters)
        if n == 0:
            done_slices.add(sig)
            state_f.write(json.dumps({'_slice_done': sig}) + '\n')
            return
        if n <= WINDOW_LIMIT or not slicers:
            if n > WINDOW_LIMIT:
                logger.warning(f'窓超過スライス hit={n} {sig} → 先頭2,000件のみ')
            recs = client.fetch_window(filters=filters)
            for rid, r in recs.items():
                if rid not in records:
                    records[rid] = r
                    state_f.write(json.dumps(r, ensure_ascii=False) + '\n')
            state_f.flush()
            done_slices.add(sig)
            state_f.write(json.dumps({'_slice_done': sig}) + '\n')
            logger.info(f'slice完了 hit={n} 累計{len(records)}件 {sig[:100]}')
            return
        param, values = slicers[0]
        for v in values:
            f2 = dict(filters)
            f2[param] = (f2.get(param) if isinstance(f2.get(param), list)
                         else [f2[param]] if f2.get(param) else []) + ['+' + v]
            harvest_with_state(f2, slicers[1:])
        f2 = dict(filters)
        f2[param] = (f2.get(param) if isinstance(f2.get(param), list)
                     else [f2[param]] if f2.get(param) else []) + ['-' + v2 for v2 in values]
        harvest_with_state(f2, slicers[1:])

    harvest_with_state(base_filter, slicers)
    state_f.close()

    logger.info(f'収穫完了: {len(records)}件 (API呼び出し {client.n_requests}回) '
                f'/ 期待値 {total}件')

    suffix = '.jsonl' if args.no_gzip else '.jsonl.gz'
    raw_path = os.path.join(args.out_dir, f'{args.db}_meta{suffix}')
    n = write_jsonl(raw_path, progress(records.values(), total=len(records),
                                       desc='raw書き出し'),
                    gzip_out=not args.no_gzip)
    logger.info(f'raw: {raw_path} ({n}件)')

    if args.db == 'yahoo_shinsai':
        norm_path = os.path.join(args.out_dir, f'{args.db}_normalized{suffix}')
        n = write_jsonl(norm_path,
                        (normalize_yahoo(r) for r in records.values()),
                        gzip_out=not args.no_gzip)
        logger.info(f'normalized: {norm_path} ({n}件)')


if __name__ == '__main__':
    main()
