# -*- coding: utf-8 -*-
"""
相場DBのメンテナンス（掃除だけ。メルカリには一切アクセスしない）

このプログラムがすること:
  1. GitHub Release から相場DB（souba_db.sqlite）を取ってくる
  2. 手元のDBの商品名・状態ランクだけを見て、相場に使わない実例を消す
       - まとめ売り・ジャンク・箱のみ・新品 など（souba.json『相場DB_除外キーワード』）
       - 状態ランク「新品、未使用」・「〇〇様専用」の予約出品・メルカリショップの混入
       - 相場参照期間（souba.json『相場参照期間_日』）より古い取引
  3. 掃除の前後の件数をまとめて recon/SOUBA_DB_MAINTENANCE.txt に書き、Discordにも送る
  4. 掃除後のDBを Release に保存し直す（ワークフロー側で実行）

なぜ別に用意するか:
  設定（除外キーワード・参照期間）を変えた時、次の収集日（毎月1日・15日）を待たずに
  DBへ反映させるため。収集と違ってメルカリの検索APIも画像サーバーも呼ばないので、
  何回動かしてもメルカリ側に負荷はかからない。

使い方（ワークフロー souba-db-maintenance.yml から）:
    python db_release.py download
    python souba_db_maintenance.py
    python db_release.py upload
"""

import os
import json
import time
import sqlite3
import datetime
import urllib.request

import souba_clean

DB_FILE = "せどり/データ/data/souba_db.sqlite"
REPORT_FILE = "せどり/データ/recon/SOUBA_DB_MAINTENANCE.txt"


def souba_days():
    """相場を何日分まで参照するか（souba.json の設定。既定=半年183日）"""
    try:
        with open("せどり/データ/watchlists/souba.json", "r", encoding="utf-8") as f:
            return int(json.load(f).get("設定", {}).get("相場参照期間_日", 183))
    except Exception:
        return 183


def send_discord(message):
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        return
    data = json.dumps({"content": message[:1900]}).encode()
    req = urllib.request.Request(
        webhook, data=data,
        headers={"Content-Type": "application/json",
                 "User-Agent": "sedori-bot/1.0 (+https://github.com/ryosugihara/sedori)"})
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception as e:
        print(f"Discord送信失敗: {e}")


def brand_counts(con):
    """ブランドごとの件数（多い順）"""
    return con.execute(
        "SELECT brand, COUNT(*) FROM items GROUP BY brand ORDER BY COUNT(*) DESC"
    ).fetchall()


def main():
    if not os.path.exists(DB_FILE) or os.path.getsize(DB_FILE) == 0:
        msg = "🧹 相場DBメンテナンス: DBが無いため何もしませんでした（収集が先に必要）"
        print(msg)
        send_discord(msg)
        return

    con = sqlite3.connect(DB_FILE)
    before_total = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    before_brands = dict(brand_counts(con))

    # 1) メルカリショップの混入（商品IDが m+数字 でない物）
    shops = con.execute("DELETE FROM items WHERE id NOT GLOB 'm[0-9]*'").rowcount
    # 2) 相場に使わない実例（まとめ売り・ジャンク・新品・予約出品 等）
    cleaned = souba_clean.cleanse_db(con)
    # 3) 参照期間より古い取引
    days = souba_days()
    cutoff = int(time.time()) - days * 86400
    expired = con.execute(
        "DELETE FROM items WHERE updated IS NOT NULL AND updated < ?", (cutoff,)
    ).rowcount
    con.commit()
    # 消した分の空きを詰めてファイルを小さくする（Releaseへの保存も軽くなる）
    con.execute("VACUUM")

    after_total = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    with_vec = con.execute(
        "SELECT COUNT(*) FROM items WHERE vec IS NOT NULL AND vec2 IS NOT NULL"
    ).fetchone()[0]
    after_brands = brand_counts(con)
    con.close()

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    removed_total = before_total - after_total
    lines = [
        f"🧹 相場DBメンテナンス（{now}）※メルカリへのアクセスなし",
        f"　掃除前: {before_total:,} 件 → 掃除後: {after_total:,} 件（{removed_total:,} 件を除外）",
        f"　　・メルカリショップの混入: {shops} 件",
        f"　　・相場に使わない実例: {souba_clean.format_counts(cleaned)}",
        f"　　・{days}日より古い取引: {expired} 件",
        f"　画像指紋(CLIP+DINO)が揃っている実例: {with_vec:,} 件",
        "",
        "　ブランド別（掃除後 / 掃除前）:",
    ]
    for brand, n in after_brands:
        lines.append(f"　　{brand}: {n:,} / {before_brands.get(brand, 0):,}")
    # 掃除で消えてしまったブランド（0件になった）は目立たせる
    gone = [b for b in before_brands if b not in dict(after_brands)]
    for b in gone:
        lines.append(f"　　{b}: 0 / {before_brands[b]:,} ⚠️ 実例が無くなりました")
    lines.append("")
    lines.append("　根拠が少ないブランドは watch_mercari.json の検索キーワードを増やすと集まりやすくなります")

    report = "\n".join(lines)
    print(report)
    os.makedirs(os.path.dirname(REPORT_FILE), exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report)
    # Discord は短めに（ブランド一覧は上位10件だけ）
    short = "\n".join(lines[:8] + lines[8:18] + (["　　…（続きは recon/SOUBA_DB_MAINTENANCE.txt）"]
                                                 if len(lines) > 18 else []))
    send_discord(short)


if __name__ == "__main__":
    main()
