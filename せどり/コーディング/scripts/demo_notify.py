# -*- coding: utf-8 -*-
"""
デモ通知スクリプト（RINKAN）
　今あるRINKANのサンローラン商品を「新着が出た」と仮定して、
　本物の新着通知とまったく同じ形でDiscordに送ります。
　※「見た商品リスト(state)」には一切触れません（本番の監視に影響なし）。
"""

import monitor  # 通知の送信部品を再利用
import rinkan   # RINKANの読み取り部品

DEMO_COUNT = 3  # デモで送る件数

# RINKANのサンローランを新着順で取得し、先頭から数件を選ぶ
items = rinkan.fetch_brand_items({"name": "Saint Laurent", "keyword": "saint laurent"})
items = items[:DEMO_COUNT]

# まず説明を送ってから、本物と同じカード形式で通知する
monitor.send_text(
    "🧪 デモ通知（RINKAN）：今ある商品を『新着』と仮定して送ります"
    "（本物の新着通知と、まったく同じ見た目です）"
)
monitor.send_items(items)
print(f"RINKANのデモ通知 {len(items)} 件を送信しました")
