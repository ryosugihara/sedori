# -*- coding: utf-8 -*-
"""
KINDAL 新着監視プログラム（本番）

このプログラムがすること:
  1. 見張りたいブランドの「商品一覧データ(JSON)」を KINDAL から取得する
  2. 前回までに見た商品と比べて「新しく追加された商品」を見つける
  3. 新着があれば Discord に通知する（ブランド名・商品名・値段・リンク・画像つき）
  4. 「見た商品リスト」を更新して保存する（次回の比較に使う）

大事なルール:
  - 一番最初の実行では、今ある商品を全部「見た」と記録するだけで通知しません。
    （過去の在庫が一気に何百件も通知されるのを防ぐためです）
  - 2回目以降の実行で、新しく増えた商品だけを通知します。
"""

import os
import json
import time
import urllib.request

# --- 設定（ここの数字や名前を変えれば動きを調整できます）-------------------
SHOP = "https://shop.kind.co.jp"      # KINDAL 通販サイトのアドレス
BRANDS_FILE = "watch_brands.json"     # 見張るブランドの一覧ファイル
STATE_FILE = "state/seen.json"        # 「見た商品」を覚えておくファイル
PER_PAGE = 250                        # 1回の取得件数（Shopifyの最大値）
MAX_PAGES = 20                        # 安全のための上限（無限ループ防止）
REQUEST_WAIT = 1.5                    # サイトへの優しさ（アクセスの間に待つ秒数）

# 本物のブラウザのふりをするための情報
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "ja,en;q=0.9",
}
# -------------------------------------------------------------------------


def http_get_json(url):
    """URL にアクセスして、プログラム用データ(JSON)を辞書として返す"""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def fetch_collection_products(handle):
    """あるブランド(売り場)の全商品を、ページをめくりながら取得して返す"""
    all_products = []
    for page in range(1, MAX_PAGES + 1):
        url = f"{SHOP}/collections/{handle}/products.json?limit={PER_PAGE}&page={page}"
        try:
            data = http_get_json(url)
        except Exception as e:
            print(f"  取得失敗 ({handle} page{page}): {e}")
            break
        products = data.get("products", [])
        if not products:
            break  # これ以上商品がなければ終わり
        all_products.extend(products)
        if len(products) < PER_PAGE:
            break  # これが最後のページ
        time.sleep(REQUEST_WAIT)  # サイトに優しく、少し待つ
    return all_products


def yen(price_str):
    """'5060' のような文字列を '¥5,060' の見た目に整える"""
    try:
        return "¥{:,}".format(int(float(price_str)))
    except Exception:
        return f"¥{price_str}"


def build_item(product, brand_name):
    """商品データから、通知に使う情報だけを取り出す"""
    variants = product.get("variants") or [{}]
    images = product.get("images") or []
    return {
        "brand": brand_name,
        "title": product.get("title", "(名前なし)"),
        "price": yen(variants[0].get("price", "")),
        "url": f"{SHOP}/products/{product.get('handle')}",
        "image": images[0].get("src") if images else None,
    }


def load_json_file(path, default):
    """ファイルがあれば読み込む。無ければ default を返す"""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json_file(path, data):
    """データをファイルに保存する（フォルダが無ければ作る）"""
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def discord_post(payload):
    """Discord に1メッセージ送る（共通部分）"""
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        print("DISCORD_WEBHOOK_URL が未設定。通知をスキップします。")
        return
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception as e:
        print(f"  Discord通知に失敗: {e}")


def send_text(message):
    """ただの文章を Discord に送る"""
    discord_post({"content": message[:1900]})


def send_items(items):
    """新着商品を Discord に通知する（見やすいカード形式・最大10件ずつ）"""
    # Discord は1メッセージに最大10個のカード(embed)まで入れられる
    for i in range(0, len(items), 10):
        chunk = items[i:i + 10]
        embeds = []
        for it in chunk:
            embed = {
                "title": it["title"][:250],
                "url": it["url"],
                "description": f"🏷️ {it['brand']}\n💴 {it['price']}",
            }
            if it.get("image"):
                embed["thumbnail"] = {"url": it["image"]}
            embeds.append(embed)
        discord_post({"content": f"🆕 KINDAL 新着 {len(chunk)} 件", "embeds": embeds})
        print(f"  Discordに {len(chunk)} 件通知しました")
        time.sleep(1)  # 連続で送りすぎない


def main():
    # 見張るブランド一覧を読み込む
    brands = load_json_file(BRANDS_FILE, {"brands": []}).get("brands", [])
    # 「見た商品」の記録を読み込む（形は { "売り場名": [見た商品IDたち] }）
    seen = load_json_file(STATE_FILE, {})
    # 記録が空っぽなら「初回」と判断する
    first_run = (len(seen) == 0)

    new_items = []  # 今回見つかった新着（あとでまとめて通知する）

    for b in brands:
        name = b["name"]
        handle = b["collection"]
        print(f"チェック中: {name} ({handle})")

        products = fetch_collection_products(handle)
        # 今ある商品のID一覧（None は除く）
        current_ids = [p["id"] for p in products if p.get("id") is not None]
        seen_ids = set(seen.get(handle, []))

        # 「前に見ていない＝新着」の商品を抜き出す
        fresh = [p for p in products if p.get("id") not in seen_ids]

        if first_run:
            print(f"  初回のため {len(current_ids)} 件を記録（通知なし）")
        else:
            for p in fresh:
                new_items.append(build_item(p, name))
            print(f"  新着 {len(fresh)} 件" if fresh else "  新着なし")

        # 見た商品リストを更新（今ある商品IDを全部覚える）
        seen[handle] = sorted(seen_ids | set(current_ids))
        time.sleep(REQUEST_WAIT)

    # 結果に応じて通知する
    if first_run:
        names = "、".join(sorted({b["name"] for b in brands}))
        send_text(
            "✅ KINDAL 新着監視を開始しました！\n"
            "今ある在庫を記録したので、次回からは新しく入荷した商品だけを通知します。\n"
            f"対象ブランド: {names}"
        )
    elif new_items:
        print(f"合計 {len(new_items)} 件の新着を通知します")
        send_items(new_items)
    else:
        print("新着はありませんでした（通知なし）")

    # 「見た商品」の記録を保存する
    save_json_file(STATE_FILE, seen)
    print("完了")


if __name__ == "__main__":
    main()
