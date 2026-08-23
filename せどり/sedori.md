# 服のせどり自動化プロジェクト

## 概要
ブランド古着せどりの作業（仕入れ先の新着監視・相場照合・利益商品の発見・購入記録）を、AIとGitHub Actionsで自動化するプロジェクト。人がやっているのは「通知を見て買うか決める」ことだけで、探す・比べる・記録するはすべて自動で回る状態を目指す。

- 仕入れ先（KINDAL・トレファク・BRING・RINKAN・おふもーる）の新着商品を自動で監視する
- メルカリ内の出品も定期的にスキャンし、安く出ている商品を探す
- メルカリの売却実例（相場DB）と写真をAI（CLIP・DINOv2）で見比べて、利益が出そうな商品だけを残す
- 見つけたら Discord に通知し、スマホで受け取る
- 実際に仕入れた商品は購入記録に残し、週1回まとめて Discord に報告する

GitHubリポジトリ: https://github.com/ryosugihara/sedori （作業ブランチ: claude/adoring-bell-7rqznj。Actions もこのブランチへ自動コミットする）

**ルール・開発規約はリポジトリ直下の `CLAUDE.md` が正。** このファイルは「仕組みの説明書」で、両者が矛盾したら実装せず報告する。

## 全体の流れ
```
仕入れ先サイト ──取得──▶ 新着監視 monitor.py ──┐
メルカリ(販売中) ─取得──▶ メルカリ内スキャン ───┤
                                                 ├─▶ 画像判定(CLIP・DINOv2・幾何検証) ─▶ 最終確認AI(Gemini/Anthropic・任意) ─▶ Discord通知
メルカリ(売り切れ) ─収集─▶ 相場DB souba_db.sqlite ┘                                                                           │
                           (GitHub Releasesに保存)                                                           購入記録 purchases.py ◀─┘
```

1. **見張る**: 各サイトから監視ブランドの商品一覧を取り、前回「見た商品」と比べて増えた分だけを新着とする
2. **比べる**: 新着の写真を、メルカリで実際に売れた商品の写真（相場DB）と照合し、「同デザインがいくらで売れているか」から予想利益を出す
3. **知らせる**: 条件を満たした商品だけをDiscordに送る（ブランド名・商品名・値段・リンク・画像・予想利益）
4. **覚える**: 見た商品・送信済み商品を `state/` に記録し、次回以降は同じ物を通知しない
5. **振り返る**: 買った・売れたを記録し、予想利益が当たっているかを週1回レポートする

## 通知の2つの基準（①と②）
監視ブランドは `watchlists/watch_*.json` で2種類に分かれる。

| 種類 | 設定 | 通知の条件 | 画像判定が使えない時 |
|---|---|---|---|
| ① 優先ブランド | `profit_only` なし（例: Saint Laurent） | ブランドが一致した新着は**すべて**通知。相場照合の結果は参考表示 | そのまま通知する |
| ② 利益限定ブランド | `profit_only: true` | 写真が相場DBの売却実例と「同デザイン」で、**予想利益が通知ライン（既定2,000円）以上**の時だけ通知 | 通知せずスキップ（文字だけだと別デザインを誤通知するため） |

共通の絞り込み: `only_keywords`（この言葉を含む商品だけ）、`exclude.json` のNGキーワード・価格ルール・ブロックURL。

## 自動実行スケジュール（GitHub Actions）
時刻は日本時間。詳細・Secrets・実行時間の上限は `.github/workflows/` が正。

| ワークフロー | 役割 | 間隔 |
|---|---|---|
| `monitor.yml` 新着監視 | 5サイトの新着を30秒ごとに見張るループ（1回約5時間）。終わると自分で次を起動し続ける（PAT再起動チェーン）。cron 6時間ごとはチェーンが途切れた時の保険 | 常時 |
| `mercari-scan.yml` メルカリ内スキャン | 販売中の商品を相場DBと照合して掘り出し物を探す | 毎日 12:00 |
| `scan-profit.yml` 在庫スキャン | 監視中の全ブランドの「今ある出品」から利益商品を探す棚卸し | 毎日 12:30 |
| `mercari-priority-scan.yml` メルカリ優先スキャン | 出品直後に競争になるブランドだけを高頻度で見張るループ | 6時間ごと（ループ） |
| `collect-souba.yml` 相場DB収集 | メルカリの売り切れ実例を集めて相場DBに追加 | 毎月1日・15日 12:00 |
| `upgrade-embeddings.yml` 指紋アップグレード | 相場DBの指紋を新方式（背景切り抜き＋SigLIP＋DINOv2-large）で作り直す。全件終わるまで繰り返す | 3時間ごと |
| `purchase-log.yml` 購入・売却の記録 | 買った・売れたを記録し、集計レポートをDiscordへ | 毎週月曜 09:00 |

手動専用（Actionsタブから Run workflow）:
- 確認系: `test-notify`（テスト通知1通）/ `test-gemini`（AIカギの動作確認）/ `recon`（KINDAL接続テスト）/ `test-db-release`（相場DB保存の往復テスト。本番DBには触れない）
- 調整系: `calibrate` / `calibrate-v2`（画像判定の合格ライン・新旧AIの精度測定）/ `test-color`（色比較の精度）/ `test-vecsize`（指紋サイズ診断）/ `clip-test`
- お試し系: `demo-notify`（今ある商品を新着と仮定して1回通知）/ `demo-match`（画像判定のテスト通知）/ `souba-check`（「ブランド 商品 色 サイズ」の売り切れ相場を返す）/ `discover-brands`（よく売る出品者から新ブランド候補を探す。監視リストへは自動追加しない）/ `mercari-test`
- 復旧系: `repair-db`（壊れた指紋を除去して相場DBを圧縮し直す）/ `souba-db-maintenance`（相場DBの掃除だけ。設定を変えた後、次の収集日を待たずに反映したい時。メルカリへのアクセスなし）
- 校正系: `test-verify-ai`（最終確認AIの点数の精度測定。『AI確認_同じと判定する点数』を決めるための表を作る。検索API3回・AI最大60回）

## 画像判定のしくみ（3段階）
| 段階 | 部品 | 何をするか | 無い時 |
|---|---|---|---|
| 1. 指紋で比べる | `fingerprint.py` / `souba_match.py` | 写真を数字の並び（指紋）にして、相場DBの売却実例と近さを測る。CLIPとDINOv2の2種類で二重チェック | 画像判定をスキップ（監視は続く） |
| 2. 細部で裏取り | `geom_verify.py` | プリントの角・金具・ステッチなどの特徴点が同じ位置関係で一致する数を数える。無地や繰り返し柄は安全側（不一致扱い） | 同上 |
| 3. 人の目レベルの最終確認 | `verify_ai.py` | 写真2枚と商品名をAIに渡し、決まった手順（特徴を言葉にする→比べる→写り方の差か本物の差かを見極める）で「一致度0〜100点＋理由」をJSONで答えさせる。`souba.json` の『AI確認_同じと判定する点数』（既定80）以上なら同じ商品。理由は `recon/AI_VERIFY_LOG.txt` に残る。Gemini（無料枠）優先、無ければAnthropic（有料）。5秒以上の間隔で呼ぶ | 最終確認なしで進む |

判定結果は「同デザイン」（ほぼ同じ見た目が売れている）と「似た系統」（参考程度）の2段階。通知に至らなかった理由は `recon/*_STATS.txt` に件数つきで残る。

### 予想相場の出し方（四分位モデル）
似ている売却実例を最大30件集め（`souba.json`『相場_参照する実例の上限件数』）、極端に高い／安い実例（四分位範囲の1.5倍の外）を外れ値として除いてから、**安全（下から25%）／標準（中央値）／強気（下から75%）** の3つの相場を出す。通知するかどうかの利益判定には『標準』を使う（『相場_判定に使う値』で変更可）。根拠の件数が『相場_最低件数』（既定5件）に満たない時は信頼度「低」として一番安い実例で控えめに判定する。通知には3つの相場・根拠件数・信頼度がすべて表示され、出品価格を決める時の参考になる。

## 相場DBのしくみ
- 正体: `せどり/データ/data/souba_db.sqlite`（1ファイルのデータベース）。商品名・値段・ブランド・サイズ・状態・画像URL・写真の指紋を保存。画像そのものは保存しない
- 収集: `collect_souba.py` が `watch_mercari.json` のキーワードで売り切れを検索（1キーワード最大120件・1.5秒間隔）。すでにある商品はスキップ
- 掃除: まとめ売り・ジャンク・箱のみ・新品など「ふつうの中古1点の値段ではない」実例は入れない／すでにあれば消す（`souba_clean.py`。言葉の一覧は `souba.json`『相場DB_除外キーワード』）。収集のたびに自動で行うほか、`souba-db-maintenance` で掃除だけを単独で行える（メルカリには一切アクセスしない）
- 保存場所: 100MB超のため git には入れない（`.gitignore` 済み）。`db_release.py` がgz圧縮して45MBずつに分け、GitHub Releases（タグ `souba-db`）に保存。途中で失敗しても前の完全なDBが残る
- 取得: 各ワークフローが実行の最初に `db_release.py download` で取り込む。無ければ画像判定なしで続行

## ディレクトリ構成
- `.github/workflows/`: 自動実行設定（実際に動く唯一の正）
- `CLAUDE.md`: ルール・開発規約（正）
- `せどり/sedori.md`: このファイル
- `せどり/壁打ち/`: アイデア出し・要件検討のメモ（README.md・メモ.txt）。個人の連絡先・住所・取引相手の情報は書かない
- `せどり/コーディング/scripts/`: 実際に動くPythonプログラム
  - 本体: `monitor.py`（新着監視＋Discord通知。他のスクリプトも通知・保存部品として再利用）
  - サイト読み取り部品: `trefac.py` / `rinkan.py` / `hardoff.py`（サイトごとに1ファイル。KINDAL・BRINGはShopifyの `products.json` を本体で直接読む）
  - メルカリ: `mercari.py`（検索部品）/ `mercari_scan.py`（仕入れスキャン）/ `mercari_test.py`
  - 画像判定: `souba_match.py` / `fingerprint.py` / `geom_verify.py` / `verify_ai.py`
  - 相場DB: `collect_souba.py` / `souba_clean.py`（掃除の基準）/ `souba_db_maintenance.py`（掃除だけ）/ `db_release.py` / `upgrade_embeddings.py` / `discover_brands.py`
  - 購入記録: `purchases.py`
  - 調整・テスト用: `calibrate*.py` / `clip_test.py` / `color_check.py` / `demo_*.py` / `test_*.py` / `recon.py`
- `せどり/コーディング/workflows/`: ワークフローの参照用コピー（古い場合がある。読む時も `.github/workflows/` を見る）
- `せどり/データ/`
  - `watchlists/`: 設定の正。人が編集する唯一のデータフォルダ（下記「設定の変え方」）
  - `state/`: 見た商品記録（`*_seen.json`）・送信済み記録（`mercari_scan_seen.json`・`scan_profit_seen.json`）・購入記録（`purchases.json`）。Actionsが自動更新・コミットする。手で編集しない
  - `data/`: 相場データ（`mercari_souba.json`・`sold/sold_items.json`）と相場DB本体（git管理外）
  - `recon/`: 実行ログ・診断レポート（自動生成）。`*_STATS.txt`＝通知に至らなかった理由、`SCAN_PROFIT.txt`・`MERCARI_SCAN.txt`＝送った商品の記録、`page_*.html`・`mercari_*.html`＝取得したページの生データ

## 設定の変え方（`せどり/データ/watchlists/`）
各ファイルの先頭 `_説明` に書き方が載っている。JSONの形式（カンマ・引用符）を崩さないこと。

| ファイル | 何を決めるか | 主な項目 |
|---|---|---|
| `watch_brands.json` | KINDALで見張るブランド | `name`（通知に出る名前）/ `collection`（KINDAL上の名前） |
| `watch_bring.json` | BRINGで見張るブランド | `name` / `collection`（wastenotのhandle） |
| `watch_trefac.json` / `watch_rinkan.json` / `watch_hardoff.json` | トレファク・RINKAN・おふもーるで見張るブランド | `name` / `keyword`（検索語）/ `only_keywords` / `profit_only` |
| `watch_mercari.json` | 相場DB収集・メルカリ内スキャンの検索キーワード | `name`（監視ブランドと同じ名前にする）/ `keywords` |
| `souba.json` | 相場の設定と手入力の売却実例 | `設定`: 利益通知ライン（既定2,000円）/ 相場モデル（上限件数・最低件数・判定に使う値）/ 相場DB除外キーワード / AI確認の点数ライン、`records[]`: `brand` / `keywords` / `mercari_price` / `memo` |
| `exclude.json` | 通知から除外する条件 | `ng_keywords` / `brand_ng_keywords` / `price_rules` / `block_urls`（誤通知した商品を二度と送らない永久ブロック） |

数字の調整は環境変数でもできる（ワークフローの `env:` に書く）: `POLL_SECONDS`（チェック間隔）/ `LOOP_MINUTES`（1回の見張り時間）/ `SCAN_MAX`（1回の通知上限）/ `SLOW_SECONDS`（②ブランドのゆっくり巡回間隔）など。サイトへの待ち時間を短く・上限を大きくする変更は理由を報告してから行う。

## 秘密情報（GitHub Secrets）
値はGitHubの Settings → Secrets にだけ登録し、コードやファイルには書かない。

| 名前 | 用途 | 無い時 |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | 通知の送り先 | 通知できない（必須） |
| `PAT_TOKEN` | 新着監視の自動再起動チェーン | 再起動せず、cron（6時間ごと）と手動に頼る |
| `GEMINI_API_KEY` | 最終確認AI（無料枠） | 最終確認をスキップ |
| `ANTHROPIC_API_KEY` | 最終確認AI（有料・任意） | 同上 |
| `GITHUB_TOKEN` | 相場DBのReleases保存・取得（自動付与） | — |

## 日々の確認のしかた
- 通知が来ない時: ①Actionsタブで `KINDAL新着監視` が動いているか（止まっていれば Run workflow）→ ②`recon/*_STATS.txt` で「なぜ通知に至らなかったか」を見る → ③`test-notify` でDiscordの配線を確かめる
- 誤通知があった時: `exclude.json` の `block_urls` に商品URLを足す（永久ブロック）。ブランド名が紛れているだけなら `brand_ng_keywords`
- 買った・売れた時: `purchase-log` を `ACTION=buy` / `ACTION=sell` で実行（URL・値段を入力）。月曜朝にレポートが届く
- 相場DBを育てたい時: `watch_mercari.json` にキーワードを足す → 次回の `collect-souba`（1日・15日）で取り込まれる。急ぐなら手動実行

## 用語集
- **新着**: 前回の実行で「見た」記録に無かった商品。初回実行は記録するだけで通知しない
- **指紋**: 写真をAIで数字の並びに変えたもの。指紋どうしの近さ＝見た目の近さ
- **同デザイン／似た系統**: 指紋の近さの2段階。通知は原則「同デザイン」のみ
- **予想利益**: 判定相場（既定＝似た実例の中央値）× (1 − メルカリ手数料) − 送料 − 仕入れ値（手数料・送料は `souba.json` の `設定`）
- **安全／標準／強気**: 似た売却実例の値段の下から25%／50%／75%。外れ値は除いてある。信頼度（高/中/低）は根拠の件数
- **seen（見た記録）**: `state/*_seen.json`。重複通知防止の正
- **PAT再起動チェーン**: 監視ループが終わるたびに自分で次の実行を起動し、常時監視を実現する仕組み

## 現在の状況と今後
- 稼働中: 5サイトの常時新着監視、メルカリ内スキャン（毎日）、在庫スキャン（毎日）、相場DB収集（月2回）、購入・売却レポート（週1回）
- 進行中: 相場DBの指紋を新方式へ作り直し（`upgrade-embeddings`、全件終わるまで自動継続）
- 未作成: 業務フロー・処理フローの図（フローチャート）
- 課題の記録先: `せどり/壁打ち/メモ.txt`（個人情報は書かない）
- 未解決の検討事項（提案のうち保留にしたもの・校正待ちの設定）: `せどり/壁打ち/未解決事項.md`
