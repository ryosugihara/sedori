# -*- coding: utf-8 -*-
"""
購入・売却の実績記録（予想利益が当たっているか・実際にいくら儲かったかを追跡する）

今までの仕組みは「Discordに通知する」ところまでで終わっていて、
その商品を実際に買ったか・いくらで転売できたかを記録する場所が無かった。
これが無いと、月300万円の目標にどれだけ近づいているかシステム側からは
一切わからない。このファイルはその記録・集計を行う。

使い方（環境変数でactionを指定）:
  ACTION=buy    ITEM_URL=... ITEM_TITLE=... ITEM_SHOP=... ITEM_BRAND=... PRICE=買値 [PREDICTED_PROFIT=通知時の予想利益]
  ACTION=sell   ITEM_URL=...(買った時と同じURL) PRICE=売れた値段
  ACTION=report （集計してDiscordに送る。値は不要）
"""

import os
import datetime

import monitor  # 通知・ファイル読み書きの部品を再利用

PURCHASES_FILE = "せどり/データ/state/purchases.json"


def load():
    return monitor.load_json_file(PURCHASES_FILE, {"items": []})


def save(data):
    monitor.save_json_file(PURCHASES_FILE, data)


def _find(items, url):
    """URLが一致する記録を探す（無ければNone）。売却記録はまだ売れていない物を優先。"""
    matches = [it for it in items if it.get("url") == url]
    if not matches:
        return None
    unsold = [it for it in matches if it.get("status") != "sold"]
    return unsold[0] if unsold else matches[-1]


def add_purchase(url, title="", shop="", brand="", buy_price=None, predicted_profit=None):
    """買った商品を記録する"""
    data = load()
    rec = {
        "url": url,
        "title": title,
        "shop": shop,
        "brand": brand,
        "buy_price": buy_price,
        "buy_date": datetime.date.today().isoformat(),
        "predicted_profit": predicted_profit,
        "status": "bought",
        "sell_price": None,
        "sell_date": None,
        "actual_profit": None,
    }
    data["items"].append(rec)
    save(data)
    return rec


def mark_sold(url, sell_price, souba):
    """買った商品が売れたことを記録し、実現利益を計算する"""
    data = load()
    rec = _find(data["items"], url)
    if rec is None:
        return None
    fee, ship = souba["fee"], souba["shipping"]
    net = int(sell_price * (1 - fee) - ship)
    rec["sell_price"] = sell_price
    rec["sell_date"] = datetime.date.today().isoformat()
    rec["status"] = "sold"
    if rec.get("buy_price") is not None:
        rec["actual_profit"] = net - rec["buy_price"]
    save(data)
    return rec


def build_report(data):
    """集計レポートの文章を作る"""
    items = data.get("items", [])
    bought = [it for it in items if it.get("status") == "bought"]
    sold = [it for it in items if it.get("status") == "sold"]

    holding_cost = sum(it.get("buy_price") or 0 for it in bought)
    realized = [it["actual_profit"] for it in sold if it.get("actual_profit") is not None]
    total_realized = sum(realized)

    lines = [
        "📒 購入・売却の実績レポート",
        f"保有中（買ったがまだ未売却）: {len(bought)}件・仕入合計¥{holding_cost:,}",
        f"売却済み: {len(sold)}件",
    ]
    if realized:
        lines.append(f"実現利益 合計: ¥{total_realized:,}（平均¥{total_realized // len(realized):,}/件）")
    else:
        lines.append("実現利益 合計: まだ売却記録がありません")

    # 予想利益 vs 実際の利益（両方わかる物だけ）
    compared = [it for it in sold
                if it.get("predicted_profit") is not None and it.get("actual_profit") is not None]
    if compared:
        diffs = [it["actual_profit"] - it["predicted_profit"] for it in compared]
        avg_diff = sum(diffs) // len(diffs)
        lines.append(
            f"予想利益との差（{len(compared)}件）: 平均{'+' if avg_diff >= 0 else ''}¥{avg_diff:,}"
            f"（実際が予想を上回れば＋）"
        )

    if bought:
        lines.append("")
        lines.append("【保有中の内訳】")
        for it in bought[-10:]:
            lines.append(f"  ・{(it.get('title') or '')[:30]} 仕入¥{it.get('buy_price') or 0:,}"
                          f"（{it.get('buy_date', '')}・{it.get('shop', '')}）")

    return "\n".join(lines)


def main():
    action = os.environ.get("ACTION", "report").strip().lower()

    if action == "buy":
        url = os.environ.get("ITEM_URL", "").strip()
        if not url:
            print("ITEM_URL が必要です")
            return
        price_raw = os.environ.get("PRICE", "").strip()
        price = int(price_raw) if price_raw else None
        pred_raw = os.environ.get("PREDICTED_PROFIT", "").strip()
        predicted = int(pred_raw) if pred_raw else None
        rec = add_purchase(
            url=url,
            title=os.environ.get("ITEM_TITLE", "").strip(),
            shop=os.environ.get("ITEM_SHOP", "").strip(),
            brand=os.environ.get("ITEM_BRAND", "").strip(),
            buy_price=price,
            predicted_profit=predicted,
        )
        msg = f"📥 購入記録: {rec['title'][:40] or rec['url']} 仕入¥{price:,}" if price else f"📥 購入記録: {rec['title'][:40] or rec['url']}"
        print(msg)
        monitor.send_text(msg)

    elif action == "sell":
        url = os.environ.get("ITEM_URL", "").strip()
        price_raw = os.environ.get("PRICE", "").strip()
        if not url or not price_raw:
            print("ITEM_URL と PRICE が必要です")
            return
        souba = monitor.load_souba()
        rec = mark_sold(url, int(price_raw), souba)
        if rec is None:
            msg = f"⚠️ 売却記録に失敗：該当する購入記録が見つかりません（{url}）"
        else:
            profit_txt = f"実現利益¥{rec['actual_profit']:,}" if rec.get("actual_profit") is not None else "仕入値未記録のため利益計算できず"
            msg = f"📤 売却記録: {rec['title'][:40] or rec['url']} 売値¥{int(price_raw):,}（{profit_txt}）"
        print(msg)
        monitor.send_text(msg)

    elif action == "report":
        data = load()
        report = build_report(data)
        print(report)
        monitor.send_text(report)

    else:
        print(f"不明なACTION: {action}（buy / sell / report のいずれかを指定してください）")


if __name__ == "__main__":
    main()
