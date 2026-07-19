# ひなぎく(NDL東日本大震災アーカイブ)API 調査レポート

調査日: 2026-07-20 / 調査手段: ローカルMacからの実測 (計約40リクエスト、1秒間隔)
検証コード: `tools/test_hinagiku_api.py`, `tools/hinagiku_survey.py`
生データ: `tools/hinagiku_survey_result.json`

## 結論サマリ

1. **公式ドキュメント記載の旧API (`/api/opensearch`) は廃止済み**(404)。リニューアル後の
   検索画面が使う内部API `GET /api/item/search-so/hina-cross` が認証不要で利用できる。
2. **ひなぎくには Yahoo!写真保存プロジェクトのDB `yahoo_shinsai` が47,224件収録済み**。
   当アーカイブのyahooデータ(35,969件)を上回る規模で、**元写真ID・原座標(GPS精度)・
   撮影日時が保存されている**。ただし画像URL自体はYahooストレージを指しており、
   Yahoo閉鎖と同時にリンク切れになる。**画像本体の保全には別途ダウンロードが必要**。
3. 副産物として、`yahoo_shinsai` の**原座標(99.9%カバレッジ)を当アーカイブに取り込めば、
   テキスト由来で市区町村レベルに留まっている現在の座標を原点のGPS精度に置き換えられる**。
   これは位置精度問題の最終解になりうる。

---

## 1. API仕様

```
GET https://kn.ndl.go.jp/api/item/search-so/hina-cross
```

| パラメータ | 説明 | 実測値 |
|---|---|---|
| `csid` | 固定 `hina-cross` (横断検索) | 必須 |
| `keyword` | 検索語 (URLエンコード、空も可=全件) | 空で hit=3,736,872 |
| `from` | オフセット | **from+size ≦ 2,000 の窓制限** |
| `size` | 件数 | **最大500** (1000は空応答) |
| `sort` | 並び順 | `rand` / `new` / `old` / `title` / `score` を確認 |
| `f-db` | データベース絞込 | `+yahoo_shinsai` / `-caportal` 形式 (+= include, -= exclude) |
| `f-prefectures` | 都道府県絞込 | `+Miyagi` 等 (英語名) |

- 認証: 不要。レスポンス: JSON (`{facets, from, hit, list}`)
- `hit` = 総ヒット数、`list` = アイテム配列、`facets` = 集計
- HTTPS必須。応答は高速(500件で約0.2〜1秒)

## 2. フィールド一覧

各アイテムは `common`(全DB共通の正規化ブロック) + `<db名>-*`(提供元固有) の二層構造。

### common ブロック (主要フィールド、サンプル400件での充足率)

| フィールド | 内容 | 充足率 |
|---|---|---|
| `id` | 一意ID (`<db>-<提供元ID>` 形式、例 `yahoo_shinsai-000029815`) | 100% |
| `title` | タイトル | ~100% |
| `database` | 提供DB (`yahoo_shinsai`, `miyagi_shinsai`, `kahoku` 等) | 100% |
| `thumbnailUrl` | サムネイルURL (配列) | 92% |
| `contentsUrl` | コンテンツURL (配列) | ~90% |
| `linkUrl` | 元資料へのリンク | ~100% |
| `coordinates` | `{lat, lon}` | 79% (yahoo_shinsaiは**99.9%**) |
| `location` | 場所テキスト (例「宮城県 大崎市 岩出山」) | 高 |
| `temporal` | 日付配列 [撮影日, 登録日] | 高 (yahoo_shinsaiは100%) |
| `contentsRightsType` | 権利区分 | 100% (サンプルは全て `others`) |
| `contentsAccess` | `internet` / `not_exist` 等 | 100% |
| `access` | `PUBLIC` 等 | 100% |
| `provider` / `ownerOrg` | 提供機関 | 100% |

### yahoo_shinsai 固有フィールド (番号キー)

| キー | 内容 |
|---|---|
| `yahoo_shinsai-96-s` | **元写真ID (例 55944)** — 当アーカイブの `yahoo_55944_sr` と直結 |
| `yahoo_shinsai-Coordinates-l` | **原座標 (GPS、小数8桁)** |
| `yahoo_shinsai-70-d` | 撮影日時 (ISO8601) |
| `yahoo_shinsai-52-s` | タグ (風景/震災前/震災後/人物 等) |
| `yahoo_shinsai-41-s` | 投稿者名 |
| `yahoo_shinsai-144-u` | 画像URL群 (_sr スクリーン画像 / スクエア画像) |
| `yahoo_shinsai-158-u` / `HarvestURL-u` | 原寸画像URL |
| `yahoo_shinsai-ThumbnailURL-u` | サムネ (_tn) |
| `yahoo_shinsai-177-s` | 撮影場所テキスト |

## 3. ページネーション仕様 (実測)

- `size` 上限 **500** (1000指定は28バイトの空応答)
- `from + size` の取得窓は **2,000件で打ち切り** (from=1990→OK, from=2000→hit=0の空応答)
- **totalHits(hit)は安定** (5ページ取得で揺れなし)
- **ページ間重複あり**: デフォルトソートで500件中54重複、`sort=new` でも22重複
  → ソートは完全に安定ではない。**クライアント側のID重複排除が必須**
- レート制限: 1秒間隔・30リクエスト連続で拒否・遅延なし (明示的な制限は未観測)

### 2,000件窓の回避策 (スライシング)

hitが2,000を超えるクエリは条件分割で窓内に収める:
1. `f-db` でDB単位に分割 (`yahoo_shinsai` は47,224件なのでさらに分割必要)
2. `f-prefectures` で県別に分割
3. それでも超える場合は `keyword` に市町村名を付与、または `sort=old`/`sort=new` の
   両端から取得して突き合わせる
4. facetsの件数で事前に各スライスのhitを確認してから取得

## 4. ライセンス整理

- facets `rights` / `caccess`、`contentsRightsType` はサンプル全件 `others`
  (CC等の明示ライセンスではない)
- `contentsAccess`: `internet`(ウェブ閲覧可) 89% / `not_exist`(メタデータのみ) / `restricted`
- ひなぎく全体では contents facet: text 240万 / **image 119万** / thumb 136万 / iiif 1.1万
- **yahoo_shinsai の画像はNDLがホストしておらず、Yahooストレージへの直リンク**。
  権利は各投稿者・ヤフー株式会社に帰属 (`others`)。閲覧区分は「ウェブ公開」
- 実務整理:
  - メタデータ(タイトル・座標・撮影日): ひなぎくAPIから取得可、保存・利用可能
  - 画像本体: Yahoo閉鎖で消滅予定。私的保存・アーカイブ目的のダウンロードは
    技術的に可能だが、再公開の権利関係は投稿者に帰属するため要検討
    (現サイトが既にYahoo画像を直リンク表示している構図と同等の扱いが安全)

## 5. Yahoo代替としての評価

| 観点 | 評価 |
|---|---|
| メタデータの継続性 | ◎ NDLが恒久保存。閉鎖後もタイトル・座標・撮影日は残る |
| 画像の継続性 | ✗ URLはYahooストレージ直リンク。**閉鎖と同時に全滅** |
| 突合キー | ◎ `yahoo_shinsai-96-s`(元写真ID) = 当アーカイブのID (`yahoo_{ID}_sr`) と**機械的に1:1対応** |
| 座標品質 | ◎ 原座標99.9%カバレッジ・GPS精度(小数8桁)。現在のテキスト由来座標より圧倒的に高品質 |
| 網羅性 | ◎ 47,224件 > 当アーカイブyahoo 35,969件 |

**突合設計**: 座標・タイトル・撮影日での曖昧マッチは不要。
当アーカイブのID `yahoo_{N}_sr` の `N` と `yahoo_shinsai-96-s` が直接一致するため、
**ID完全一致で突合できる**(画像URL末尾の番号でも同じ)。

## 6. 実装時の推奨事項

- User-Agent: 連絡先入りの固定UA (例: `disaster-archive-harvester/1.0 (contact: ...)`)
- レート: 1リクエスト/秒以上の間隔。夜間バッチ推奨。並列化しない
- キャッシュ: 取得済みスライスはJSONで永続化し再実行時はスキップ (中断再開可能に)
- リトライ: HTTP 5xx/タイムアウトは指数バックオフ (30s→60s→120s、3回で断念しログ)
- 空応答 (`hit=0` かつ窓内のはず) は異常とみなし再試行
- ID重複排除を常時実施 (ソート不安定のため)
- 画像ダウンロードは別フェーズ・別スロットル (Yahooストレージ側への配慮。
  1秒間隔で47,224件 ≒ 13時間 + サムネ・スクリーン画像)

## 7. Python収集設計案

```
tools/harvest_hinagiku.py  (メタデータ収集)
  1. facetsで f-prefectures ごとの件数を取得
  2. 県別スライス (>2,000件の県は keyword=市町村名でさらに分割)
  3. 各スライスを sort=new, size=500 で from=0..1500 まで取得
  4. id をキーに dict へマージ (重複排除)
  5. tools/hinagiku_yahoo_meta.json に保存 (中断再開はスライス単位)
  6. 完了後、当アーカイブIDと突合したマッピング表を出力

tools/download_yahoo_images.py  (画像保全・別フェーズ)
  1. ハーベスト済みメタデータから画像URL一覧 (_tn / _sr / 原寸)
  2. images/yahoo/{photo_id}/ へ保存、1秒間隔、Content-Type検証
  3. 進捗・失敗リストを記録し再開可能に
  4. 完了後、index.html の画像参照をローカル(または自前ホスティング)へ
     切り替えるオプションを実装

tools/apply_hinagiku_coords.py  (副産物: 座標の原典置換)
  1. 突合表から当アーカイブyahooレコードに原座標・撮影日を適用
  2. loc_precision='building' 相当の新区分 (原典GPS) を導入検討
```

## 付記

- OAI-PMH等のハーベスト専用APIは既知のパスでは発見できず (404)。
  必要なら NDL への利用申請 (公式にはAPI利用は申請制) を検討
- 内部APIのため予告なく仕様変更されうる。収集は早めに実施することを推奨
