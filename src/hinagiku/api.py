# -*- coding: utf-8 -*-
"""ひなぎく検索APIクライアント。

実測仕様 (docs/hinagiku_api_survey.md):
  - GET https://kn.ndl.go.jp/api/item/search-so/hina-cross
  - size 最大500、from+size は2,000件窓で打ち切り
  - ソート不安定のためページ間重複あり → ID重複排除必須
  - 認証不要・JSON応答
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

ENDPOINT = 'https://kn.ndl.go.jp/api/item/search-so/hina-cross'
USER_AGENT = ('disaster-archive-hinagiku/1.0 '
              '(+https://github.com/yuto0101kondo/disaster-archive; '
              'contact: kondo20060101@gmail.com)')
MAX_SIZE = 500          # 実測: 501以上は空応答
WINDOW_LIMIT = 2000     # 実測: from+size がこれを超えると空応答
THROTTLE_SEC = 1.0
RETRY_BACKOFF = (30, 60, 120)


class HinagikuError(Exception):
    pass


def _default_fetch(url, timeout):
    req = urllib.request.Request(
        url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


class HinagikuClient:
    """スロットル・リトライつきAPIクライアント。

    fetch を差し替え可能にしてテスト時はモックを注入する。
    """

    def __init__(self, fetch=_default_fetch, throttle=THROTTLE_SEC,
                 timeout=60, logger=None):
        self.fetch = fetch
        self.throttle = throttle
        self.timeout = timeout
        self.logger = logger
        self._last = 0.0
        self.n_requests = 0

    def _log(self, msg):
        if self.logger:
            self.logger.info(msg)

    def _wait(self):
        rest = self.throttle - (time.time() - self._last)
        if rest > 0:
            time.sleep(rest)
        self._last = time.time()

    def search(self, keyword='', from_=0, size=MAX_SIZE, sort='new',
               filters=None):
        """1回の検索呼び出し。filters は {'f-db': '+yahoo_shinsai', ...} 形式
        (値はリスト可)。戻り値: パース済みJSON dict。"""
        if size > MAX_SIZE:
            raise ValueError(f'size は最大 {MAX_SIZE}')
        if from_ + size > WINDOW_LIMIT:
            raise ValueError(f'from+size は {WINDOW_LIMIT} まで')
        params = {'csid': 'hina-cross', 'keyword': keyword,
                  'from': from_, 'size': size}
        if sort:
            params['sort'] = sort
        for k, v in (filters or {}).items():
            params[k] = v
        url = ENDPOINT + '?' + urllib.parse.urlencode(params, doseq=True)

        last_err = None
        for attempt, backoff in enumerate((0,) + RETRY_BACKOFF):
            if backoff:
                self._log(f'retry {attempt}/3 in {backoff}s: {url}')
                time.sleep(backoff)
            self._wait()
            self.n_requests += 1
            try:
                status, body = self.fetch(url, self.timeout)
                if status != 200:
                    last_err = HinagikuError(f'HTTP {status}')
                    continue
                return json.loads(body)
            except (urllib.error.URLError, TimeoutError, OSError,
                    json.JSONDecodeError) as e:
                last_err = e
        raise HinagikuError(f'取得失敗: {url} ({last_err})')

    def count(self, keyword='', filters=None):
        """ヒット数だけ取得する (size=1)。"""
        return int(self.search(keyword, 0, 1, filters=filters).get('hit') or 0)

    def facet(self, key, keyword='', filters=None):
        """指定facetの {値: 件数} を返す。"""
        data = self.search(keyword, 0, 1, filters=filters)
        for f in data.get('facets') or []:
            if f.get('key') == key:
                return dict(f.get('counts') or {})
        return {}

    def fetch_window(self, keyword='', filters=None, sort='new',
                     on_page=None):
        """1つの検索条件の取得窓(最大2,000件)を全ページ取得し、
        ID重複を除去した dict {id: record} を返す。"""
        out = {}
        from_ = 0
        while from_ < WINDOW_LIMIT:
            size = min(MAX_SIZE, WINDOW_LIMIT - from_)
            data = self.search(keyword, from_, size, sort=sort,
                               filters=filters)
            items = data.get('list') or []
            for it in items:
                rid = it.get('id')
                if rid:
                    out[rid] = it
            if on_page:
                on_page(from_, len(items), int(data.get('hit') or 0))
            if len(items) < size:
                break
            from_ += size
        return out

    def harvest(self, filters, keyword='', slicers=None, sort='new',
                on_slice=None):
        """検索条件を再帰的にスライスして全件収穫する。

        slicers: [(パラメータ名, [値, ...]), ...] のリスト。
          hit が窓上限を超える場合、先頭の slicer で分割して再帰する。
          値でカバーしきれない残余は全値の除外(-値)で取得する。
        戻り値: {id: record}
        """
        slicers = list(slicers or [])
        total = self.count(keyword, filters)
        if total == 0:
            return {}
        if total <= WINDOW_LIMIT or not slicers:
            if total > WINDOW_LIMIT:
                self._log(f'警告: hit={total} が窓上限超過だが分割軸なし '
                          f'(keyword={keyword!r} filters={filters}) '
                          f'→ 先頭{WINDOW_LIMIT}件のみ')
            recs = self.fetch_window(keyword, filters, sort=sort)
            if on_slice:
                on_slice(keyword, filters, total, len(recs))
            return recs

        param, values = slicers[0]
        rest = slicers[1:]
        out = {}
        for v in values:
            f2 = dict(filters or {})
            f2[param] = _merge_filter(f2.get(param), '+' + v)
            out.update(self.harvest(f2, keyword, rest, sort, on_slice))
        # 残余 (どの値にも属さないレコード)
        f2 = dict(filters or {})
        f2[param] = _merge_filter(f2.get(param), ['-' + v for v in values])
        out.update(self.harvest(f2, keyword, rest, sort, on_slice))
        return out


def _merge_filter(existing, new):
    vals = []
    if existing:
        vals += existing if isinstance(existing, list) else [existing]
    vals += new if isinstance(new, list) else [new]
    return vals
