# -*- coding: utf-8 -*-
"""src/hinagiku の主要処理のテスト (APIはモック使用、実通信なし)。"""
import json
import os
import sys
import urllib.parse

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from hinagiku.api import HinagikuClient, WINDOW_LIMIT, MAX_SIZE
from hinagiku.mapping import our_id_to_photo_id, build_photo_index, match
from hinagiku.models import normalize_yahoo, image_urls
from hinagiku.download import ImageDownloader


# ---------- モックAPI ----------

def make_fake_fetch(records, facets=None):
    """records: [{'id':..., ...}] を検索窓仕様どおりに返す fetch モック。
    filters (f-db 等) は無視し、keyword='' 前提の全件データベースとして動く。"""
    def fetch(url, timeout):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        from_ = int(q.get('from', ['0'])[0])
        size = int(q.get('size', ['500'])[0])
        if from_ + size > WINDOW_LIMIT and from_ >= WINDOW_LIMIT:
            body = {'hit': 0, 'list': [], 'facets': facets or []}
        else:
            body = {'hit': len(records),
                    'list': records[from_:from_ + size],
                    'facets': facets or []}
        return 200, json.dumps(body).encode()
    return fetch


def test_search_respects_limits():
    client = HinagikuClient(fetch=make_fake_fetch([]), throttle=0)
    with pytest.raises(ValueError):
        client.search(size=MAX_SIZE + 1)
    with pytest.raises(ValueError):
        client.search(from_=WINDOW_LIMIT - 10, size=100)


def test_fetch_window_dedup_and_pagination():
    # 1,200件 + ページ間重複を混ぜる
    recs = [{'id': f'x-{i:05d}'} for i in range(1200)]
    # 重複を模擬: 2ページ目の先頭に1ページ目の要素を混入
    recs[500] = recs[0]
    client = HinagikuClient(fetch=make_fake_fetch(recs), throttle=0)
    out = client.fetch_window()
    assert len(out) == 1199          # 重複1件は除去される
    assert client.n_requests == 3    # 500+500+200


def test_count_and_facet():
    facets = [{'key': 'prefectures', 'counts': {'Miyagi': 10, 'Iwate': 5}}]
    client = HinagikuClient(fetch=make_fake_fetch([{'id': 'a'}] * 15, facets),
                            throttle=0)
    assert client.count() == 15
    assert client.facet('prefectures') == {'Miyagi': 10, 'Iwate': 5}
    assert client.facet('nonexistent') == {}


def test_retry_then_success():
    calls = {'n': 0}

    def flaky(url, timeout):
        calls['n'] += 1
        if calls['n'] < 2:
            return 500, b''
        return 200, json.dumps({'hit': 0, 'list': []}).encode()

    import hinagiku.api as api_mod
    orig = api_mod.RETRY_BACKOFF
    api_mod.RETRY_BACKOFF = (0, 0, 0)
    try:
        client = HinagikuClient(fetch=flaky, throttle=0)
        assert client.count() == 0
        assert calls['n'] == 2
    finally:
        api_mod.RETRY_BACKOFF = orig


# ---------- mapping ----------

def test_our_id_to_photo_id():
    assert our_id_to_photo_id('yahoo_55944_sr') == '55944'
    assert our_id_to_photo_id('yahoo_366') == '366'
    assert our_id_to_photo_id('noto_R07_022') is None
    assert our_id_to_photo_id('') is None


def test_build_index_and_match():
    recs = [{'photo_id': '1', 'title': 'a'},
            {'photo_id': '2', 'title': 'b'},
            {'photo_id': '2', 'title': 'dup'},   # 重複は先勝ち
            {'photo_id': None}]
    index, dup = build_photo_index(recs)
    assert set(index) == {'1', '2'}
    assert dup == 1
    assert index['2']['title'] == 'b'
    matched, unmatched = match(['yahoo_1_sr', 'yahoo_9_sr'], index)
    assert list(matched) == ['yahoo_1_sr']
    assert unmatched == ['yahoo_9_sr']


# ---------- models ----------

FIXTURE_ITEM = {
    'id': 'yahoo_shinsai-000029815',
    'common': {
        'title': '有備館の庭園',
        'coordinates': {'lat': 38.6577, 'lon': 140.8635},
        'location': ['宮城県 大崎市 岩出山'],
        'thumbnailUrl': ['https://example/55944_tn.jpg'],
        'linkUrl': 'https://example/55944.jpg',
        'contentsRightsType': 'others',
        'contentsAccess': 'internet',
    },
    'yahoo_shinsai-96-s': 55944,
    'yahoo_shinsai-70-d': '2010-04-30T22:44:30+09:00',
    'yahoo_shinsai-52-s': ['風景', '震災前'],
    'yahoo_shinsai-41-s': 'MT',
    'yahoo_shinsai-144-u': ['https://example/55944_sr.jpg',
                            'https://example/55944_sq.jpg'],
}


def test_normalize_yahoo():
    r = normalize_yahoo(FIXTURE_ITEM)
    assert r['photo_id'] == '55944'
    assert r['lat'] == 38.6577 and r['lon'] == 140.8635
    assert r['taken_at'].startswith('2010-04-30')
    assert r['tags'] == ['風景', '震災前']
    assert r['url_screen'] == 'https://example/55944_sr.jpg'
    assert r['url_thumbnail'] == 'https://example/55944_tn.jpg'
    assert r['url_full'] == 'https://example/55944.jpg'
    assert r['rights_type'] == 'others'
    urls = dict(image_urls(r))
    assert set(urls) == {'tn', 'sr', 'full'}


def test_normalize_coordinates_fallback():
    item = dict(FIXTURE_ITEM)
    item['common'] = {k: v for k, v in FIXTURE_ITEM['common'].items()
                      if k != 'coordinates'}
    item['yahoo_shinsai-Coordinates-l'] = '39.1,141.2'
    r = normalize_yahoo(item)
    assert r['lat'] == 39.1 and r['lon'] == 141.2


# ---------- download ----------

def test_downloader_success_skip_and_failure(tmp_path):
    def fake_fetch(url, timeout):
        if 'bad' in url:
            raise TimeoutError('boom')
        return b'\xff\xd8jpegdata', 'image/jpeg'

    import hinagiku.download as dl_mod
    orig = dl_mod.RETRY_BACKOFF
    dl_mod.RETRY_BACKOFF = (0, 0, 0)
    try:
        dl = ImageDownloader(str(tmp_path), throttle=0, fetch=fake_fetch)
        assert dl.download_one('1', 'tn', 'https://example/1_tn.jpg')
        assert dl.stats['downloaded'] == 1
        # 2回目はスキップ
        assert dl.download_one('1', 'tn', 'https://example/1_tn.jpg')
        assert dl.stats['skipped'] == 1
        # 失敗は failed.csv に記録
        assert not dl.download_one('2', 'tn', 'https://example/bad.jpg')
        assert dl.stats['failed'] == 1
        assert os.path.exists(dl.failed_path)
        # manifest に sha256 が記録される
        with open(dl.manifest_path, encoding='utf-8') as f:
            row = json.loads(f.readline())
        assert row['photo_id'] == '1' and len(row['sha256']) == 64
    finally:
        dl_mod.RETRY_BACKOFF = orig
