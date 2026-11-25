# -*- coding: utf-8 -*-
"""
高山質店オンライン（takayama78online.jp）用の読み取り部品

この店の商品一覧は検索ページ（/shop/goods/search.aspx）にしか出ない。
検索ページは robots.txt でロボットのアクセスを断っているが、2026-11-25 に
利用者(オーナー)が高山質店から許可を得たうえで監視している（頻度は10分ごとで合意）。
許可の範囲とお店への負担を守るため、次のルールで読む:
  ・新着順(sort=rgd)の1ページ目だけを読む（新着の確認には十分）
  ・同じ検索は INTERVAL_SECONDS(10分)に1回だけ。それ以外は前回の結果を返す
  ・読み込みと読み込みの間は最低 GAP_SECONDS(10秒)あける
  ・圧縮(gzip)して受け取り、通信量を減らす
ページは Shift_JIS(cp932)。商品は class="StyleT_Item_" のかたまりごとに並ぶ。
"""

import gzip
import html
import re
import time
import urllib.parse
import urllib.request

SHOP_NAME = "高山質店"
BASE = "https://takayama78online.jp"
SEARCH_URL = BASE + "/shop/goods/search.aspx?search=x&sort=rgd&name={name}&tree={tree}"
INTERVAL_SECONDS = 600  # 同じ検索を読む間隔（許可の範囲: 10分ごと）
GAP_SECONDS = 10        # 読み込みと読み込みの間を最低何秒あけるか
TREE_NAMES = {"80": "メンズファッション", "90": "レディースファッション"}

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

_cache = {}               # (検索語, カテゴリ) → (読んだ時刻, 商品リスト)
_last_request = [0.0]     # 最後に読みに行った時刻


def _get(url):
    """URLを読んで文字列(cp932→str)で返す。前回の読み込みから GAP_SECONDS 以上あける"""
    wait = GAP_SECONDS - (time.time() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=40) as res:
            raw = res.read()
            if (res.headers.get("Content-Encoding") or "").lower() == "gzip":
                raw = gzip.decompress(raw)
    finally:
        _last_request[0] = time.time()
    return raw.decode("cp932", errors="replace")


def parse_items(page, category=""):
    """検索結果のページから、商品の一覧（辞書のリスト）を取り出す。売り切れは除く"""
    items = []
    seen = set()
    for block in page.split('class="StyleT_Item_"')[1:]:
        m = re.search(r'href="/shop/g/g(\d+)/"\s+title="([^"]*)"', block)
        if not m or m.group(1) in seen:
            continue
        code = m.group(1)
        seen.add(code)
        title = html.unescape(m.group(2)).strip() or "(名前不明)"

        stock = re.search(r'class="stock_name_">([^<]*)<', block)
        stock_text = stock.group(1).strip() if stock else ""
        if any(w in stock_text for w in ("なし", "切", "SOLD")):
            continue  # 売り切れは通知しても買えない

        m_img = re.search(r'<img[^>]+src="(https?://[^"]+)"', block)
        m_price = re.search(r'class="price_[^"]*"[^>]*>\s*([\d,]+)\s*円', block)
        if m_price:
            price_num = int(m_price.group(1).replace(",", ""))
            price = "¥{:,}".format(price_num)
        else:
            price_num, price = None, "(価格不明)"

        items.append({
            "id": code,
            "title": title,
            "brand": "",
            "price": price,
            "price_num": price_num,
            "url": f"{BASE}/shop/g/g{code}/",
            "image": m_img.group(1) if m_img else None,
            "shop": SHOP_NAME,
            "category": category,
        })
    return items


def fetch_brand_items(brand):
    """ブランド設定(辞書)を受け取り、そのブランドの新着順1ページ目の商品一覧を返す。
    brand = {"name": 通知に出す名前, "keyword": 検索語, "tree": "80"(メンズ)/"90"(レディース)}
    同じ検索は INTERVAL_SECONDS に1回しか読まない（それ以外は前回の結果を返す）。
    """
    keyword = brand["keyword"]
    tree = str(brand.get("tree", "80"))
    key = (keyword, tree)
    now = time.time()
    cached = _cache.get(key)
    if cached and now - cached[0] < INTERVAL_SECONDS:
        return cached[1]
    url = SEARCH_URL.format(name=urllib.parse.quote(keyword.encode("cp932")), tree=tree)
    try:
        items = parse_items(_get(url), TREE_NAMES.get(tree, ""))
    except Exception:
        # 失敗しても、すぐに読み直してお店に負担をかけないよう、次の読み込みは10分後にする
        _cache[key] = (now, cached[1] if cached else [])
        raise
    for it in items:
        it["brand"] = brand["name"]
    _cache[key] = (now, items)
    return items
