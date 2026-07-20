#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Yahoo画像を Google Cloud Storage へ直接ストリーム転送するCLI。

Macのディスクには一切書き込まず、メモリ上で
  Yahooストレージ → (バイナリ取得) → SHA256計算 → GCS blob アップロード
を1枚ずつ行う。SHA256はblobのカスタムメタデータとmanifestに記録する。

- リジューム: 起動時にバケット内の既存オブジェクト一覧を取得しスキップ
- リトライ: 取得/アップロードとも指数バックオフ (404は即failed記録)
- 失敗記録: gs://{bucket}/manifest/failed.csv に集約 (ローカルにも残す)
- manifest: gs://{bucket}/manifest/manifest-{timestamp}.jsonl を定期アップロード

実行例:
  python3 tools/stream_yahoo_images_gcs.py --bucket <name> --limit 10   # テスト
  python3 tools/stream_yahoo_images_gcs.py --bucket <name> --variants tn,sr
"""
import argparse
import csv
import hashlib
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'src'))

from google.cloud import storage  # noqa: E402

from hinagiku.api import USER_AGENT  # noqa: E402
from hinagiku.models import image_urls  # noqa: E402
from hinagiku.utils import setup_logger, read_jsonl, progress  # noqa: E402

DEFAULT_META = os.path.join(BASE, 'raw', 'hinagiku',
                            'yahoo_shinsai_normalized.jsonl.gz')
RETRY_BACKOFF = (5, 15, 45)


def fetch_bytes(url, timeout=60):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers.get('Content-Type', '')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--bucket', required=True)
    ap.add_argument('--meta', default=DEFAULT_META)
    ap.add_argument('--variants', default='tn,sr')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--throttle', type=float, default=1.0)
    ap.add_argument('--prefix', default='yahoo')
    args = ap.parse_args()

    logdir = os.path.join(BASE, 'raw', 'gcs_transfer')
    logger = setup_logger('gcs', os.path.join(logdir, 'transfer.log'))
    variants = set(args.variants.split(','))

    client = storage.Client()
    bucket = client.bucket(args.bucket)

    logger.info('既存オブジェクト一覧を取得中 (リジューム用)...')
    existing = {b.name for b in
                client.list_blobs(args.bucket, prefix=args.prefix + '/')}
    logger.info(f'既存: {len(existing)}オブジェクト')

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
                name = f'{args.prefix}/{pid}/{variant}.jpg'
                targets.append((pid, variant, url, name))
    todo = [t for t in targets if t[3] not in existing]
    logger.info(f'対象 {len(targets)}枚 / 転送済スキップ {len(targets)-len(todo)} '
                f'/ 今回転送 {len(todo)}枚')

    failed_local = os.path.join(logdir, 'failed.csv')
    manifest_rows = []
    stats = {'uploaded': 0, 'failed': 0}
    last = [0.0]

    def throttle():
        rest = args.throttle - (time.time() - last[0])
        if rest > 0:
            time.sleep(rest)
        last[0] = time.time()

    def record_failure(pid, variant, url, err):
        new = not os.path.exists(failed_local)
        with open(failed_local, 'a', encoding='utf-8', newline='') as f:
            w = csv.writer(f)
            if new:
                w.writerow(['photo_id', 'variant', 'url', 'error'])
            w.writerow([pid, variant, url, str(err)[:200]])
        stats['failed'] += 1

    def flush_manifest(final=False):
        if not manifest_rows:
            return
        ts = time.strftime('%Y%m%d-%H%M%S')
        name = f'manifest/manifest-{ts}.jsonl'
        buf = '\n'.join(json.dumps(r, ensure_ascii=False)
                        for r in manifest_rows) + '\n'
        bucket.blob(name).upload_from_string(buf,
                                             content_type='application/json')
        logger.info(f'manifest {len(manifest_rows)}行 -> gs://{args.bucket}/{name}')
        manifest_rows.clear()

    for pid, variant, url, name in progress(todo, total=len(todo),
                                            desc='GCS転送'):
        ok = False
        last_err = None
        for attempt, backoff in enumerate((0,) + RETRY_BACKOFF):
            if backoff:
                time.sleep(backoff)
            throttle()
            try:
                body, ctype = fetch_bytes(url)
                if not body or 'image' not in (ctype or ''):
                    raise ValueError(f'非画像応答 ({ctype}, {len(body or b"")}B)')
                sha = hashlib.sha256(body).hexdigest()
                blob = bucket.blob(name)
                blob.metadata = {'sha256': sha, 'source_url': url}
                blob.upload_from_file(io.BytesIO(body), size=len(body),
                                      content_type=ctype or 'image/jpeg',
                                      retry=storage.retry.DEFAULT_RETRY)
                manifest_rows.append({'name': name, 'url': url,
                                      'sha256': sha, 'bytes': len(body)})
                stats['uploaded'] += 1
                ok = True
                break
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code == 404:
                    break
            except Exception as e:  # GCS例外含む
                last_err = e
        if not ok:
            record_failure(pid, variant, url, last_err)
            logger.warning(f'失敗 {pid}/{variant}: {last_err}')
        if len(manifest_rows) >= 500:
            flush_manifest()

    flush_manifest(final=True)
    # failed.csv もバケットへ集約
    if os.path.exists(failed_local):
        bucket.blob('manifest/failed.csv').upload_from_filename(failed_local)
    logger.info(f'完了: {stats}')


if __name__ == '__main__':
    main()
