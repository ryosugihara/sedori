# -*- coding: utf-8 -*-
"""
テスト通知スクリプト
　本物の新着通知が「ちゃんと届くか」を確かめるために、サンプルを1通だけ送ります。
"""

import os
import json
import urllib.request

# Discord のカギ（GitHub の Secrets から渡される）
webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
if not webhook:
    raise SystemExit("DISCORD_WEBHOOK_URL が設定されていません")

# 本物の新着通知と同じ「カード形式」のサンプル
payload = {
    "content": "🔔 これはテスト通知です（本物の新着が出ると、これと同じ形で届きます）",
    "embeds": [
        {
            "title": "【テスト】Saint Laurent サンプル商品",
            "url": "https://shop.kind.co.jp/collections/saint-laurent-paris",
            "description": (
                "🏷️ Saint Laurent\n"
                "💴 ¥99,800\n\n"
                "※これはテストです。実際の通知には商品の写真とリンクが付きます。"
            ),
        }
    ],
}

data = json.dumps(payload).encode("utf-8")
req = urllib.request.Request(
    webhook, data=data, headers={"Content-Type": "application/json"}
)
urllib.request.urlopen(req, timeout=30)
print("テスト通知を送信しました")
