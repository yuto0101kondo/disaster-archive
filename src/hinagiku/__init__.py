"""ひなぎく (NDL東日本大震災アーカイブ) 統合パッケージ。

Yahoo!写真保存プロジェクトの正式メタデータソースとしてひなぎくを扱う:
  api.py     - 検索APIクライアント (スロットル・リトライ・スライス収穫)
  models.py  - レコードの正規化モデル
  mapping.py - 当アーカイブID <-> ひなぎくID の変換
  download.py- 画像保全ダウンローダ
  utils.py   - JSONL(gzip)入出力・ログ・進捗
"""
__version__ = '1.0.0'
