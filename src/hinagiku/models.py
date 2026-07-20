# -*- coding: utf-8 -*-
"""ひなぎくAPIの生レコードを扱いやすい正規化モデルに変換する。"""


def _first(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _aslist(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def normalize_yahoo(item):
    """yahoo_shinsai の生レコード → 正規化 dict。

    photo_id が当アーカイブID (yahoo_{photo_id}_sr) との突合キー。
    ライセンス・権利情報は収集のみ (rights_* に保存)。
    """
    c = item.get('common') or {}
    coords = c.get('coordinates') or {}
    lat = coords.get('lat')
    lon = coords.get('lon')
    if lat is None:
        latlon = item.get('yahoo_shinsai-Coordinates-l') or ''
        if isinstance(latlon, str) and ',' in latlon:
            try:
                lat, lon = (float(x) for x in latlon.split(',', 1))
            except ValueError:
                lat = lon = None
    return {
        'hinagiku_id': item.get('id'),
        'photo_id': str(item.get('yahoo_shinsai-96-s') or '') or None,
        'title': c.get('title') or _first(item.get('yahoo_shinsai-12-s')),
        'lat': lat,
        'lon': lon,
        'taken_at': _first(item.get('yahoo_shinsai-70-d')) or _first(c.get('temporal')),
        'tags': _aslist(item.get('yahoo_shinsai-52-s')),
        'author': item.get('yahoo_shinsai-41-s'),
        'provider': item.get('yahoo_shinsai-4-s') or 'ヤフー株式会社',
        'location_text': _first(c.get('location')) or item.get('yahoo_shinsai-177-s'),
        'url_thumbnail': _first(c.get('thumbnailUrl')) or item.get('yahoo_shinsai-ThumbnailURL-u'),
        'url_screen': _screen_url(item),
        'url_full': c.get('linkUrl') or item.get('yahoo_shinsai-158-u'),
        'mime': item.get('yahoo_shinsai-90-s'),
        'rights_type': c.get('contentsRightsType'),
        'rights_access': c.get('contentsAccess'),
        'publication_status': item.get('yahoo_shinsai-PublicationStatus-c'),
    }


def _screen_url(item):
    """144-u の画像URL群から _sr (スクリーン画像) を選ぶ。"""
    for u in _aslist(item.get('yahoo_shinsai-144-u')):
        if isinstance(u, str) and '_sr' in u:
            return u
    c = item.get('common') or {}
    for u in _aslist(c.get('contentsUrl')):
        if isinstance(u, str) and '_sr' in u:
            return u
    return None


def image_urls(rec):
    """正規化レコードから保全対象の画像URL一覧 [(variant, url), ...]。"""
    out = []
    for variant, key in (('tn', 'url_thumbnail'), ('sr', 'url_screen'),
                         ('full', 'url_full')):
        u = rec.get(key)
        if u:
            out.append((variant, u))
    return out
