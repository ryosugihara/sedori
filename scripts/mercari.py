# -*- coding: utf-8 -*-
"""
メルカリ 相場取得 部品（売り切れ＝実際に売れた値段から相場を出す）

使い方（他のプログラムから）:
    import mercari
    souba = mercari.get_souba("サンローラン スキニー 黒 S")
    print(souba["median"], souba["count"])

メルカリの検索API(鍵付き入口)に DPoP という使い捨て署名を付けてアクセスします。
必要ライブラリ: pyjwt, cryptography
"""

import os
import re
import json
import time
import uuid
import base64
import statistics
import urllib.request
import urllib.error

import jwt  # PyJWT
from cryptography.hazmat.primitives.asymmetric import ec

API_URL = "https://api.mercari.jp/v2/entities:search"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _make_dpop(url, method="POST"):
    """DPoP（使い捨ての電子署名）を1つ作る。毎回その場で鍵を作る。"""
    key = ec.generate_private_key(ec.SECP256R1())
    nums = key.public_key().public_numbers()
    jwk = {
        "crv": "P-256", "kty": "EC",
        "x": _b64u(nums.x.to_bytes(32, "big")),
        "y": _b64u(nums.y.to_bytes(32, "big")),
    }
    headers = {"typ": "dpop+jwt", "alg": "ES256", "jwk": jwk}
    payload = {
        "iat": int(time.time()),
        "jti": str(uuid.uuid4()),
        "htu": url, "htm": method,
        "uuid": str(uuid.uuid4()),
    }
    return jwt.encode(payload, key, algorithm="ES256", headers=headers)


def _search_body(keyword, page_size=120):
    """検索APIに送るデータ。status=売り切れ で実売相場を狙う。"""
    return {
        "userId": "", "pageSize": page_size, "pageToken": "",
        "searchSessionId": uuid.uuid4().hex,
        "indexRouting": "INDEX_ROUTING_UNSPECIFIED",
        "thumbnailTypes": [],
        "searchCondition": {
            "keyword": keyword, "excludeKeyword": "",
            "sort": "SORT_CREATED_TIME", "order": "ORDER_DESC",
            "status": ["STATUS_SOLD_OUT"],
            "sizeId": [], "categoryId": [], "brandId": [], "sellerId": [],
            "priceMin": 0, "priceMax": 0, "itemConditionId": [],
            "shippingPayerId": [], "shippingFromArea": [], "shippingMethod": [],
            "colorId": [], "hasCoupon": False, "attributes": [],
            "itemTypes": [], "skuIds": [], "shopIds": [],
        },
        "defaultDatabaseId": "", "serviceFrom": "suruga",
        "withItemBrand": True, "withItemSize": True,
    }


def _to_int(v):
    """'1719...'のような文字列の数字を int にする（できなければ None）"""
    try:
        return int(v)
    except Exception:
        return None


def fetch_sold(keyword, page_size=120):
    """キーワードで売り切れ商品を取得し、商品の辞書リストを返す（失敗時は空）"""
    body = json.dumps(_search_body(keyword, page_size)).encode("utf-8")
    req = urllib.request.Request(API_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "*/*")
    req.add_header("X-Platform", "web")
    req.add_header("DPoP", _make_dpop(API_URL, "POST"))
    req.add_header("User-Agent", UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            data = json.loads(res.read().decode("utf-8"))
    except Exception as e:
        print(f"  メルカリ取得失敗 ({keyword}): {e}")
        return []
    out = []
    for it in data.get("items", []):
        # メルカリショップ（業者の店）は除外する。
        # 理由: ①業者価格なので個人間の相場とズレる ②商品リンクの形式が違い開けない。
        # 普通のメルカリの商品IDは「m+数字」（例 m12345678901）なので、それ以外を弾く。
        item_id = str(it.get("id", ""))
        if not re.fullmatch(r"m\d+", item_id):
            continue
        try:
            price = int(it.get("price"))
        except Exception:
            continue
        brand = (it.get("itemBrand") or {}).get("name", "") or ""
        size = (it.get("itemSize") or {}).get("name", "") if isinstance(it.get("itemSize"), dict) else ""
        thumbs = it.get("thumbnails") or []
        out.append({
            "id": it.get("id", ""),
            "name": it.get("name", ""),
            "price": price,
            "brand": brand,
            "size": size,
            "image": thumbs[0] if thumbs else "",          # 商品写真(サムネイル)のURL
            "thumbnails": thumbs,                           # 写真の一覧（精度測定に使う）
            "condition_id": it.get("itemConditionId"),      # 状態ランク(1=新品寄り〜6)
            # いつの取引か（updated=最終更新。売れた頃の時刻として使う）
            "updated": _to_int(it.get("updated")) or _to_int(it.get("created")),
        })
    return out


def get_souba(keyword, brand_keys=None, page_size=120):
    """キーワードの相場をまとめて返す。
    brand_keys を渡すと、商品名/ブランドにその語を含む物だけで計算（ノイズ除去）。
    返り値: {median, trim_mean, count, min, max, samples}
    median … 中央値（外れ値に強い、相場の代表値）
    trim_mean … 上下10%を除いた平均（参考）
    """
    items = fetch_sold(keyword, page_size)
    if brand_keys:
        keys = [k.lower() for k in brand_keys]
        items = [
            it for it in items
            if any(k in (it["name"] + " " + it["brand"]).lower() for k in keys)
        ]
    prices = sorted(it["price"] for it in items)
    if not prices:
        return {"median": None, "trim_mean": None, "count": 0,
                "min": None, "max": None, "samples": []}
    k = len(prices) // 10
    trimmed = prices[k:len(prices) - k] if k else prices
    return {
        "median": int(statistics.median(prices)),
        "trim_mean": int(statistics.mean(trimmed)),
        "count": len(prices),
        "min": prices[0],
        "max": prices[-1],
        "samples": items[:5],
    }


# --- 手動の相場チェック（ワークフローから KEYWORD を渡して実行）-----------
def _send_discord(message):
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        print(message)
        return
    data = json.dumps({"content": message[:1900]}).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=data,
        headers={"Content-Type": "application/json",
                 "User-Agent": "sedori-bot/1.0 (+https://github.com/ryosugihara/sedori)"},
    )
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception as e:
        print(f"Discord 送信失敗: {e}")


def main():
    # 環境変数 KEYWORD（複数なら改行で区切る）で相場を調べてDiscordに送る
    raw = os.environ.get("KEYWORD", "").strip()
    if not raw:
        print("KEYWORD が未設定です")
        return
    keywords = [k.strip() for k in raw.splitlines() if k.strip()]
    lines = ["🔎 メルカリ相場チェック（売り切れ）", ""]
    for kw in keywords:
        s = get_souba(kw)
        if s["count"] == 0:
            lines.append(f"■ {kw}\n   データ取得できず")
        else:
            lines.append(
                f"■ {kw}\n"
                f"   相場(中央値) ¥{s['median']:,} ／ 参考平均 ¥{s['trim_mean']:,}\n"
                f"   {s['count']}件（¥{s['min']:,}〜¥{s['max']:,}）"
            )
        time.sleep(1.5)
    msg = "\n".join(lines)
    print(msg)
    _send_discord(msg)


if __name__ == "__main__":
    main()
