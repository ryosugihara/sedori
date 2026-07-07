# -*- coding: utf-8 -*-
"""
出品者ベース ブランド発見スクリプト

やりたいこと:
  「アルマーニ・ディーゼル・バルマンのような服をよく売っている出品者は、
   他にも似た系統の掘り出し物ブランドを扱っているはず」という考えで、
   新しく相場を覚える価値がありそうなブランドの候補を探す。

流れ:
  1. 起点キーワード（例: アルマーニ）で「売り切れ」実績を検索し、
     よく出てくる出品者(sellerId)＝このジャンルの本気の出品者を見つける。
  2. その出品者たちの「今の出品」を全部のぞき見て、扱っているブランドを集計する。
  3. まだ監視していないブランドのうち、複数の出品者が扱っている物を候補として報告する。

※ 見つけた候補は自動では監視リストに追加しない（人が見て判断するための一覧）。
"""

import os
import time
from collections import Counter

import monitor  # 通知・設定の部品を再利用
import mercari  # メルカリ検索の部品

REQUEST_WAIT = 1.0
REPORT_FILE = "せどり/データ/recon/BRAND_DISCOVERY.txt"

# 起点にするブランドの検索キーワード（環境変数 SEED_KEYWORDS があれば改行区切りでそれを使う）
DEFAULT_SEEDS = ["アルマーニ", "ディーゼル", "バルマン"]

TOP_SELLERS = int(os.environ.get("TOP_SELLERS", "8"))          # 深掘りする出品者の人数
MIN_SELLER_HITS = int(os.environ.get("MIN_SELLER_HITS", "2"))  # 起点キーワードで何回以上出てきた出品者を対象にするか
MIN_CANDIDATE_SELLERS = int(os.environ.get("MIN_CANDIDATE_SELLERS", "2"))  # 候補にする最低人数


def load_seeds():
    raw = os.environ.get("SEED_KEYWORDS", "").strip()
    if raw:
        return [s.strip() for s in raw.splitlines() if s.strip()]
    return DEFAULT_SEEDS


def load_known_brands():
    """すでに相場収集リストに載っているブランド名の集合（小文字）を返す"""
    data = monitor.load_json_file(
        "せどり/データ/watchlists/watch_mercari.json", {"brands": []}
    )
    return {b.get("name", "").strip().lower() for b in data.get("brands", [])}


def find_active_sellers(seeds):
    """起点キーワードの売り切れ実績から、よく出てくる出品者IDを集める"""
    hits = Counter()
    for kw in seeds:
        items = mercari.fetch_sold(kw, page_size=120)
        print(f"  『{kw}』売り切れ実績: {len(items)} 件")
        for it in items:
            sid = it.get("seller_id")
            if sid:
                hits[sid] += 1
        time.sleep(REQUEST_WAIT)
    return hits


def peek_seller_shops(seller_ids):
    """出品者ごとに『今の出品』を取得し、ブランド別の点数・出品者数を集計する"""
    brand_counts = Counter()
    brand_sellers = {}
    for sid in seller_ids:
        items = mercari.fetch_on_sale("", page_size=120, seller_ids=[sid])
        print(f"  出品者 {sid}: 現在の出品 {len(items)} 件")
        for it in items:
            b = (it.get("brand") or "").strip()
            if not b:
                continue
            brand_counts[b] += 1
            brand_sellers.setdefault(b, set()).add(sid)
        time.sleep(REQUEST_WAIT)
    return brand_counts, brand_sellers


def main():
    seeds = load_seeds()
    known = load_known_brands()
    print(f"起点ブランド: {', '.join(seeds)}")

    hits = find_active_sellers(seeds)
    top_sellers = [sid for sid, cnt in hits.most_common() if cnt >= MIN_SELLER_HITS][:TOP_SELLERS]
    print(f"深掘りする出品者: {len(top_sellers)} 人（延べ {sum(hits.values())} 件の出品履歴から抽出）")

    if not top_sellers:
        monitor.send_text(
            "🔎 ブランド発見スキャン：起点ブランドで目立った出品者が見つかりませんでした。"
        )
        return

    brand_counts, brand_sellers = peek_seller_shops(top_sellers)

    candidates = [
        (b, cnt, len(brand_sellers[b]))
        for b, cnt in brand_counts.items()
        if b.lower() not in known and len(brand_sellers[b]) >= MIN_CANDIDATE_SELLERS
    ]
    candidates.sort(key=lambda x: (-x[2], -x[1]))

    lines = [
        "🔎 ブランド発見スキャン結果",
        f"起点: {', '.join(seeds)} / 深掘りした出品者: {len(top_sellers)} 人",
        "",
    ]
    if not candidates:
        lines.append("まだ監視していない候補ブランドは見つかりませんでした。")
    else:
        lines.append("未監視ブランドの候補（扱う出品者が多い順）:")
        for b, cnt, nsellers in candidates[:20]:
            lines.append(f"  ・{b} … {nsellers}人の出品者が計{cnt}点を出品中")

    report = "\n".join(lines)
    print(report)

    os.makedirs("せどり/データ/recon", exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report)

    monitor.send_text(report)


if __name__ == "__main__":
    main()
