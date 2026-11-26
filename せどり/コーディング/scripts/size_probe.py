# -*- coding: utf-8 -*-
"""各お店の商品ページから、サイズが読み取れるかを確かめる診断（GitHub上で実行）。
結果は せどり/データ/recon/SIZE_PROBE.txt に書く。服の商品を各店3件ずつだけ読む。"""
import json, os, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(__file__))
import item_size, allu

H = {"User-Agent": item_size.HEADERS["User-Agent"], "Accept": "application/json"}
CLOTHES = ["パンツ", "ジャケット", "シャツ", "ニット", "パーカー", "スウェット", "ブルゾン", "コート", "デニム", "カーディガン"]
out = []


def shopify(label, base, path):
    try:
        req = urllib.request.Request(f"{base}{path}", headers=H)
        prods = json.loads(urllib.request.urlopen(req, timeout=30).read()).get("products", [])
    except Exception as e:
        out.append(f"{label}: 一覧の取得失敗 {e}")
        return
    n = 0
    for p in prods:
        if not any(k in p.get("title", "") for k in CLOTHES):
            continue
        it = {"title": p["title"], "brand": p.get("vendor", ""), "category": p.get("product_type", ""),
              "url": f"{base}/products/{p['handle']}"}
        st = item_size.size_text_of(it)
        out.append(f"{label}: {p['title'][:30]} → サイズ表記『{st}』→ 区分 {item_size.to_bucket(st, it)}  {it['url']}")
        n += 1
        time.sleep(2)
        if n >= 3:
            break


shopify("KINDAL", "https://shop.kind.co.jp", "/collections/saint-laurent-paris/products.json?limit=60")
shopify("BRING", "https://wastenot-official.com", "/collections/saintlaurentparis/products.json?limit=60")
shopify("買取王国", "https://okoku.jp", "/products.json?limit=100")
try:
    for it in allu.fetch_brand_items({"name": "Saint Laurent", "code": "b-95", "category": "c-28"})[:2]:
        st = item_size.size_text_of(it)
        out.append(f"ALLU: {it['title'][:30]} → サイズ表記『{st}』→ 区分 {item_size.to_bucket(st, it)}  {it['url']}")
except Exception as e:
    out.append(f"ALLU: 失敗 {e}")
os.makedirs("せどり/データ/recon", exist_ok=True)
open("せどり/データ/recon/SIZE_PROBE.txt", "w", encoding="utf-8").write("\n".join(out) + "\n")
print("\n".join(out))
