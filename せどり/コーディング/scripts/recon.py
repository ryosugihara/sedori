# -*- coding: utf-8 -*-
"""
メルカリ 相場取得テスト（recon = 偵察）スクリプト

目的:
  メルカリの「売り切れ（sold_out）検索ページ」を、ロボット（自動）で
  取りに行けるか？ 取れた中身に商品データ（値段など）が入っているか？を調べる。

  ※メルカリはロボットのアクセスを強くブロックするため、まず「そもそも取れるか」
    を確認するための偵察です。ここで取れれば、本番の相場取得を作れます。

やること:
  1. いくつかのメルカリ検索URLにアクセスしてみる
  2. つながったか（状態コード）と中身の大きさを記録する
  3. 中身に「商品データらしき目印」が入っているか自動で数える
  4. 取得したHTMLを recon フォルダに保存（あとで詳しく中身を調べる用）
  5. Discord に結果の要約を1通送る
"""

import os
import re
import json
import datetime
import urllib.request
import urllib.error
import urllib.parse


# --- 調べたいメルカリの検索URL --------------------------------------
# status=sold_out = 売り切れ（＝実際に売れた相場）。色・サイズ違いも試す。
def search_url(keyword):
    return (
        "https://jp.mercari.com/search?keyword="
        + urllib.parse.quote(keyword)
        + "&status=sold_out&order=desc&sort=created_time"
    )


# 通知で送った「根拠リンク」がちゃんと生きているかを確認する。
# 同じ商品を2つのリンク形式で試す（jp.mercari.com と item.mercari.com）。
TARGET_URLS = [
    "https://jp.mercari.com/item/m89440520190",
    "https://jp.mercari.com/item/m69539026459",
    "https://jp.mercari.com/item/m88593701431",
    "https://item.mercari.com/jp/m89440520190/",   # 昔からある共有リンク形式
    "https://item.mercari.com/jp/m69539026459/",
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

OUTPUT_DIR = "せどり/データ/recon"
# --------------------------------------------------------------------


def fetch(url):
    """1つのURLにアクセスして、(状態コード, 最終URL, 中身) を返す"""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, res.geturl(), res.read()
    except urllib.error.HTTPError as e:
        return e.code, url, e.read()
    except Exception as e:
        return None, url, str(e).encode("utf-8")


def analyze(body):
    """取得した中身に『商品データらしき目印』がいくつ入っているか調べる"""
    try:
        html = body.decode("utf-8", errors="replace")
    except Exception:
        html = ""
    m_title = re.search(r"<title>([^<]*)</title>", html)
    return {
        "has_next_data": "__NEXT_DATA__" in html,   # Next.js が埋め込む初期データ
        "n_itemName": len(re.findall(r"itemName", html)),
        "n_price": len(re.findall(r'"price"', html)),
        "n_item_id": len(re.findall(r'"m\d{6,}"', html)),  # メルカリ商品ID(m+数字)
        "looks_blocked": ("Access Denied" in html or "captcha" in html.lower()
                          or "ロボット" in html),
        "title": (m_title.group(1)[:60] if m_title else ""),
        # 商品ページが死んでいる時に出る文言たち
        "error_text": [t for t in ["エラーが発生しました", "存在しない", "削除され",
                                   "ページが見つかりません", "not found"]
                       if t in html],
    }


def send_discord(message):
    """Discord にメッセージを送る（Webhook URL がある時だけ）"""
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        print("DISCORD_WEBHOOK_URL 未設定のためDiscord送信はスキップ")
        return
    data = json.dumps({"content": message[:1900]}).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "sedori-bot/1.0 (+https://github.com/ryosugihara/sedori)",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=30)
        print("Discord に送信しました")
    except Exception as e:
        print(f"Discord 送信に失敗: {e}")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"メルカリ 相場取得テスト  実行時刻: {now}", ""]

    for i, url in enumerate(TARGET_URLS, start=1):
        status, final_url, body = fetch(url)
        size = len(body) if body else 0
        info = analyze(body or b"")
        verdict = (
            "🟢 商品データあり" if (info["n_item_id"] > 0 or info["n_itemName"] > 0)
            else ("🔴 ブロックされた" if info["looks_blocked"]
                  else "🟡 データ見当たらず")
        )
        line = (
            f"[{i}] {url}\n"
            f"    status={status} size={size:,}B {verdict}\n"
            f"    final={final_url}\n"
            f"    title={info['title']} エラー文言={info['error_text']}"
        )
        print(line)
        lines.append(line)
        with open(os.path.join(OUTPUT_DIR, f"mercari_{i}.html"), "wb") as f:
            f.write(body or b"")

    with open(os.path.join(OUTPUT_DIR, "SUMMARY.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    send_discord("【メルカリ 相場取得テスト】\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
