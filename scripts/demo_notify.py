# -*- coding: utf-8 -*-
"""
デモ通知スクリプト
　今あるサンローランの商品を「新着が出た」と仮定して、
　本物の新着通知とまったく同じ形でDiscordに送ります。
　※「見た商品リスト(state)」には一切触れません（本番の監視に影響なし）。
"""

import monitor  # 本番の監視プログラムの部品を再利用する

DEMO_COLLECTION = "saint-laurent-paris"  # サンローランの売り場
DEMO_COUNT = 3                           # デモで送る件数

# 今ある商品を取得して、登録日時が新しい順に並べる
products = monitor.fetch_collection_products(DEMO_COLLECTION)
products.sort(key=lambda p: p.get("created_at", ""), reverse=True)

# 新しい方から数件を「通知用の情報」に変換
items = [monitor.build_item(p, "Saint Laurent") for p in products[:DEMO_COUNT]]

# まず説明を送ってから、本物と同じカード形式で通知する
monitor.send_text(
    "🧪 デモ通知です：今ある商品を『新着』と仮定して送ります"
    "（本物の新着通知と、まったく同じ見た目です）"
)
monitor.send_items(items)
print(f"デモ通知 {len(items)} 件を送信しました")
