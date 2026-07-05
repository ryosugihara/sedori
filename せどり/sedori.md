# 服のせどり自動化プロジェクト

## 概要
ブランド古着せどりの作業を自動化するためのプロジェクト。
- 仕入れ先（まずは KINDAL）の新着商品を自動で監視する
- 取扱ブランドの新着が出たら Discord に通知する
- 通知はスマホで受け取る

GitHubリポジトリ: https://github.com/ryosugihara/sedori （ブランチ: claude/adoring-bell-7rqznj）

## ディレクトリ構成
- 壁打ち: アイデア出し・要件検討のメモ（README.mdなど）
- フローチャート: 業務フロー・処理フローの図（未作成）
- コーディング: 実装コード
  - scripts/: 実際に動くPythonプログラム（メルカリ/ハードオフ/ブランドリユース等の監視・相場取得スクリプト）
  - workflows/: GitHub Actionsの自動実行設定（元 .github/workflows/）
  - .gitignore
- データ: 収集・加工したデータ
  - data/: 相場DB・成約商品データ
  - recon/: 接続テスト・スクレイピング結果（自動生成、HTML/JSONログ）
  - state/: 各サイトの既読・監視状態
  - watchlists/: 監視対象ブランド・相場・除外リストなどの設定JSON
