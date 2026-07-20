# 災害空間変遷アーカイブ・マップ

能登半島地震・東日本大震災の写真・記録をLeaflet地図と一覧で閲覧できるアーカイブサイト。

- 公開ページ: GitHub Pages (main ブランチの `index.html`)
- データ: `disaster_data_1〜4.js` (計53,320レコード、JS埋め込みJSON)
- データセット: `noto` (石川県公開データ) / `shinsai` (国交省) / `yahoo` (Yahoo!写真保存プロジェクト)

## 位置情報の設計

各レコードは `lat` / `lon` / `loc_precision` を持つ:

| loc_precision | 意味 | 地図表示 |
|---|---|---|
| `building` | 施設・番地・原典GPSレベルの正確な位置 | 青ピン |
| `area-centroid` | 大字・町丁目・市区町村の代表点 (概略位置) | 橙ピン |
| `uncertain` | 位置を特定できない (大半は座標なし) | 灰ピン (既定非表示) |

経緯: 初期データの座標はAI推定で誤差km級だったため、テキストからのゼロベース
再ジオコーディング → POI格上げ → ひなぎく原座標適用、と段階的に再構築している。

## ひなぎく統合 (Yahoo画像消滅対策)

Yahoo!アーカイブ閉鎖に備え、NDL東日本大震災アーカイブ「ひなぎく」を
yahooデータの正式メタデータソースとして統合している。
詳細: `docs/hinagiku_integration.md` / API調査: `docs/hinagiku_api_survey.md`

```bash
# メタデータ収穫 (yahoo_shinsai 全件 → raw/hinagiku/)
python3 tools/harvest_hinagiku.py [--dry-run]

# 画像保全 (images/yahoo/ へ。リジューム可)
python3 tools/download_yahoo_images.py --variants tn,sr [--limit 50] [--dry-run]

# 原座標・メタデータの適用 (ID完全一致)
python3 tools/apply_hinagiku_coords.py [--dry-run]

# 突合検証レポート生成
python3 tools/verify_hinagiku_mapping.py
```

共通ライブラリは `src/hinagiku/` (標準ライブラリのみ、tqdmは任意)。

## 開発

```bash
# ローカル表示
python3 -m http.server 8765   # → http://localhost:8765/index.html

# テスト (venv 推奨)
python3 -m venv .venv && .venv/bin/pip install pytest tqdm
.venv/bin/python -m pytest tests/ -q
```

## 主要ツール (tools/)

| スクリプト | 用途 |
|---|---|
| `rebuild_eastjapan.py` | 東日本データのテキストからのゼロベース再ジオコーディング |
| `upgrade_poi_pass.py` | 代表点どまりの施設spotをPOI実位置へ格上げ |
| `find_sea_points.py` / `repair_sea_points.py` | 海上ピンの検出と引き戻し |
| `snap_to_town_centroid.py` | Geolonia辞書による町丁目スナップ |
| `harvest_hinagiku.py` ほか | ひなぎく統合 (上記) |

## データ出典

- Yahoo! JAPAN 東日本大震災 写真保存プロジェクト
- 国土交通省 震災伝承館
- 石川県 令和6年能登半島地震 オープンデータ
- 国立国会図書館 東日本大震災アーカイブ (ひなぎく)
- 地図・航空写真: 国土地理院
