# -*- coding: utf-8 -*-
"""
画像判定のテスト通知（デモ）

今お店(トレファク/RINKAN/オフモール)に並んでいる②ブランドの商品を
少しだけ取ってきて、メルカリ相場DB(3,984件)と写真で見比べる。
「似ている度が高い順」に上位5件をDiscordへ送る（利益は関係なく送る＝テスト用）。

本番との違い:
  本番 = 『同デザイン(類似度0.92以上) かつ 利益2,000円以上』だけ通知
  テスト = 類似度が高い順に5件、判定内容ごと全部見せる
"""

import json
import time
import os

import monitor      # 通知カードの部品を再利用
import souba_match  # 画像照合の部品
import trefac
import rinkan
import hardoff

PER_BRAND = 12  # 1ブランドあたり何件ためすか（多すぎると時間がかかる）
REPORT_FILE = "recon/DEMO_MATCH.txt"


def main():
    souba = monitor.load_souba()
    if not souba_match.ready():
        monitor.send_text("🧪 テスト中止：画像判定の準備(相場DBかAI)が揃っていません。")
        return

    sources = [
        (monitor.load_json_file(monitor.TREFAC_BRANDS_FILE, {"brands": []}).get("brands", []),
         trefac.fetch_brand_items),
        (monitor.load_json_file(monitor.RINKAN_BRANDS_FILE, {"brands": []}).get("brands", []),
         rinkan.fetch_brand_items),
        (monitor.load_json_file(monitor.HARDOFF_BRANDS_FILE, {"brands": []}).get("brands", []),
         hardoff.fetch_brand_items),
    ]

    candidates = []
    for brands, get_items in sources:
        for b in brands:
            if not b.get("profit_only"):
                continue  # テスト対象は②ブランドだけ
            try:
                items = get_items(b)
            except Exception as e:
                print(f"  取得失敗 ({b.get('keyword')}): {e}")
                continue
            for it in items[:PER_BRAND]:
                m = souba_match.match_item(it, souba)
                if m:
                    it["img_match"] = m
                    candidates.append(it)
            print(f"  {it.get('shop','?') if items else '?'}/{b.get('keyword')}: "
                  f"累計候補 {len(candidates)} 件")
            time.sleep(monitor.REQUEST_WAIT)

    # 似ている度が高い順に上位5件
    candidates.sort(key=lambda x: x["img_match"]["best_sim"], reverse=True)
    top = candidates[:5]

    lines = [f"画像判定テスト  候補{len(candidates)}件から上位{len(top)}件", ""]
    for it in top:
        m = it["img_match"]
        profit = f"¥{m['profit']:,}" if m["profit"] is not None else "不明"
        lines.append(
            f"- [{it.get('shop')}] {it['title'][:40]} 仕入{it['price']}\n"
            f"    {m['rank']} 類似度{m['best_sim']:.3f} 相場¥{m['estimate']:,} "
            f"利益{profit}  根拠:{m['ref_url']}"
        )
    report = "\n".join(lines)
    os.makedirs("recon", exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)

    if not top:
        monitor.send_text(
            "🧪 画像判定テスト：似ている売却実例が見つかる商品がありませんでした。"
        )
        return

    monitor.send_text(
        "🧪 **画像判定のテスト通知です**（本番と違い、利益に関係なく"
        f"『似ている度が高い順』に{len(top)}件送ります）\n"
        "各カードの『根拠』リンクを開いて、写真と本当に似ているか見てみてください。"
    )
    monitor.send_items(top)


if __name__ == "__main__":
    main()
