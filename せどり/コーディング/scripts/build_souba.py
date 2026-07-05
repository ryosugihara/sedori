# -*- coding: utf-8 -*-
"""
メルカリ相場DBを『一度だけ』作るプログラム

考え方:
  - メルカリは常時アクセスせず、ここでまとめて1回だけ相場を取得して保存する。
  - 元ネタは data/sold/sold_items.json（あなたが送った売却デザイン・色・サイズ付き）。
  - 各デザインの「今のメルカリ売り切れ相場(中央値)」を取って、
    data/mercari_souba.json に保存する。
  - この保存データを使って、他サイト(KINDAL等)の監視で利益判定する。

必要ライブラリ: pyjwt, cryptography（ワークフローで pip install）
"""

import os
import re
import json
import time
import datetime

import mercari  # 同じフォルダの mercari.py（相場取得の部品）

SOLD_DB_FILE = "data/sold/sold_items.json"
OUT_FILE = "data/mercari_souba.json"
REQUEST_WAIT = 1.5  # メルカリに優しく、1件ごとに少し待つ


def build_keyword(item):
    """売却デザインから、メルカリ検索に使う言葉を組み立てる。
    型番名から『季節記号(数字入り)』を除き、色も足して具体的にする。
    例: Dior Homme / AW07 翼 Tシャツ / グレー → 'Dior Homme 翼 Tシャツ グレー'
    """
    brand = item.get("brand", "")
    model = item.get("model", "")
    color = (item.get("color", "") or "").split("×")[0]  # 「黒×赤」は「黒」だけ使う
    model = re.sub(r"[（(].*?[)）]", "", model)  # 「(黒×レザー)」などカッコ書きを除く
    words = [w for w in model.split() if not any(c.isdigit() for c in w)]
    parts = [brand, " ".join(words), color]
    return " ".join(p for p in parts if p).strip()


def main():
    if not os.path.exists(SOLD_DB_FILE):
        print(f"{SOLD_DB_FILE} が見つかりません")
        return
    with open(SOLD_DB_FILE, "r", encoding="utf-8") as f:
        sold = json.load(f).get("items", [])

    print(f"相場DB作成: {len(sold)} デザイン分のメルカリ相場を取得します")
    results = []
    for i, it in enumerate(sold, start=1):
        keyword = build_keyword(it)
        s = mercari.get_souba(keyword)
        rec = {
            "id": it.get("id"),
            "brand": it.get("brand"),
            "item_type": it.get("item_type"),
            "model": it.get("model"),
            "color": it.get("color"),
            "size": it.get("size"),
            "keyword": keyword,
            "median": s["median"],
            "trim_mean": s["trim_mean"],
            "count": s["count"],
            "min": s["min"],
            "max": s["max"],
        }
        results.append(rec)
        mark = f"¥{s['median']:,}（{s['count']}件）" if s["count"] else "データ無し"
        print(f"  [{i}/{len(sold)}] {keyword[:34]:34} → {mark}")
        time.sleep(REQUEST_WAIT)

    out = {
        "_説明": "メルカリの売り切れ相場を一度だけ取得して保存したものです（監視の利益判定に使う）。",
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "items": results,
    }
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    got = sum(1 for r in results if r["count"])
    print(f"保存しました: {OUT_FILE}（相場が取れた: {got}/{len(results)} 件）")


if __name__ == "__main__":
    main()
