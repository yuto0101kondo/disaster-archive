#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Yahoo画像の保全ダウンロードCLI。

収穫済みメタデータ (raw/hinagiku/yahoo_shinsai_normalized.jsonl.gz) から
画像URLを取り出し、images/yahoo/{photo_id}/{variant}.jpg へ保存する。
既存ファイルはスキップ (リジューム可)。SHA256 を manifest.jsonl に記録し、
失敗は failed.csv に記録する。

実行例:
  python3 tools/download_yahoo_images.py --dry-run            # 対象数の確認のみ
  python3 tools/download_yahoo_images.py --limit 50           # サンプル50件
  python3 tools/download_yahoo_images.py --variants tn,sr     # サムネ+スクリーン全件
"""
import argparse
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'src'))

from hinagiku.download import ImageDownloader  # noqa: E402
from hinagiku.models import image_urls  # noqa: E402
from hinagiku.utils import setup_logger, read_jsonl, progress  # noqa: E402

DEFAULT_META = os.path.join(BASE, 'raw', 'hinagiku',
                            'yahoo_shinsai_normalized.jsonl.gz')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--meta', default=DEFAULT_META)
    ap.add_argument('--out-dir', default=os.path.join(BASE, 'images', 'yahoo'))
    ap.add_argument('--variants', default='tn,sr',
                    help='取得する画像種別 (tn,sr,full のカンマ区切り)')
    ap.add_argument('--limit', type=int, default=0,
                    help='取得するレコード数上限 (0=全件)')
    ap.add_argument('--throttle', type=float, default=1.0)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    variants = set(args.variants.split(','))
    logger = setup_logger('imgdl', os.path.join(args.out_dir, 'download.log'))
    records = list(read_jsonl(args.meta))
    if args.limit:
        records = records[:args.limit]

    targets = []
    for rec in records:
        pid = rec.get('photo_id')
        if not pid:
            continue
        for variant, url in image_urls(rec):
            if variant in variants:
                targets.append((pid, variant, url))
    logger.info(f'対象レコード {len(records)}件 / 画像 {len(targets)}枚 '
                f'(variants={sorted(variants)})')
    if args.dry_run:
        est_min = len(targets) * max(args.throttle, 0.5) / 60
        logger.info(f'(dry-run) 推定所要時間: 約{est_min:.0f}分')
        return

    dl = ImageDownloader(args.out_dir, throttle=args.throttle, logger=logger)
    for pid, variant, url in progress(targets, total=len(targets), desc='画像取得'):
        dl.download_one(pid, variant, url)
    logger.info(f'完了: {dl.stats}')


if __name__ == '__main__':
    main()
