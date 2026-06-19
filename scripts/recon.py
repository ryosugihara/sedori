# -*- coding: utf-8 -*-
"""
KINDAL 接続テスト（recon = 偵察）スクリプト

このプログラムがすること：
  1. KINDAL のページにアクセスしてみる
  2. つながったか（HTTPステータス）を記録する
  3. 取得したページの中身（HTML）を recon フォルダに保存する
  4. Discord に「テストの結果」を1通送る

※ なぜ必要？
  Claude(私)のいるクラウドからは KINDAL に直接アクセスできないため、
  GitHub の自動実行(Actions)に「代わりに見に行ってもらい」、
  結果をこのリポジトリに保存してもらうための偵察用プログラムです。
"""

import os
import json
import datetime
import urllib.request
import urllib.error

# --- 設定 ------------------------------------------------------------
# 確認したい KINDAL のページURL（まずはトップページ）
TARGET_URLS = [
    "https://kind.co.jp/",
]

# 本物のブラウザのふりをするための情報（これがないと弾かれやすい）
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# 結果を保存するフォルダ名
OUTPUT_DIR = "recon"
# --------------------------------------------------------------------


def fetch(url):
    """1つのURLにアクセスして、(状態コード, 中身) を返す"""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as e:
        # 403 などのエラー応答もここで中身を拾う
        return e.code, e.read()
    except Exception as e:
        # ネットワーク自体がダメだった場合
        return None, str(e).encode("utf-8")


def send_discord(message):
    """Discord にメッセージを送る（Webhook URL が設定されている時だけ）"""
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        print("DISCORD_WEBHOOK_URL が未設定のため、Discord送信はスキップしました")
        return
    # Discord は2000文字までなので念のため短く切る
    data = json.dumps({"content": message[:1900]}).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=30)
        print("Discord に送信しました")
    except Exception as e:
        print(f"Discord 送信に失敗: {e}")


def main():
    # 保存用フォルダを作る（既にあってもエラーにしない）
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    summary_lines = [f"KINDAL 接続テスト  実行時刻: {now}", ""]

    for i, url in enumerate(TARGET_URLS, start=1):
        status, body = fetch(url)
        size = len(body) if body else 0
        line = f"[{i}] {url}  ->  status={status}, size={size} bytes"
        print(line)
        summary_lines.append(line)

        # 取得した中身をファイルに保存（あとで Claude が中身を調べるため）
        with open(os.path.join(OUTPUT_DIR, f"page_{i}.html"), "wb") as f:
            f.write(body or b"")

    # 結果の要約をファイルに保存
    with open(os.path.join(OUTPUT_DIR, "SUMMARY.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(summary_lines))

    # Discord に結果を1通送る
    send_discord("【KINDAL 接続テスト結果】\n" + "\n".join(summary_lines))


if __name__ == "__main__":
    main()
