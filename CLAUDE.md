# せどり自動化

概要: ブランド古着せどりの作業（仕入れ先の新着監視・相場照合・利益商品の発見・購入記録）をAIとGitHub Actionsで自動化する。

- 仕入れ先（KINDAL・トレファク・BRING・RINKAN・おふもーる）の新着商品を自動で監視する
- メルカリ内の出品も定期的にスキャンし、安く出ている商品を探す
- メルカリの売却実例（相場DB）と写真をAI（CLIP・DINOv2）で見比べて、利益が出そうな商品だけを残す
- 見つけたら Discord に通知し、スマホで受け取る
- 実際に仕入れた商品は購入記録に残し、週1回まとめて Discord に報告する

GitHubリポジトリ: https://github.com/ryosugihara/sedori （作業ブランチ: claude/adoring-bell-7rqznj。**Actions もこのブランチへ自動コミットする**）

## フォルダ構成
- `.github/workflows/`: GitHub Actionsの自動実行設定（**実際に動く唯一の正**）
- `せどり/sedori.md`: プロジェクトの説明ドキュメント
- `せどり/壁打ち/`: アイデア出し・要件検討のメモ（README.md・メモ.txt）。個人の連絡先・住所・取引相手の情報は書かない
- `せどり/コーディング/scripts/`: 実際に動くPythonプログラム
  - 本体: `monitor.py`（新着監視＋Discord通知。他のスクリプトからも通知・保存部品として再利用される）
  - サイト読み取り部品: `trefac.py` / `rinkan.py` / `hardoff.py`（サイトごとに1ファイル）
  - メルカリ: `mercari.py`（検索部品）/ `mercari_scan.py`（仕入れスキャン）/ `mercari_test.py`
  - 画像判定: `souba_match.py` / `fingerprint.py` / `geom_verify.py` / `verify_ai.py`（最終確認AI）
  - 相場DB: `collect_souba.py`（収集）/ `db_release.py`（Releasesへの保存・取得）/ `upgrade_embeddings.py` / `discover_brands.py`
  - 購入記録: `purchases.py`
  - 調整・テスト用: `calibrate*.py` / `clip_test.py` / `color_check.py` / `demo_*.py` / `test_*.py` / `recon.py`
- `せどり/コーディング/workflows/`: ワークフローの参照用コピー（古い場合がある。**読む時も必ず `.github/workflows/` を見る**）
- `せどり/データ/`: 収集・加工したデータ
  - `watchlists/`: 監視対象ブランド（`watch_*.json`）・相場（`souba.json`）・除外条件（`exclude.json`）。**人が編集する唯一のデータフォルダで、設定の正**
  - `state/`: 各サイトの「見た商品」記録（`*_seen.json`）・送信済み記録・購入記録（`purchases.json`）。**Actionsが自動更新・コミットする。手で編集しない**
  - `data/`: 相場データ（`mercari_souba.json`・`sold/sold_items.json`）。相場DB本体 `souba_db.sqlite` は100MB超のため `.gitignore` 済みで、GitHub Releasesに保存する
  - `recon/`: 実行ログ・診断レポート（自動生成。HTML/JSON/テキスト）。`*_STATS.txt` が「なぜ通知に至らなかったか」の診断

## ルール
- 日本語で返答する
- ウェブリサーチは信頼できる発信元のみ参照する
- 個人情報を外部に流出させない。Discord通知・ログ・`recon/`・コミットのいずれにも、取引相手・出品者個人の氏名・住所・電話番号・メールアドレス・アカウント情報を含めない
- **個人情報を含むファイルには一切触れない（閲覧・編集・移動・リネーム・削除・コミットのすべて）。** 対象は、**場所を問わず**、購入者・取引相手・出品者個人の氏名・住所・連絡先を含むファイル、各サイトのログイン情報を含むファイル、その他顧客名や個人情報を含むファイル。下の「編集は自動承認」の例外であり、必要が生じた場合は実行せず必ず事前確認する
- コミット時は `git add -A` / `git add .` を使わず、対象ファイルを必ず明示指定する（個人情報・100MB超の相場DB・不要ファイルの巻き込み防止）。`せどり/データ/state/` と `せどり/データ/recon/` はActionsが自動コミットするフォルダなので、ローカルから手でコミットしない
- パソコン内データを削除する際は必ずどんな名前、どのような内容のファイルのことか事前報告し、承認確認する（編集は自動承認モードで進めてよい）
- 最新情報が必要な場合はウェブ検索で確認する
- 私はプログラミング未経験の学生。専門用語は避けて説明する
- GitHub Actionsに関わる次の操作は、何をするか事前に報告して承認を得てから行う: ワークフローの手動起動（workflow_dispatch）、実行中ワークフローの停止、Secretsの追加・変更、cron（実行間隔）の変更、本番ワークフローの削除・無効化
- push の前に必ず `git pull --rebase` する（Actionsが同じブランチへ頻繁にコミットしているため、衝突しやすい）

---

# せどり開発規約（scripts/workflows実装時の憲法）

**正とする仕様**: `せどり/sedori.md` と本ファイル。矛盾を見つけたら実装せず報告する。設定値（監視対象・除外条件・相場）の正は `せどり/データ/watchlists/`、動作の正は `.github/workflows/`。作業ごとに使うAIは下の「AI使い分け表」に従う。

## 技術スタック確定表
| 領域 | 技術 |
|---|---|
| 言語 | Python 3.11（標準ライブラリ中心: urllib・json・sqlite3・re。外部ライブラリは画像判定だけに限る） |
| 実行基盤 | GitHub Actions（ubuntu-latest。cron＋workflow_dispatch）。PATによる自動再起動チェーンで約5時間のループ監視を継続し、cronはチェーンが途切れた時の保険。`concurrency` で同じ監視の二重実行を防ぐ |
| サイト取得 | urllib＋正規表現。Shopify系（KINDAL・BRING）は `products.json`、他サイトは専用の読み取り部品 |
| 画像判定AI | CLIP＋DINOv2（sentence-transformers・torch CPU版・opencv）。2種類の指紋＋幾何検証（`geom_verify.py`）の二重チェック |
| 最終確認AI | Gemini API（無料枠、任意）／Anthropic API（有料、任意）。`verify_ai.py` |
| 相場DB | SQLite（`souba_db.sqlite`）。gz圧縮してGitHub Releasesに保存し、実行時に `db_release.py` でダウンロード |
| 状態管理 | JSONファイル（`せどり/データ/state/`）。Actionsが `[skip ci]` 付きでコミット |
| 通知 | Discord Webhook |
| 秘密情報 | GitHub Secrets: `DISCORD_WEBHOOK_URL` / `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` / `PAT_TOKEN`（＋自動付与の `GITHUB_TOKEN`） |

## AI使い分け表
| 場面 | 使うもの | 無い時の動き |
|---|---|---|
| 写真が同じ商品かの一次判定 | CLIP＋DINOv2 の指紋（`souba_match.py`・`fingerprint.py`） | 画像判定をスキップし、新着監視だけ続ける |
| 一次判定の裏取り | 幾何検証（`geom_verify.py`） | 同上 |
| 人の目レベルの最終確認 | Gemini（無料枠）を優先、無ければ Anthropic（有料）。5秒以上の間隔を空けて呼ぶ | 最終確認なしで進む（静かにスキップ） |
| コードの作成・修正 | Claude Code（このセッション） | — |

## 6原則
1. **監視を止めないことが最優先。** AIの道具（torch・相場DB・APIキー）が無くても新着監視は動き続ける設計にする（画像判定だけ休む）。道具の読み込み失敗で本体が落ちる書き方は禁止。
2. **仕入れ先サイトには優しくアクセスする。** リクエスト間に待ち時間（`REQUEST_WAIT`）を入れ、取得件数・ページ数（`PER_PAGE`・`MAX_PAGES`）・チェック間隔（`POLL_SECONDS`）に上限を設ける。待ち時間を短く・上限を大きくする変更は理由を報告してから行う。ログインが必要なページ・CAPTCHA・アクセス制限の回避は行わない。
3. **初回実行では通知しない。** 今ある商品を「見た」と記録するだけにして、過去在庫の大量通知を防ぐ。記録の形式を変える時は古い記録も読めるようにし、読めない場合は初回扱い（通知しない）に倒す。
4. **通知は重複させない。** 送信済み記録（`state/` のseen系JSON）が正。二重通知を防ぎ、通知しすぎ防止の上限（`SCAN_MAX` 等）を守る。購入記録など「二重に動くと困る状態」もすべて `state/` のJSONが正で、Actionsだけが更新する。
5. **秘密情報（Discord Webhook・APIキー・PAT）はGitHub Secretsと環境変数でのみ扱う。** コードやデータファイルに直接書かない・読まない。ログ・`recon/`・Discord通知にも出さない。
6. **学生が運用できることを設計要件とする。** 設定は `watchlists/` のJSONと環境変数で変えられるようにし、コードのコメントは専門用語を避けた日本語で書く。設定値に「変えると何が起きるか」をコメントで添える。

## コーディング規約
- コメント・ログ・Discord通知文・コミットメッセージはすべて日本語。「このプログラムがすること」を冒頭のdocstringに番号付きの平易な言葉で書く（既存スクリプトの書き方に合わせる）
- ファイルパスはリポジトリのルートからの相対パス（`せどり/データ/...`）で統一する。スクリプトはルートから実行される前提。パスは各スクリプト冒頭の「設定」欄に定数としてまとめる
- サイトごとの読み取り部品（`trefac.py` 等）と、通知・判定の本体（`monitor.py`）を分ける。新しい仕入れ先を足す時は「読み取り部品を新設 → `watchlists/watch_<サイト>.json` → `state/<サイト>_seen.json` → `monitor.py` に登録」の順で、本体の通知・保存部品を再利用する
- 外部ライブラリの追加は最小限にする。失敗しても本体が止まらないよう `try / except` で読み込み、無ければ機能をスキップする
- **秘密情報（APIキー・トークン等）を読まない・書かない。** 値は人間だけがGitHub Secretsに登録し、コードは環境変数名だけを扱う
- 実行結果・診断レポートは `せどり/データ/recon/` に書き出し、あとで見返せるようにする。通知に至らなかった理由は `*_STATS.txt` に件数つきで残す
- Discord通知文には、ブランド名・商品名・値段・リンク・画像を必ず含める。テスト送信は本番と区別できる文言（「テスト」等）にする
- 動作確認はテスト系ワークフロー（`test-*.yml`・`demo-*.yml`・`recon.yml`・`mercari-test.yml`）で小さく試してから、本番系に反映する。本番系（cronあり）: `monitor` / `mercari-scan` / `mercari-priority-scan` / `scan-profit` / `collect-souba` / `purchase-log` / `upgrade-embeddings`
- ワークフローの必須要素: `timeout-minutes`、同じ処理が重ならないための `concurrency`、コミットする場合は `permissions: contents: write`、自動コミットのメッセージに `[skip ci]`、`git add` は対象パスを明示、`actions/checkout@v4`・`actions/setup-python@v5`・Python 3.11
- ワークフローを変更したら `.github/workflows/` を正とし、`せどり/コーディング/workflows/` のコピーも同じ内容に揃える

## 参照マップ
- プロジェクト全体の説明: `せどり/sedori.md`
- 監視対象・除外条件・相場設定（設定の正）: `せどり/データ/watchlists/`
- 自動実行の仕組み（スケジュール・Secrets・実行時間の上限）: `.github/workflows/`
- 新着監視の本体と通知部品: `せどり/コーディング/scripts/monitor.py`
- 通知に至らなかった理由の診断: `せどり/データ/recon/*_STATS.txt`
- 相場DBの入出力: `せどり/コーディング/scripts/db_release.py`
- 最終確認AIの仕様とカギの作り方: `せどり/コーディング/scripts/verify_ai.py`
- 購入・売却記録の使い方: `せどり/コーディング/scripts/purchases.py`
- 過去のアイデア・検討メモ: `せどり/壁打ち/`
