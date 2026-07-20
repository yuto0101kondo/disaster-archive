# -*- coding: utf-8 -*-
"""Yahoo画像の保全ダウンローダ。

保存レイアウト: images/yahoo/{photo_id}/{variant}.jpg
  variant: tn (サムネ) / sr (スクリーン) / full (原寸)
併せて images/yahoo/manifest.jsonl に {photo_id, variant, url, sha256, bytes}
を追記する。既存ファイル(サイズ>0)はスキップ = リジューム可能。
失敗は failed.csv (photo_id, variant, url, error) に記録する。
"""
import csv
import hashlib
import os
import time
import urllib.error
import urllib.request

from .api import USER_AGENT

RETRY_BACKOFF = (5, 15, 45)


def _fetch_bytes(url, timeout):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers.get('Content-Type', '')


class ImageDownloader:
    def __init__(self, out_dir, throttle=1.0, timeout=60,
                 fetch=_fetch_bytes, logger=None):
        self.out_dir = out_dir
        self.throttle = throttle
        self.timeout = timeout
        self.fetch = fetch
        self.logger = logger
        self._last = 0.0
        self.manifest_path = os.path.join(out_dir, 'manifest.jsonl')
        self.failed_path = os.path.join(out_dir, 'failed.csv')
        self.stats = {'downloaded': 0, 'skipped': 0, 'failed': 0}
        os.makedirs(out_dir, exist_ok=True)

    def _wait(self):
        rest = self.throttle - (time.time() - self._last)
        if rest > 0:
            time.sleep(rest)
        self._last = time.time()

    def _record_failure(self, photo_id, variant, url, error):
        new = not os.path.exists(self.failed_path)
        with open(self.failed_path, 'a', encoding='utf-8', newline='') as f:
            w = csv.writer(f)
            if new:
                w.writerow(['photo_id', 'variant', 'url', 'error'])
            w.writerow([photo_id, variant, url, str(error)[:200]])
        self.stats['failed'] += 1

    def _record_manifest(self, photo_id, variant, url, sha256, nbytes):
        import json
        with open(self.manifest_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'photo_id': photo_id, 'variant': variant,
                                'url': url, 'sha256': sha256,
                                'bytes': nbytes}, ensure_ascii=False) + '\n')

    def target_path(self, photo_id, variant):
        ext = 'jpg'
        return os.path.join(self.out_dir, str(photo_id), f'{variant}.{ext}')

    def download_one(self, photo_id, variant, url):
        """1画像を取得。既存(サイズ>0)ならスキップ。成功時 True。"""
        path = self.target_path(photo_id, variant)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            self.stats['skipped'] += 1
            return True
        last_err = None
        for attempt, backoff in enumerate((0,) + RETRY_BACKOFF):
            if backoff:
                time.sleep(backoff)
            self._wait()
            try:
                body, ctype = self.fetch(url, self.timeout)
                if not body or 'image' not in (ctype or ''):
                    raise ValueError(f'非画像応答 ({ctype}, {len(body or b"")}B)')
                os.makedirs(os.path.dirname(path), exist_ok=True)
                tmp = path + '.part'
                with open(tmp, 'wb') as f:
                    f.write(body)
                os.replace(tmp, path)
                sha = hashlib.sha256(body).hexdigest()
                self._record_manifest(photo_id, variant, url, sha, len(body))
                self.stats['downloaded'] += 1
                return True
            except (urllib.error.URLError, TimeoutError, OSError,
                    ValueError) as e:
                last_err = e
                if isinstance(e, urllib.error.HTTPError) and e.code == 404:
                    break  # 404はリトライしない
        self._record_failure(photo_id, variant, url, last_err)
        if self.logger:
            self.logger.warning(f'失敗 {photo_id}/{variant}: {last_err}')
        return False
