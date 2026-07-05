# -*- coding: utf-8 -*-
"""
メルカリ 相場 自動取得テスト（実験）

メルカリの検索API(鍵付き入口)に、DPoPという使い捨て電子署名を付けて
アクセスし、本物の売り切れ相場（商品名・値段・状態）が取れるか試す。

必要ライブラリ: pyjwt, cryptography （ワークフローで pip install する）

※これは実験です。401/403で弾かれたり、本文(body)の形が違って
  エラーになる可能性があります。結果のJSONは recon に保存して中身を調べます。
"""

import os
import json
import time
import uuid
import base64
import datetime
import urllib.request
import urllib.error

import jwt  # PyJWT
from cryptography.hazmat.primitives.asymmetric import ec

API_URL = "https://api.mercari.jp/v2/entities:search"
OUTPUT_DIR = "せどり/データ/recon"

# 試す検索キーワード（色・サイズ違いで相場差を見る）
KEYWORDS = [
    "サンローラン スキニー 黒 S",
    "サンローラン スキニー 黒 M",
    "アンダーカバー デニム",
]


def b64u(b):
    """バイト列を URL-safe Base64（=なし）にする（JWTで使う形）"""
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def make_dpop(url, method="POST"):
    """DPoP（使い捨ての電子署名）を1つ作って返す。
    その場で鍵を作り、アクセス先URL・メソッドを署名に含める。
    """
    key = ec.generate_private_key(ec.SECP256R1())
    nums = key.public_key().public_numbers()
    jwk = {
        "crv": "P-256",
        "kty": "EC",
        "x": b64u(nums.x.to_bytes(32, "big")),
        "y": b64u(nums.y.to_bytes(32, "big")),
    }
    headers = {"typ": "dpop+jwt", "alg": "ES256", "jwk": jwk}
    payload = {
        "iat": int(time.time()),
        "jti": str(uuid.uuid4()),
        "htu": url,        # アクセス先URL
        "htm": method,     # メソッド(POST)
        "uuid": str(uuid.uuid4()),
    }
    return jwt.encode(payload, key, algorithm="ES256", headers=headers)


def search_body(keyword):
    """検索APIに送るデータ(body)。status=売り切れ で相場を狙う。"""
    return {
        "userId": "",
        "pageSize": 120,
        "pageToken": "",
        "searchSessionId": uuid.uuid4().hex,
        "indexRouting": "INDEX_ROUTING_UNSPECIFIED",
        "thumbnailTypes": [],
        "searchCondition": {
            "keyword": keyword,
            "excludeKeyword": "",
            "sort": "SORT_CREATED_TIME",
            "order": "ORDER_DESC",
            "status": ["STATUS_SOLD_OUT"],
            "sizeId": [],
            "categoryId": [],
            "brandId": [],
            "sellerId": [],
            "priceMin": 0,
            "priceMax": 0,
            "itemConditionId": [],
            "shippingPayerId": [],
            "shippingFromArea": [],
            "shippingMethod": [],
            "colorId": [],
            "hasCoupon": False,
            "attributes": [],
            "itemTypes": [],
            "skuIds": [],
            "shopIds": [],
        },
        "defaultDatabaseId": "",
        "serviceFrom": "suruga",
        "withItemBrand": True,
        "withItemSize": True,
        "withItemPromotions": False,
        "withItemSizes": True,
        "withShopname": False,
        "useDynamicAttribute": True,
        "withSuggestedItems": False,
        "withOfferPricePromotion": False,
        "withProductSuggest": False,
        "withParentProducts": False,
        "withProductArticles": False,
        "withSearchConditionId": False,
    }


def call_api(keyword):
    """1キーワード分、APIを叩いて (状態コード, 本文) を返す"""
    body = json.dumps(search_body(keyword)).encode("utf-8")
    req = urllib.request.Request(API_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "*/*")
    req.add_header("X-Platform", "web")
    req.add_header("DPoP", make_dpop(API_URL, "POST"))
    req.add_header(
        "User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return None, str(e).encode("utf-8")


def summarize_items(raw):
    """返ってきたJSONから、商品名・値段・状態を数件だけ取り出す"""
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return None, []
    items = data.get("items") or data.get("data") or []
    out = []
    for it in items[:5]:
        out.append({
            "name": it.get("name", ""),
            "price": it.get("price", ""),
            "status": it.get("status", ""),
            "id": it.get("id", ""),
        })
    return len(items), out


def send_discord(message):
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        print("DISCORD_WEBHOOK_URL 未設定のためスキップ")
        return
    data = json.dumps({"content": message[:1900]}).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "sedori-bot/1.0 (+https://github.com/ryosugihara/sedori)",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception as e:
        print(f"Discord 送信失敗: {e}")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"メルカリ 相場 自動取得テスト  {now}", ""]

    for i, kw in enumerate(KEYWORDS, start=1):
        status, raw = call_api(kw)
        size = len(raw) if raw else 0
        # 生データを保存（あとで中身を詳しく調べる）
        with open(os.path.join(OUTPUT_DIR, f"mercari_api_{i}.json"), "wb") as f:
            f.write(raw or b"")

        if status == 200:
            n, sample = summarize_items(raw)
            lines.append(f"[{i}] 「{kw}」 status=200 🟢 取得成功 件数={n}")
            for s in sample[:3]:
                lines.append(f"     - {s['name'][:30]} / ¥{s['price']} / {s['status']}")
        else:
            head = (raw[:160].decode("utf-8", "replace") if raw else "")
            lines.append(f"[{i}] 「{kw}」 status={status} 🔴 失敗 size={size}B")
            lines.append(f"     応答先頭: {head}")
        time.sleep(1.5)

    with open(os.path.join(OUTPUT_DIR, "SUMMARY.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    send_discord("【メルカリ 相場 自動取得テスト】\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
