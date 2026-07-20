# -*- coding: utf-8 -*-
"""当アーカイブID <-> ひなぎく元写真ID の変換。

当アーカイブの yahoo レコードID:
  yahoo_{N}_sr  (35,939件) / yahoo_{N}  (30件)
ひなぎく側の突合キー:
  yahoo_shinsai-96-s フィールド (= 元写真ID N)

曖昧検索は行わない。ID完全一致のみ。
"""
import re

_OUR_ID_RE = re.compile(r'^yahoo_(\d+)(?:_sr)?$')


def our_id_to_photo_id(our_id):
    """'yahoo_55944_sr' → '55944'。yahoo以外・不正形式は None。"""
    m = _OUR_ID_RE.match(our_id or '')
    return m.group(1) if m else None


def build_photo_index(normalized_records):
    """正規化済みひなぎくレコード列 → {photo_id: record}。
    photo_id 重複時は先勝ち (重複は呼び出し側で検知可能なよう件数も返す)。"""
    index = {}
    dup = 0
    for r in normalized_records:
        pid = r.get('photo_id')
        if not pid:
            continue
        if pid in index:
            dup += 1
            continue
        index[pid] = r
    return index, dup


def match(our_ids, photo_index):
    """当アーカイブID列を突合し (matched: {our_id: rec}, unmatched: [our_id])。"""
    matched = {}
    unmatched = []
    for oid in our_ids:
        pid = our_id_to_photo_id(oid)
        rec = photo_index.get(pid) if pid else None
        if rec is not None:
            matched[oid] = rec
        else:
            unmatched.append(oid)
    return matched, unmatched
