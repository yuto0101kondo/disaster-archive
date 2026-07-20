# ひなぎく統合ガイド (Yahoo!アーカイブ閉鎖対策)

## 目的

Yahoo!写真保存プロジェクトの閉鎖に備え、NDL東日本大震災アーカイブ「ひなぎく」を
yahooデータの正式メタデータソースとして統合する。

1. 全メタデータをひなぎくから再取得可能にする (恒久保存はNDL側)
2. 座標を原典GPS精度に更新する (ひなぎくが原座標を99.9%保持)
3. 画像本体を閉鎖前にローカル保全できる状態にする (画像URLはYahooストレージ
   直リンクのため閉鎖で消滅する)

## データフロー

```
ひなぎくAPI (kn.ndl.go.jp/api/item/search-so/hina-cross)
   │  tools/harvest_hinagiku.py   県別+年代別スライス収穫 (1req/秒)
   ▼
raw/hinagiku/yahoo_shinsai_meta.jsonl.gz         … API生レコード
raw/hinagiku/yahoo_shinsai_normalized.jsonl.gz   … 正規化済み (photo_id付き)
   │
   ├─ tools/download_yahoo_images.py → images/yahoo/{photo_id}/{tn,sr,full}.jpg
   │     (SHA256 manifest / failed.csv / リジューム可)
   │
   └─ tools/apply_hinagiku_coords.py → disaster_data_1〜4.js を更新
         ・lat/lon = 原座標 (loc_precision='building')
         ・taken_at / title / tags / author / provider / rights_* を付与
   │
   ▼
tools/verify_hinagiku_mapping.py → docs/hinagiku_mapping_report.md
```

## ID突合 (曖昧検索禁止)

- 当アーカイブ: `yahoo_{N}_sr` または `yahoo_{N}`
- ひなぎく: `yahoo_shinsai-96-s` フィールド = 元写真ID `N`
- `src/hinagiku/mapping.py` の正規表現でIDから `N` を抽出し **完全一致のみ** で対応付ける

## 実行方法

```bash
# 1. 収穫 (約47,000件 / 20〜30分 / 中断再開可)
python3 tools/harvest_hinagiku.py --dry-run    # スライス計画の確認
python3 tools/harvest_hinagiku.py

# 2. 画像保全 (variants: tn=サムネ, sr=スクリーン, full=原寸)
python3 tools/download_yahoo_images.py --dry-run --variants tn,sr
python3 tools/download_yahoo_images.py --limit 50 --variants tn,sr   # サンプル
python3 tools/download_yahoo_images.py --variants tn,sr              # 全件

# 3. メタデータ適用 (disaster_data_*.js を書き換える)
python3 tools/apply_hinagiku_coords.py --dry-run
python3 tools/apply_hinagiku_coords.py

# 4. 検証レポート
python3 tools/verify_hinagiku_mapping.py
```

## 注意事項

- **APIは内部API** (公式ドキュメントの旧APIは廃止済み)。予告なく変更されうるため
  収穫は早めに。仕様変更時は `src/hinagiku/api.py` の定数を更新する
- **レート**: 1リクエスト/秒厳守。並列化しない。User-Agentに連絡先を明示
- **2,000件窓**: `from+size ≦ 2000`。超えるクエリは県別 (`f-prefectures`) →
  年代別 (`f-tempo_group`) で自動分割される
- **重複**: ソートが不安定なためページ間重複が出る。ID重複排除は実装済み
- **ライセンス**: `contentsRightsType` は `others` (明示CCなし)。権利は投稿者・
  ヤフー株式会社に帰属。収集・保全のみ行い、rights情報をレコードに保存する。
  保全画像の再公開は別途権利判断が必要
- **画像量の目安**: tn+sr 全件で約8〜10GB / 原寸込みで数十GB。`--variants` で調整
- raw/ と images/ は gitignore 対象 (リポジトリにはコミットしない)
