# -*- coding: utf-8 -*-
"""
買取王国オンライン（okoku.jp）用の読み取り部品

買取王国は Shopify。ブランド別の売り場は無く『ファッションブランド』売り場の中を
アプリで絞り込む作りのため、お店全体の新着(/products.json の1ページ目)を読み、
その中から見張るブランドだけを抜き出す。
  ・/products.json は新しい順に並ぶ（実測 2026-11-25: 1ページ250件 = 約2時間半分）。
    30秒ごとに読めば新着は漏れない
  ・全ブランドで同じページを使うので、CACHE_SECONDS の間は前回の結果を使い回す
    （3ブランド分を見ても、お店への読み込みは30秒に1回程度）
  ・robots.txt は商品・売り場の閲覧を許可。並べ替え(sort_by)は禁止なので使わない
"""

import json
import time
import urllib.request

SHOP_NAME = "買取王国"
BASE = "https://okoku.jp"
PER_PAGE = 250
CACHE_SECONDS = 20  # この秒数の間は、お店全体の新着ページを読み直さない

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "ja,en;q=0.9",
}

_cache = {"t": 0.0, "products": []}


def newest_products():
    """お店全体の新着(1ページ目・新しい順250件)を返す。CACHE_SECONDS の間は使い回す"""
    now = time.time()
    if now - _cache["t"] < CACHE_SECONDS:
        return _cache["products"]
    req = urllib.request.Request(f"{BASE}/products.json?limit={PER_PAGE}&page=1", headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            products = json.loads(res.read().decode("utf-8")).get("products", [])
    except Exception:
        _cache["t"] = now  # 失敗してもすぐ読み直さない（お店に負担をかけない）
        raise
    _cache["t"], _cache["products"] = now, products
    return products


def _matches(product, brand):
    """この商品が、見張るブランドの物か（vendor の一致、または商品名の言葉）"""
    vendor = (product.get("vendor") or "").strip().lower()
    if vendor in [v.lower() for v in brand.get("vendors", [])]:
        return True
    title = (product.get("title") or "").lower()
    return any(k.lower() in title for k in brand.get("title_keywords", []))


def _build(product, brand_name):
    variants = product.get("variants") or [{}]
    images = product.get("images") or []
    raw = variants[0].get("price", "")
    try:
        price_num = int(float(raw))
        price = "¥{:,}".format(price_num)
    except Exception:
        price_num, price = None, f"¥{raw}"
    return {
        "id": product.get("id"),
        "brand": brand_name,
        "title": product.get("title", "(名前なし)"),
        "price": price,
        "price_num": price_num,
        "url": f"{BASE}/products/{product.get('handle')}",
        "image": images[0].get("src") if images else None,
        "shop": SHOP_NAME,
        "category": product.get("product_type", ""),
    }


def fetch_brand_items(brand):
    """ブランド設定を受け取り、お店全体の新着のうち、そのブランドの商品だけを返す。
    brand = {"name": 通知に出す名前, "keyword": 見た記録の名前,
             "vendors": [ブランド表記...], "title_keywords": [商品名に含まれる言葉...]}
    """
    out = []
    for p in newest_products():
        if not _matches(p, brand):
            continue
        # 在庫が無い(全サイズ売り切れ)物は通知しても買えない
        if not any(v.get("available", True) for v in (p.get("variants") or [{}])):
            continue
        out.append(_build(p, brand["name"]))
    return out
