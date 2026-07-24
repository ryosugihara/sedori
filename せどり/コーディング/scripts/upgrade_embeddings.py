# -*- coding: utf-8 -*-
"""
相場DBの指紋を新方式(v2)で作り直す移行スクリプト
  新方式 = 背景切り抜き + SigLIP(新世代) + DINOv2-large(大型)  ＝ Google Lensに近づける強化版

一度に全部(5.4万件)は無料CPUでは終わらないため、1回の実行で時間の許す分だけ
vec3(SigLIP)/vec4(DINOv2-large) を埋めていく。何回か自動実行するうちに全件完了する。
旧方式(vec/vec2)はそのまま残すので、この作業中も今の画像判定は普通に動き続ける。
全件そろってから、新旧どちらが正確かを実測し、良ければ本番判定を切り替える。
"""

import os
import json
import time
import sqlite3
import urllib.request

import fingerprint  # 画像の指紋化（新方式の関数を使う）

DB_FILE = "せどり/データ/data/souba_db.sqlite"
TIME_BUDGET_SEC = int(os.environ.get("UPGRADE_MINUTES", "270")) * 60  # 1回の作業時間の上限
LIMIT = int(os.environ.get("UPGRADE_LIMIT", "0"))  # >0 なら今回その件数だけ処理（動作確認用）
COMMIT_EVERY = 50  # 何件ごとに保存するか（途中で止まっても進捗が残るように）


def send_discord(message):
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        print(message)
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


def ensure_columns(con):
    """新方式の指紋を入れる列(vec3/vec4)が無ければ足す"""
    cols = [r[1] for r in con.execute("PRAGMA table_info(items)")]
    if "vec3" not in cols:
        con.execute("ALTER TABLE items ADD COLUMN vec3 BLOB")  # SigLIP
    if "vec4" not in cols:
        con.execute("ALTER TABLE items ADD COLUMN vec4 BLOB")  # DINOv2-large
    con.commit()


def main():
    if not os.path.exists(DB_FILE):
        send_discord("🧠 指紋アップグレード中止：相場DBが見つかりません。")
        return
    con = sqlite3.connect(DB_FILE)
    ensure_columns(con)
    total = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    rows = con.execute(
        "SELECT id, image_url FROM items "
        "WHERE (vec3 IS NULL OR vec4 IS NULL) AND image_url IS NOT NULL AND image_url != ''"
    ).fetchall()
    if LIMIT > 0:
        rows = rows[:LIMIT]
    print(f"新方式の指紋づくり: 全{total}件 / 今回対象{len(rows)}件（時間上限{TIME_BUDGET_SEC // 60}分）")

    # 画像ダウンロードは「計算している間に、次の画像を裏で先取り(prefetch)」する。
    # こうするとネット待ちが計算時間の裏に隠れ、全体が速くなる。
    # ※DBへの書き込みは今まで通りメインだけ・重いAI計算も1件ずつなので競合は起きない。
    from concurrent.futures import ThreadPoolExecutor
    PREFETCH = 8               # 何件先までダウンロードを先取りするか
    pool = ThreadPoolExecutor(max_workers=4)  # 同時ダウンロード数（サイトに優しい範囲）
    futures = {}

    def submit(k):
        if 0 <= k < len(rows):
            futures[k] = pool.submit(fingerprint.download_bytes, rows[k][1])

    for k in range(min(PREFETCH, len(rows))):
        submit(k)

    start = time.time()
    done = 0
    fail = 0
    for i in range(len(rows)):
        if time.time() - start > TIME_BUDGET_SEC:
            print("時間切れ。ここまで保存して次回に続ける")
            break
        iid, url = rows[i]
        try:
            raw = futures.pop(i).result()   # 先取りしておいた画像データ
            submit(i + PREFETCH)            # 次の画像のダウンロードを補充
            if raw is None:
                fail += 1
            else:
                v3, v4 = fingerprint.embed_bytes_v2(raw)
                # 安全弁: 指紋は本来1本(数千バイト)。異常に大きい物は保存しない
                # （SigLIPが系列[729,1152]=1.6MBを返してDBを19GBに膨張させた事故の再発防止）。
                if (v3 is not None and v4 is not None
                        and v3.size <= 4096 and v4.size <= 4096):
                    con.execute(
                        "UPDATE items SET vec3=?, vec4=? WHERE id=?",
                        (v3.astype("float16").tobytes(), v4.astype("float16").tobytes(), iid),
                    )
                    done += 1
                else:
                    fail += 1
        except Exception as e:
            print(f"  失敗 ({iid}): {e}")
            fail += 1
        if (i + 1) % COMMIT_EVERY == 0:
            con.commit()
            print(f"  進捗 {i + 1}/{len(rows)}（成功{done}/失敗{fail}）")
    con.commit()
    pool.shutdown(wait=False)

    remaining = con.execute(
        "SELECT COUNT(*) FROM items WHERE (vec3 IS NULL OR vec4 IS NULL) "
        "AND image_url IS NOT NULL AND image_url != ''"
    ).fetchone()[0]
    with_new = con.execute(
        "SELECT COUNT(*) FROM items WHERE vec3 IS NOT NULL AND vec4 IS NOT NULL"
    ).fetchone()[0]
    con.close()

    msg = (f"🧠 新方式の指紋づくり: 今回 成功{done}/失敗{fail}件。"
           f"完了 {with_new}/{total} 件・残り{remaining}件。"
           + ("\n✅ 全件完了！次は新旧の精度測定に進めます。"
              if remaining == 0 else "\n次回の自動実行で続きをやります。"))
    print(msg)
    send_discord(msg)


if __name__ == "__main__":
    main()
