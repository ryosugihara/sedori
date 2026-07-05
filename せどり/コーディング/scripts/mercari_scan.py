# -*- coding: utf-8 -*-
"""
メルカリ内 仕入れスキャン

記憶した売却実例（相場DB）をもとに、メルカリで『いま販売中』の商品から
「同じデザインが高く売れているのに、安く出ている物」を探して通知する。

流れ:
  1. 対象キーワード（アーカイブバッグ等）で販売中の商品を取得
  2. 1つずつ写真を相場DB(売り切れ実例)と照合（2つのAIの合わせ技）
  3. 『同デザイン ＋ 予想利益が通知ライン以上』をDiscordへ
     （無ければ「似た系統」の利益候補を参考として少しだけ送る）
"""

import os
import time

import monitor      # 通知・除外・設定の部品を再利用
import souba_match  # 画像照合の部品
import mercari      # メルカリ検索の部品

REPORT_FILE = "せどり/データ/recon/MERCARI_SCAN.txt"

# (照合に使うブランド名, メルカリの検索キーワード)
DEFAULT_TARGETS = [
    ("MIU MIU", "ミュウミュウ アーカイブ バッグ"),
    ("MIU MIU", "ミュウミュウ アーカイブ"),
    ("PRADA", "プラダ アーカイブ バッグ"),
    ("PRADA", "プラダ アーカイブ"),
    ("GUCCI", "グッチ アーカイブ バッグ"),
    ("GUCCI", "グッチ アーカイブ"),
]


def load_targets():
    """環境変数 SCAN_TARGETS（「ブランド|キーワード」を改行区切り）があればそれを使う"""
    raw = os.environ.get("SCAN_TARGETS", "").strip()
    if not raw:
        return DEFAULT_TARGETS
    out = []
    for line in raw.splitlines():
        if "|" in line:
            brand, kw = line.split("|", 1)
            out.append((brand.strip(), kw.strip()))
    return out or DEFAULT_TARGETS


def main():
    if not souba_match.ready():
        monitor.send_text("🛒 メルカリ内スキャン中止：相場DBかAIの準備が揃っていません。")
        return
    souba = monitor.load_souba()
    excludes = monitor.load_excludes()

    strict = []   # 同デザイン＋利益あり（本命）
    loose = []    # 似た系統＋利益あり（参考）
    seen = set()
    checked = 0

    for brand, kw in load_targets():
        items = mercari.fetch_on_sale(kw)
        time.sleep(1.5)
        print(f"「{kw}」 販売中 {len(items)}件")
        for raw in items:
            if raw["id"] in seen:
                continue
            seen.add(raw["id"])
            it = {
                "id": raw["id"],
                "brand": brand,
                "title": raw["name"],
                "price": "¥{:,}".format(raw["price"]),
                "price_num": raw["price"],
                "url": f"https://jp.mercari.com/item/{raw['id']}",
                "image": raw.get("image"),
                "shop": "メルカリ(販売中)",
                "category": "",
            }
            if monitor.is_excluded(it, excludes):
                continue
            m = souba_match.match_item(it, souba)
            checked += 1
            if not m or m["profit"] is None or m["profit"] < souba["notify_line"]:
                continue
            it["img_match"] = m
            if m["rank"] == "同デザイン":
                strict.append(it)
            else:
                loose.append(it)

    strict.sort(key=lambda x: x["img_match"]["profit"], reverse=True)
    loose.sort(key=lambda x: x["img_match"]["profit"], reverse=True)

    lines = [f"メルカリ内スキャン  照合{checked}件 → 同デザイン{len(strict)}件 / 似た系統{len(loose)}件"]
    for it in (strict + loose)[:10]:
        m = it["img_match"]
        lines.append(f"- [{m['rank']}] {it['title'][:40]} {it['price']} "
                     f"利益¥{m['profit']:,} {it['url']}")
    report = "\n".join(lines)
    os.makedirs("せどり/データ/recon", exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)

    if strict:
        monitor.send_text(
            f"🛒 メルカリ内スキャン結果：🟢 **同デザインの売却実例より安い出品**を "
            f"{len(strict)} 件みつけました（利益2,000円以上のみ）"
        )
        monitor.send_items(strict[:10])
    if loose:
        monitor.send_text(
            f"🟡 参考：**似た系統で利益が出そうな出品** 上位{min(len(loose), 10)}件"
            "（同じ商品と断定はできていません。カードの上下の写真を見比べて判断してください）"
        )
        monitor.send_items(loose[:10])
    if not strict and not loose:
        monitor.send_text(
            f"🛒 メルカリ内スキャン結果：{checked}件を照合しましたが、"
            "利益が出そうな出品は今はありませんでした。"
        )


if __name__ == "__main__":
    main()
