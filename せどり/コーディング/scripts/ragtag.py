# -*- coding: utf-8 -*-
"""
RAGTAG（ラグタグ）オンライン用の読み取り部品

RAGTAG の自社サイト（www.ragtag.jp）は Akamai のボット対策で、自動の読み込みを
すべて拒否している（robots.txt すら 403。実測 2026-11-26）。そのため、
RAGTAG の運営会社（株式会社ティンパンアレイ）が Yahoo!ショッピングに出している
公式店『RAGTAG Online Shop』（store.shopping.yahoo.co.jp/ragtagonlineshop/）を見る。

  ・お店の中の検索ページ search.html?p=ブランド名&X=99 が「新着順」で30件並ぶ
  ・Yahoo の robots.txt はキーワード検索(p=)を許可。ブランド絞り込み(brandid=)や
    カテゴリ絞り込み(strcid=)は禁止なので使わない
  ・ページの中の __NEXT_DATA__（JSON）に 商品名・値段・写真・在庫切れ印 が入っている
  ・キーワード検索は説明文にも当たる（例: UNDERCOVER で LAST ORGY 2 が出る）ので、
    商品名に title_keywords のどれかを含む物だけに絞る
"""

import gzip
import json
import re
import urllib.parse
import urllib.request

SHOP_NAME = "RAGTAG"
STORE = "https://store.shopping.yahoo.co.jp/ragtagonlineshop"
SEARCH_URL = STORE + "/search.html?{query}"

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

_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def fetch_html(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as res:
        raw = res.read()
        if (res.headers.get("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def _find_result(o):
    """JSONの中から検索結果のかたまり(type=RESULT)を探す"""
    if isinstance(o, dict):
        if o.get("type") == "RESULT" and isinstance(o.get("content"), dict):
            return o
        for v in o.values():
            r = _find_result(v)
            if r:
                return r
    elif isinstance(o, list):
        for v in o:
            r = _find_result(v)
            if r:
                return r
    return None


def parse_items(page):
    m = _NEXT.search(page)
    if not m:
        raise RuntimeError("RAGTAG: ページの中に商品データ(__NEXT_DATA__)が見つからない")
    res = _find_result(json.loads(m.group(1)))
    if not res:
        return []
    out = []
    for x in res["content"].get("items") or []:
        if (x.get("labels") or {}).get("hasNoStock"):
            continue  # 在庫切れは通知しても買えない
        code = (x.get("itemId") or "").replace("ragtagonlineshop_", "")
        if not code:
            continue
        price_num = x.get("price")
        price = "¥{:,}".format(price_num) if isinstance(price_num, int) else "(価格不明)"
        name = " ".join((x.get("name") or "(名前なし)").replace("中古　古着", "").split())
        out.append({
            "id": code,
            "title": name,
            "brand": "",
            "price": price,
            "price_num": price_num if isinstance(price_num, int) else None,
            "url": f"{STORE}/{code}.html",
            "image": (x.get("image") or {}).get("imageUrl"),
            "shop": SHOP_NAME,
            "category": "",
        })
    return out


def fetch_brand_items(brand):
    """ブランド設定を受け取り、そのブランド名で検索した新着30件のうち、在庫ありの物を返す。
    brand = {"name": 通知に出す名前, "keyword": 見た記録の名前, "search": 検索する言葉,
             "title_keywords": [商品名に含まれていてほしい言葉...]}
    """
    query = urllib.parse.urlencode({"p": brand["search"], "X": "99"})
    items = parse_items(fetch_html(SEARCH_URL.format(query=query)))
    words = [w.lower() for w in brand.get("title_keywords", [])]
    out = []
    for it in items:
        if words and not any(w in it["title"].lower() for w in words):
            continue
        it["brand"] = brand["name"]
        out.append(it)
    return out
