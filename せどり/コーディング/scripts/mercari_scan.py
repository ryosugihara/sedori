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
import sys
import time

import monitor      # 通知・除外・設定の部品を再利用
import souba_match  # 画像照合の部品
import mercari      # メルカリ検索の部品

REPORT_FILE = "せどり/データ/recon/MERCARI_SCAN.txt"
SEEN_FILE = "せどり/データ/state/mercari_scan_seen.json"  # 送信済み商品の記録(重複通知防止)
STATS_FILE = "せどり/データ/recon/MERCARI_SCAN_STATS.txt"  # 「なぜ通知に至らなかったか」の診断レポート

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


MAX_SENT = int(os.environ.get("SCAN_MAX", "20"))  # 通知しすぎ防止の上限

# ===== 優先スキャン(高頻度ループ) =====================================
# メルカリは個人間売買のため、良い出品はすぐ他の買い手に取られてしまう。
# 通常のスキャン(1日1回・全107キーワード)では間に合わないブランドだけ、
# KINDAL新着監視と同じ「短い間隔でループし続ける」方式で見張る。
# 対象は watch_mercari.json 内の該当ブランドのキーワードをそのまま使う
# （増やしたい時はここにブランド名を足すだけでよい）。
PRIORITY_BRANDS = ["Balenciaga", "Saint Laurent"]
PRIORITY_SEEN_FILE = SEEN_FILE  # 通常スキャンと同じ記録を共有（二重通知防止）


def load_priority_targets():
    data = monitor.load_json_file(WATCH_MERCARI_FILE, {"brands": []})
    return [(b.get("name", ""), kw)
            for b in data.get("brands", [])
            for kw in b.get("keywords", [])
            if b.get("name") in PRIORITY_BRANDS]


def try_match_and_send(raw, brand, souba, excludes, notified_before, stats, tag=""):
    """1商品を照合し、条件を満たせば即送信する。送信したらTrueを返す。"""
    if raw["id"] in notified_before:
        return False  # 前回までのスキャンで既に送信済み
    # メルカリAPIは状態を文字列で返す('1'〜'6')ため str で比較する。
    # ('1'=新品、未使用。中古せどりの対象外。以前は数値1と比較して常に不一致だった)
    if str(raw.get("condition_id")) == "1":
        return False  # 新品未使用は中古せどりの対象外
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
        return False
    m = souba_match.match_item(it, souba, stats=stats, min_profit=souba["notify_line"])
    # 「同じと確認できた同デザイン」だけ送る（未確認の「似た系統」は送らない）
    if (not m or m["rank"] != "同デザイン"
            or m["profit"] is None or m["profit"] < souba["notify_line"]):
        return False
    it["img_match"] = m
    monitor.send_text(f"{tag}🟢 **同デザインの売却実例より安い出品**をみつけました（予想利益 約¥{m['profit']:,}）")
    monitor.send_items([it])
    notified_before.add(raw["id"])
    monitor.save_json_file(PRIORITY_SEEN_FILE, sorted(notified_before))
    return True


def priority_scan_once(souba, excludes, notified_before, stats):
    """優先ブランドのキーワードを1周だけ調べる。送信した件数を返す。"""
    sent = 0
    for brand, kw in load_priority_targets():
        try:
            items = mercari.fetch_on_sale(kw)
        except Exception as e:
            print(f"  取得失敗 ({kw}): {e}")
            continue
        for raw in items:
            if try_match_and_send(raw, brand, souba, excludes, notified_before, stats, tag="⚡【優先】"):
                sent += 1
        time.sleep(1.0)
    return sent


def priority_loop():
    """優先ブランドだけを高頻度(既定30秒間隔)で見張り続ける（KINDAL新着監視と同じ方式）。
    メルカリは個人間売買で良い出品がすぐ売れてしまうため、1日1回のスキャンでは
    間に合わない『買い付け競争が起きやすいブランド』専用のループ。
    """
    if not souba_match.ready():
        # 相場DB(Release)が消えている等で準備が整わない時は、Discordに通知せず
        # 異常終了(exit 1)する。ここで通知＋正常終了すると、ワークフローの
        # 自動再起動(if:success)が1分ごとに走り、同じ中止メッセージを大量送信して
        # しまう（実際に1957回の暴走が起きた）。exit 1 なら自動再起動が止まる。
        print("優先スキャン中止：相場DBかAIの準備が整っていません（通知せず終了）")
        sys.exit(1)
    souba = monitor.load_souba()
    excludes = monitor.load_excludes()
    notified_before = set(monitor.load_json_file(PRIORITY_SEEN_FILE, []))
    poll_seconds = int(os.environ.get("POLL_SECONDS", "30"))
    loop_minutes = int(os.environ.get("LOOP_MINUTES", "300"))
    targets = load_priority_targets()
    print(f"優先スキャンループ開始: {[b for b, _ in targets]} / {poll_seconds}秒ごと・最長{loop_minutes}分")
    if not targets:
        print("優先スキャン中止：対象ブランドのキーワードがありません（通知せず終了）")
        sys.exit(1)

    end_time = time.time() + loop_minutes * 60
    total_sent = 0
    while True:
        stats = {}
        sent = priority_scan_once(souba, excludes, notified_before, stats)
        total_sent += sent
        if sent:
            print(f"  優先スキャン 1周: {sent}件送信")
        if time.time() >= end_time:
            break
        time.sleep(poll_seconds)
    print(f"優先スキャンループ 終了（合計送信 {total_sent}件）")

# ===== ここまで(優先スキャン) ==========================================


def main():
    if os.environ.get("PRIORITY_LOOP") == "1":
        priority_loop()
        return

    if not souba_match.ready():
        monitor.send_text("🛒 メルカリ内スキャン中止：相場DBかAIの準備が揃っていません。")
        return
    souba = monitor.load_souba()
    excludes = monitor.load_excludes()
    notified_before = set(monitor.load_json_file(SEEN_FILE, []))
    stats = {}  # 診断レポート用の集計（match_item内部で加算される）
    total_fetched = 0  # 各キーワードで取得した商品の総数（除外・重複含む）

    # 全キーワードを調べ終えてからまとめて送信すると、100件近いキーワードの
    # 走査に数時間かかるため、見つけた頃には売り切れてしまう。
    # そのため『見つかり次第すぐ送信』する方式にする（利益順には並べられない）。
    seen = set()
    checked = 0
    sent_count = 0
    strict_n = 0
    loose_n = 0
    report_lines = []

    for brand, kw in load_targets():
        if sent_count >= MAX_SENT:
            break
        items = mercari.fetch_on_sale(kw)
        time.sleep(1.5)
        print(f"「{kw}」 販売中 {len(items)}件")
        for raw in items:
            total_fetched += 1
            if sent_count >= MAX_SENT:
                break
            if raw["id"] in seen:
                continue
            seen.add(raw["id"])
            if raw["id"] in notified_before:
                continue  # 前回までのスキャンで既に送信済み
            # メルカリAPIは状態を文字列で返す('1'〜'6')ため str で比較する。
            if str(raw.get("condition_id")) == "1":
                continue  # 新品未使用は中古せどりの対象外
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
            m = souba_match.match_item(it, souba, stats=stats, min_profit=souba["notify_line"])
            checked += 1
            # 「同じと確認できた同デザイン」だけ送る。以前は未確認の「似た系統」も
            # 送っていたため『違う商品が来る』原因になっていた（断定できない物は送らない）。
            if (not m or m["rank"] != "同デザイン"
                    or m["profit"] is None or m["profit"] < souba["notify_line"]):
                continue
            # 見つけたその場ですぐ送信し、記録も即保存する
            # （まだ売り切れていないうちに知らせるため）
            it["img_match"] = m
            strict_n += 1
            monitor.send_text(
                f"🛒 🟢 **同デザインの売却実例より安い出品**をみつけました"
                f"（予想利益 約¥{m['profit']:,}）"
            )
            monitor.send_items([it])
            notified_before.add(raw["id"])
            monitor.save_json_file(SEEN_FILE, sorted(notified_before))
            report_lines.append(f"- [{m['rank']}] {it['title'][:40]} {it['price']} "
                                 f"利益¥{m['profit']:,} {it['url']}")
            sent_count += 1

    print(f"メルカリ内スキャン  照合{checked}件 → 同デザイン{strict_n}件 / 似た系統{loose_n}件")

    report = (f"メルカリ内スキャン  照合{checked}件 → 同デザイン{strict_n}件 / 似た系統{loose_n}件\n"
              + "\n".join(report_lines))
    os.makedirs("せどり/データ/recon", exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)

    diag = monitor.write_scan_diagnostics(STATS_FILE, total_fetched, checked, stats, sent_count)

    if sent_count == 0:
        monitor.send_text(
            f"🛒 メルカリ内スキャン結果：{checked}件を照合しましたが、"
            "利益が出そうな出品は今はありませんでした。"
        )
        monitor.send_text(diag)


if __name__ == "__main__":
    main()
