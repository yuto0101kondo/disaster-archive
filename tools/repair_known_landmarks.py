#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重要拠点の座標を正解値で強制上書きするパッチ。

背景: ひなぎく原座標(投稿者ジオタグ)は「撮影対象の場所」ではなく
「投稿者のいた場所」を指すことがあり、spot が重要拠点そのものを指す
レコードが遠隔地(例: 福島第一原発の写真が仙台の内陸)に置かれるケースがある。
また shinsai/noto 系のジオコーディング残誤差もある。

方針: spot が拠点名に完全一致相当でマッチし、かつ現座標が正解から
--threshold (既定1.5km) を超えて離れている(または座標なし)場合のみ、
検証済みの正解座標へ強制スナップする (loc_precision='building')。

正解座標の出典: OSM/GSI照合 (2026-07-20)。福島第一のみ現地GPS付き
写真3件以上の合意点を採用 (OSM/GSIに公式ポイントが無いため)。

実行:
  python3 tools/repair_known_landmarks.py --dry-run
  python3 tools/repair_known_landmarks.py
"""
import argparse
import json
import math
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (名称, lat, lon, spotマッチ用正規表現)
LANDMARKS = [
    ('福島第一原子力発電所', 37.421386, 141.032625,
     r'^(東京電力)?福島第[一1]原(子力)?発(電所)?$'),
    ('福島第二原子力発電所', 37.315136, 141.023996,
     r'^(東京電力)?福島第[二2]原(子力)?発(電所)?$'),
    ('石巻市立大川小学校', 38.546268, 141.428455,
     r'^(石巻市立)?大川小学校(跡地?|周辺)?$|^震災遺構\s*大川小学校$'),
    ('石巻市立門脇小学校', 38.421324, 141.304665,
     r'^(石巻市立)?門脇小学校(跡地?|周辺)?$|^震災遺構\s*門脇小学校$'),
    ('石ノ森萬画館', 38.429575, 141.310762, r'^石ノ森萬画館(周辺)?$'),
    ('南三陸町防災対策庁舎', 38.677791, 141.446368,
     r'^(南三陸町)?(旧)?防災対策庁舎(周辺)?$|^南三陸町防災庁舎$'),
    ('気仙沼向洋高校(旧校舎)', 38.831738, 141.590560,
     r'^(宮城県)?(旧)?気仙沼向洋高(等学)?校(周辺)?$'),
    ('仙台市立荒浜小学校', 38.222338, 140.980564,
     r'^(仙台市立)?荒浜小学校(周辺)?$|^震災遺構\s*荒浜小学校$'),
    ('山元町立中浜小学校', 37.917408, 140.919754,
     r'^(山元町立)?中浜小学校(周辺)?$'),
    ('奇跡の一本松', 39.003466, 141.625126,
     r'^奇跡の一本松(周辺)?$|^高田松原.{0,3}一本松$'),
    ('第18共徳丸(跡地)', 38.915943, 141.579672,
     r'^第?18共徳丸$|^第十八共徳丸$|^共徳丸(周辺)?$'),
    ('たろう観光ホテル', 39.737924, 141.975914, r'^たろう観光ホテル(周辺)?$'),
    ('大槌町役場', 39.358580, 141.899947, r'^(旧)?大槌町役場(旧庁舎)?(周辺)?$'),
    ('仙台港', 38.269364, 141.046747, r'^仙台(新)?港$'),
]


def dist_km(a, b, c, d):
    R = 6371
    la1, lo1, la2, lo2 = map(math.radians, (a, b, c, d))
    return R * math.acos(min(1, math.sin(la1) * math.sin(la2) +
                             math.cos(la1) * math.cos(la2) * math.cos(lo1 - lo2)))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--threshold', type=float, default=1.5,
                    help='この距離(km)以内なら現座標を尊重して触らない')
    args = ap.parse_args()

    compiled = [(name, lat, lon, re.compile(pat))
                for name, lat, lon, pat in LANDMARKS]
    n_fixed = 0
    per_lm = {}
    files = []
    for n in range(1, 5):
        path = os.path.join(BASE, f'disaster_data_{n}.js')
        with open(path, encoding='utf-8') as f:
            txt = f.read()
        prefix = txt[:txt.index('[')]
        data = json.loads(txt[txt.index('['):].rstrip().rstrip(';'))
        files.append((path, prefix, data))
        for d in data:
            spot = (d.get('spot') or '').strip()
            if not spot:
                continue
            for name, lat, lon, rx in compiled:
                if not rx.match(spot):
                    continue
                cur_ok = (d.get('has_coord')
                          and d.get('lat') is not None
                          and dist_km(d['lat'], d['lon'], lat, lon) <= args.threshold)
                if cur_ok:
                    break
                d['lat'], d['lon'] = round(lat, 6), round(lon, 6)
                d['has_coord'] = True
                d['loc_precision'] = 'building'
                n_fixed += 1
                per_lm[name] = per_lm.get(name, 0) + 1
                break

    print('拠点別修正件数:')
    for k, v in sorted(per_lm.items(), key=lambda x: -x[1]):
        print(f'  {k}: {v}件')
    print(f'合計: {n_fixed}件 (threshold={args.threshold}km)')
    if args.dry_run:
        print('(dry-run: データファイルは変更していません)')
        return
    for path, prefix, data in files:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(prefix + json.dumps(data, ensure_ascii=False,
                                        separators=(',', ':')) + ';')
        print('wrote', path)


if __name__ == '__main__':
    main()
