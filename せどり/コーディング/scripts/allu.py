# -*- coding: utf-8 -*-
"""
ALLU（アリュー）オンライン（allu-official.com）用の読み取り部品

ALLUはNuxt製のサイト。ブランド×カテゴリの売り場ページ
  /jp/ja/market/{ブランド番号}/{カテゴリ番号}/   例: /jp/ja/market/b-95/c-28/ (サンローランのアパレル)
が、最初から新着順で30件並んだ状態でHTMLに入っている（実測 2026-11-25）。
そのページを読み、商品カードから 番号・名前・値段・写真・売り切れ表示 を取り出す。
  ・robots.txt は会員ページ(/market/user/)以外を許可
  ・売り場には売り切れ商品も並ぶので、SOLD OUT の物は除く
  ・ブランド番号は売り場URLの b-数字（サンローラン=b-95、ディオールオム=b-31）。
    カテゴリ c-28 = アパレル（服）
"""

import gzip
import html
import re
import urllib.request

SHOP_NAME = "ALLU"
BASE = "https://allu-official.com"
LIST_URL = BASE + "/jp/ja/market/{code}/{category}/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.9",
    "Accept-Encoding": "gzip",
}

_CARD = re.compile(r'href="https://allu-official\.com/jp/ja/market/items/(\d+)/"')


def fetch_html(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as res:
        raw = res.read()
        if (res.headers.get("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def parse_items(page, category_label="アパレル"):
    """売り場ページのHTMLから、売り切れでない商品の一覧を取り出す"""
    items = []
    anchors = list(_CARD.finditer(page))
    done = set()
    for i, m in enumerate(anchors):
        iid = m.group(1)
        if iid in done:
            continue
        done.add(iid)
        # この商品のカード ＝ 次の『別の商品』へのリンクが出てくるまで
        end = next((a.start() for a in anchors[i + 1:] if a.group(1) != iid), m.start() + 6000)
        card = page[m.start():end]
        m_img = re.search(r'<img[^>]*\ssrcset="([^"\s,]+)', card)
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<img[^>]*>", " ", card))).split())
        text = re.sub(r'^href="[^"]*"\s*\S*>\s*', "", text)
        if "SOLD OUT" in text:
            continue  # 売り切れは通知しても買えない
        m_price = re.search(r"¥\s*([\d,]+)", text)
        name = text.split("¥")[0]
        name = re.sub(r"^(SOLD OUT\s*)?(ALLU公式\s*)?", "", name).strip() or "(名前不明)"
        if m_price:
            price_num = int(m_price.group(1).replace(",", ""))
            price = "¥{:,}".format(price_num)
        else:
            price_num, price = None, "(価格不明)"
        items.append({
            "id": iid,
            "title": name,
            "brand": "",
            "price": price,
            "price_num": price_num,
            "url": f"{BASE}/jp/ja/market/items/{iid}/",
            "image": m_img.group(1) if m_img else None,
            "shop": SHOP_NAME,
            "category": category_label,
        })
    return items


def fetch_brand_items(brand):
    """ブランド設定を受け取り、その売り場(新着順30件)のうち売り切れでない商品を返す。
    brand = {"name": 通知に出す名前, "keyword": 見た記録の名前, "code": "b-95", "category": "c-28"}
    """
    url = LIST_URL.format(code=brand["code"], category=brand.get("category", "c-28"))
    items = parse_items(fetch_html(url))
    for it in items:
        it["brand"] = brand["name"]
    return items
