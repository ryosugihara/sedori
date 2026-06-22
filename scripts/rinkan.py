# -*- coding: utf-8 -*-
"""
RINKAN（リンカンオンライン）用の読み取り部品

RINKANはShopifyではなく独自サイトですが、隠れAPIを見つけました。
  検索API: https://api.rinkan-online.com/api/search?keyword=...（新着順・JSON）
ここからブランド名で検索して、商品一覧を取り出します。
"""

import json
import urllib.request
import urllib.parse

SHOP_NAME = "RINKAN"
SITE = "https://rinkan-online.com"
API = "https://api.rinkan-online.com/api/search"
PER_PAGE = 100  # 新着順の上位100件を見れば、直近の新着は取りこぼさない

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "ja,en;q=0.9",
    "Referer": "https://rinkan-online.com/",
    "Origin": "https://rinkan-online.com",
}


def _price_num(p):
    """セール価格があればそれを、無ければ通常価格を数値で返す"""
    sp = str(p.get("sale_price", "")).strip()
    if sp not in ("", "0"):
        try:
            return int(float(sp))
        except Exception:
            pass
    try:
        return int(float(p.get("price") or 0))
    except Exception:
        return None


def parse_products(data, brand_name):
    """検索APIのJSONから、通知用の商品リストを作る"""
    items = []
    for p in data.get("products", []):
        code = p.get("product_code")
        if not code:
            continue
        pn = _price_num(p)
        images = p.get("images") or []
        items.append(
            {
                "id": code,
                "title": p.get("product_name", "(名前なし)"),
                "brand": brand_name,
                "price": ("¥{:,}".format(pn) if pn else "(価格不明)"),
                "price_num": pn,
                "url": f"{SITE}/products/{code}",
                "image": images[0] if images else None,
                "shop": SHOP_NAME,
                "category": p.get("category_name", ""),
            }
        )
    return items


def fetch_brand_items(brand):
    """ブランド設定(辞書)を受け取り、そのブランドの新着商品一覧を返す"""
    url = f"{API}?keyword={urllib.parse.quote(brand['keyword'])}&per_page={PER_PAGE}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as res:
        data = json.loads(res.read().decode("utf-8"))
    return parse_products(data, brand["name"])
