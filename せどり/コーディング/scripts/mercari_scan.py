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
SEEN_FILE = "せどり/データ/state/mercari_scan_seen.json"  # 送信済み商品の記録(重複通知防止)
CHECKED_FILE = "せどり/データ/state/mercari_scan_checked.json"  # 調べたが利益無しだった商品と、調べた日時

# SCAN_TARGETSも相場収集リストも無い時だけ使う最終フォールバック
DEFAULT_TARGETS = [
    ("MIU MIU", "ミュウミュウ アーカイブ バッグ"),
    ("MIU MIU", "ミュウミュウ アーカイブ"),
    ("PRADA", "プラダ アーカイブ バッグ"),
    ("PRADA", "プラダ アーカイブ"),
    ("GUCCI", "グッチ アーカイブ バッグ"),
    ("GUCCI", "グッチ アーカイブ"),
]

WATCH_MERCARI_FILE = "せどり/データ/watchlists/watch_mercari.json"


def load_all_brand_targets():
    """既定の対象：相場収集リスト(watch_mercari.json)の全ブランド・全キーワード。
    以前は③ブランドだけの手打ちリストだったが、監視中の全ブランド(23種・
    キーワード計90件超)を対象にすることでスキャンの網羅性を上げる。
    """
    data = monitor.load_json_file(WATCH_MERCARI_FILE, {"brands": []})
    out = [(b.get("name", ""), kw)
           for b in data.get("brands", [])
           for kw in b.get("keywords", [])]
    return out or DEFAULT_TARGETS


def load_targets():
    """環境変数 SCAN_TARGETS（「ブランド|キーワード」を改行区切り）があればそれを使う。
    無ければ監視中の全ブランドを対象にする。
    """
    raw = os.environ.get("SCAN_TARGETS", "").strip()
    if not raw:
        return load_all_brand_targets()
    out = []
    for line in raw.splitlines():
        if "|" in line:
            brand, kw = line.split("|", 1)
            out.append((brand.strip(), kw.strip()))
    return out or load_all_brand_targets()


def main():
    if not souba_match.ready():
        monitor.send_text("🛒 メルカリ内スキャン中止：相場DBかAIの準備が揃っていません。")
        return
    souba = monitor.load_souba()
    excludes = monitor.load_excludes()
    notified_before = set(monitor.load_json_file(SEEN_FILE, []))
    checked_before = monitor.load_json_file(CHECKED_FILE, {})  # {id: 最後に調べた時刻}
    now_ts = time.time()
    checked_now = {}  # 今回あらたに「利益無し」と分かった物

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
            if raw["id"] in notified_before:
                continue  # 前回までのスキャンで既に送信済み
            last_checked = checked_before.get(raw["id"])
            if last_checked and now_ts - last_checked < monitor.RECHECK_SECONDS:
                continue  # 30日以内に調べて利益無しだった商品はスキップ
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
                checked_now[raw["id"]] = now_ts
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

    # 「調べたが利益無しだった」記録は、通知の有無に関わらず必ず保存する
    checked_before.update(checked_now)
    monitor.save_json_file(CHECKED_FILE, checked_before)

    sent = []
    if strict:
        monitor.send_text(
            f"🛒 メルカリ内スキャン結果：🟢 **同デザインの売却実例より安い出品**を "
            f"{len(strict)} 件みつけました（利益2,000円以上のみ）"
        )
        monitor.send_items(strict[:10])
        sent += strict[:10]
    if loose:
        monitor.send_text(
            f"🟡 参考：**似た系統で利益が出そうな出品** 上位{min(len(loose), 10)}件"
            "（同じ商品と断定はできていません。カードの上下の写真を見比べて判断してください）"
        )
        monitor.send_items(loose[:10])
        sent += loose[:10]
    if not strict and not loose:
        monitor.send_text(
            f"🛒 メルカリ内スキャン結果：{checked}件を照合しましたが、"
            "利益が出そうな出品は今はありませんでした。"
        )

    # 今回送信した分を「送信済み」として記録する（次回以降は重複通知しない）
    if sent:
        notified_before.update(it["id"] for it in sent)
        monitor.save_json_file(SEEN_FILE, sorted(notified_before))


if __name__ == "__main__":
    main()
