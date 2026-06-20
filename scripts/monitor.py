# -*- coding: utf-8 -*-
"""
新着監視プログラム（本番）／対応サイト: KINDAL・トレファク(TREFAC FASHION)

このプログラムがすること:
  1. 見張りたいブランドの商品一覧を、各サイトから取得する
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

import trefac  # トレファク用の読み取り部品（同じフォルダの trefac.py）

# --- 設定（ここの数字や名前を変えれば動きを調整できます）-------------------
SHOP = "https://shop.kind.co.jp"      # KINDAL 通販サイトのアドレス
BRANDS_FILE = "watch_brands.json"     # KINDAL の見張るブランド一覧
STATE_FILE = "state/seen.json"        # KINDAL の「見た商品」記録

TREFAC_BRANDS_FILE = "watch_trefac.json"     # トレファク の見張るブランド一覧
TREFAC_STATE_FILE = "state/trefac_seen.json" # トレファク の「見た商品」記録
PER_PAGE = 250                        # 1回の取得件数（Shopifyの最大値）
MAX_PAGES = 20                        # 安全のための上限（無限ループ防止）
REQUEST_WAIT = 1.5                    # サイトへの優しさ（アクセスの間に待つ秒数）

# ↓ ループ監視（短い間隔で見張り続ける）用の設定。数字は環境変数で変えられます。
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "30"))  # 何秒ごとにチェックするか
LOOP_MINUTES = int(os.environ.get("LOOP_MINUTES", "27"))  # 1回の見張りを何分続けるか

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
    """KINDAL の商品データから、通知に使う情報だけを取り出す"""
    variants = product.get("variants") or [{}]
    images = product.get("images") or []
    return {
        "id": product.get("id"),
        "brand": brand_name,
        "title": product.get("title", "(名前なし)"),
        "price": yen(variants[0].get("price", "")),
        "url": f"{SHOP}/products/{product.get('handle')}",
        "image": images[0].get("src") if images else None,
        "shop": "KINDAL",
    }


def kindal_items(brand):
    """KINDAL の1ブランドの全商品を、通知用の形(idつき)で返す"""
    products = fetch_collection_products(brand["collection"])
    return [build_item(p, brand["name"]) for p in products]


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
        webhook,
        data=data,
        headers={
            "Content-Type": "application/json",
            # ↓ これが無いと Discord に 403 で拒否される（名乗りが必要）
            "User-Agent": "sedori-bot/1.0 (+https://github.com/ryosugihara/sedori)",
        },
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
            # お店の名前があれば一緒に表示する（KINDAL / トレファク）
            shop = it.get("shop", "")
            shop_line = f"🏪 {shop}\n" if shop else ""
            embed = {
                "title": it["title"][:250],
                "url": it["url"],
                "description": f"{shop_line}🏷️ {it['brand']}\n💴 {it['price']}",
            }
            if it.get("image"):
                embed["thumbnail"] = {"url": it["image"]}
            embeds.append(embed)
        discord_post({"content": f"🆕 新着 {len(chunk)} 件", "embeds": embeds})
        print(f"  Discordに {len(chunk)} 件通知しました")
        time.sleep(1)  # 連続で送りすぎない


def check_source(brands, seen, first_run, get_items):
    """1つのサイトの全ブランドを1回チェックして、新着リストを返す。

    get_items(brand) … そのブランドの商品一覧（id付きの辞書リスト）を返す関数。
    KINDAL でもトレファクでも、この共通処理で扱える。
    """
    new_items = []
    for b in brands:
        # 識別子：KINDALは collection、トレファクは keyword を使う
        key = b.get("collection") or b.get("keyword")
        try:
            items = get_items(b)
        except Exception as e:
            print(f"  取得失敗 ({key}): {e}")
            continue

        # IDは文字列にそろえて比較する（サイトによって数値/文字が混在するため）
        current_ids = [str(it["id"]) for it in items if it.get("id") is not None]
        seen_ids = set(str(x) for x in seen.get(key, []))
        # 「前に見ていない＝新着」の商品を抜き出す
        fresh = [it for it in items if str(it.get("id")) not in seen_ids]

        if first_run:
            print(f"  初回: {key} を {len(current_ids)} 件記録（通知なし）")
        elif fresh:
            new_items.extend(fresh)
            print(f"  新着 {len(fresh)} 件: {key}")

        # 見た商品リストを更新（今ある商品IDを全部覚える）
        seen[key] = sorted(seen_ids | set(current_ids))
        time.sleep(REQUEST_WAIT)
    return new_items


def main():
    # 見張るブランド一覧を読み込む（KINDAL と トレファク、それぞれ）
    kindal_brands = load_json_file(BRANDS_FILE, {"brands": []}).get("brands", [])
    trefac_brands = load_json_file(TREFAC_BRANDS_FILE, {"brands": []}).get("brands", [])

    # これまでに「見た商品」の記録を読み込む（サイトごとに別ファイル）
    kindal_seen = load_json_file(STATE_FILE, {})
    trefac_seen = load_json_file(TREFAC_STATE_FILE, {})
    kindal_first = (len(kindal_seen) == 0)  # 記録が空っぽなら初回
    trefac_first = (len(trefac_seen) == 0)

    def one_pass(k_first, t_first):
        """両サイトを1回ずつチェックして、新着をまとめて返す"""
        new = []
        new += check_source(kindal_brands, kindal_seen, k_first, kindal_items)
        new += check_source(trefac_brands, trefac_seen, t_first, trefac.fetch_brand_items)
        return new

    def save_all():
        save_json_file(STATE_FILE, kindal_seen)
        save_json_file(TREFAC_STATE_FILE, trefac_seen)

    def start_message():
        names = sorted({b["name"] for b in kindal_brands + trefac_brands})
        send_text(
            "✅ 新着監視を開始/更新しました！\n"
            "監視中の店: KINDAL、トレファク\n"
            f"対象ブランド: {'、'.join(names)}\n"
            f"これから約{POLL_SECONDS}秒ごとに新着をチェックします。"
        )

    # 環境変数 LOOP_MODE=1 のときは「ループ監視」、それ以外は「1回だけ」
    loop_mode = (os.environ.get("LOOP_MODE") == "1")

    # --- 1回だけチェックするモード（手動の動作確認用）---
    if not loop_mode:
        new_items = one_pass(kindal_first, trefac_first)
        if kindal_first or trefac_first:
            start_message()
        if new_items:
            send_items(new_items)
        save_all()
        print("完了")
        return

    # --- ループ監視モード（短い間隔で見張り続ける）---
    print(f"ループ監視開始: {POLL_SECONDS}秒ごと / 最長 {LOOP_MINUTES}分")
    end_time = time.time() + LOOP_MINUTES * 60

    # まず最初の1回チェック
    new_items = one_pass(kindal_first, trefac_first)
    if kindal_first or trefac_first:
        start_message()
    if new_items:
        send_items(new_items)
    save_all()

    # 決めた時間内は、くり返しチェックし続ける（2回目以降は初回扱いしない）
    while time.time() < end_time:
        time.sleep(POLL_SECONDS)
        items = one_pass(False, False)
        if items:
            print(f"新着 {len(items)} 件 → 通知")
            send_items(items)
            save_all()  # 念のためその都度保存

    # 最後に記録を保存して終了（次の見張りが続きから始められる）
    save_all()
    print("ループ監視 終了")


if __name__ == "__main__":
    main()
