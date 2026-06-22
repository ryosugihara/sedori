# -*- coding: utf-8 -*-
"""
おふもーる（ハードオフ ネットモール）用の読み取り部品

検索URL: https://netmall.hardoff.co.jp/search/?q=ブランド名&s=1（s=1は新着順）
結果ページのHTMLから商品を取り出します（独自サイトなのでHTML解析）。
"""

import re
import urllib.request
import urllib.parse

SHOP_NAME = "オフモール"
SEARCH_URL = "https://netmall.hardoff.co.jp/search/?q=%s&s=1"  # s=1 = 新着順
PRODUCT_BASE = "https://netmall.hardoff.co.jp/product/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.9",
}


def fetch_html(keyword):
    """ブランド名で検索して、結果ページのHTML(文字列)を返す"""
    url = SEARCH_URL % urllib.parse.quote(keyword)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read().decode("utf-8", errors="replace")


def parse_items(html):
    """検索結果HTMLから、商品の一覧(辞書リスト)を取り出す"""
    items = []
    # 商品は class="itemcolmn_item" ごとのかたまり
    blocks = html.split("itemcolmn_item")[1:]
    for block in blocks:
        m_id = re.search(r"/product/(\d+)/", block)
        if not m_id:
            continue
        pid = m_id.group(1)

        m_brand = re.search(r'item-brand-name">([^<]*)<', block)
        m_name = re.search(r'item-name">([^<]*)<', block)
        m_price = re.search(r'item-price-en">\s*([\d,]+)', block)
        m_img = re.search(r'item-img-square.*?<img[^>]*src="([^"]+)"', block, re.S)

        site_brand = m_brand.group(1).strip() if m_brand else ""
        name = m_name.group(1).strip() if m_name else ""
        price_num = int(m_price.group(1).replace(",", "")) if m_price else None

        items.append(
            {
                "id": pid,
                "title": (site_brand + " " + name).strip() or "(名前なし)",
                "brand": site_brand,  # あとで watch設定の name で上書き
                "price": ("¥{:,}".format(price_num) if price_num else "(価格不明)"),
                "price_num": price_num,
                "url": f"{PRODUCT_BASE}{pid}/",
                "image": m_img.group(1) if m_img else None,
                "shop": SHOP_NAME,
                "category": name,  # 商品の種類（財布/デニム等の判定に使う）
            }
        )
    return items


def fetch_brand_items(brand):
    """ブランド設定(辞書)を受け取り、そのブランドの新着商品一覧を返す"""
    html = fetch_html(brand["keyword"])
    items = parse_items(html)
    for it in items:
        it["brand"] = brand["name"]  # 通知に出す名前に統一
    return items
