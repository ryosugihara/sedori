# -*- coding: utf-8 -*-
"""
トレファク（TREFAC FASHION）用の読み取り部品

KINDAL は Shopify でデータが「きれいな表(JSON)」で取れましたが、
トレファクは独自の作りで、データが「Webページ(HTML)の中」にあります。
そこでこのファイルでは、HTMLから商品情報を取り出す（パースする）処理を行います。

取り出す情報（商品1つごと）:
  id（商品の固有番号）/ title（商品名）/ brand / price（値段）/ url（商品ページ）/ image（写真）
"""

import re
import time
import urllib.request
import urllib.parse

SHOP_NAME = "トレファク"

# ブランド名で「新着順」に検索するURL（%s にブランド名が入る）
SEARCH_URL = "https://www.trefac.jp/store/tcpsb/?srchword=%s&step=1&order=new"

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
    """ブランド名で検索して、結果ページのHTML（文字列）を返す"""
    # 検索語をURL用に変換（スペース→%20 など）
    url = SEARCH_URL % urllib.parse.quote(keyword)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read().decode("utf-8", errors="replace")


def parse_items(html):
    """検索結果のHTMLから、商品の一覧（辞書のリスト）を取り出す"""
    items = []
    # 商品は <li class="p-itemlist_item"> ごとのかたまり。それで分割する
    blocks = html.split('class="p-itemlist_item"')[1:]
    for block in blocks:
        # 商品ページのURLと、その中の固有ID
        m_url = re.search(r'href="(https://www\.trefac\.jp/store/(\d+)/c\d+/)"', block)
        if not m_url:
            continue  # 商品リンクが無いかたまりは飛ばす
        url = m_url.group(1)
        item_id = m_url.group(2)

        # 商品名（写真の alt に入っている）
        m_alt = re.search(r'alt="([^"]*)"', block)
        title = m_alt.group(1).strip() if m_alt else "(名前不明)"

        # 写真のアドレス
        m_img = re.search(r'<img[^>]*src="([^"]+)"', block)
        image = m_img.group(1) if m_img else None

        # ブランド名
        m_brand = re.search(r'p-itemlist_brand">([^<]*)<', block)
        brand = m_brand.group(1).strip() if m_brand else ""

        # 値段（￥12,345 の形。最初に出てくるものを使う）
        m_price = re.search(r'￥\s*([\d,]+)', block)
        if m_price:
            price = "¥" + m_price.group(1)
            price_num = int(m_price.group(1).replace(",", ""))  # 計算用の数値
        else:
            price = "(価格不明)"
            price_num = None

        items.append(
            {
                "id": item_id,
                "title": title,
                "brand": brand,
                "price": price,
                "price_num": price_num,
                "url": url,
                "image": image,
                "shop": SHOP_NAME,
            }
        )
    return items


def fetch_brand_items(brand):
    """ブランド設定(辞書)を受け取り、そのブランドの新着商品一覧を返す"""
    keyword = brand["keyword"]
    html = fetch_html(keyword)
    items = parse_items(html)
    # 表示名を統一（設定の name を使う）
    for it in items:
        it["brand"] = brand["name"]
    return items
