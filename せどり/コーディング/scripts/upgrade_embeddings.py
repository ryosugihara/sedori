# -*- coding: utf-8 -*-
"""
相場DBの指紋を新方式(v2)で作り直す移行スクリプト
  新方式 = 背景切り抜き + SigLIP(新世代) + DINOv2-large(大型)  ＝ Google Lensに近づける強化版

一度に全部(5.4万件)は無料CPUでは終わらないため、1回の実行で時間の許す分だけ
vec3(SigLIP)/vec4(DINOv2-large) を埋めていく。何回か自動実行するうちに全件完了する。
旧方式(vec/vec2)はそのまま残すので、この作業中も今の画像判定は普通に動き続ける。
全件そろってから、新旧どちらが正確かを実測し、良ければ本番判定を切り替える。

失敗の扱い（2026-09-13追加）:
  以前は失敗した商品を記録せず、次の実行でも同じ商品に挑戦し続けていた。
  写真が消えている商品（出品者が出品を削除した等）は何度やっても作れないため、
  「残り1件」のまま永久に終わらず、3時間ごとに相場DB全体を取り直す無駄が続いていた。
  そこで、失敗した理由を商品ごとに記録し、MAX_FAIL 回続けて失敗した物は
  『作れない商品』として対象から外す（旧方式の指紋は残るので画像判定は今まで通り動く）。
  理由は Discord と recon/UPGRADE_EMBEDDINGS.txt に残る。
"""

import os
import json
import time
import sqlite3
import urllib.request
import urllib.error

import fingerprint  # 画像の指紋化（新方式の関数を使う）

DB_FILE = "せどり/データ/data/souba_db.sqlite"
TIME_BUDGET_SEC = int(os.environ.get("UPGRADE_MINUTES", "270")) * 60  # 1回の作業時間の上限
LIMIT = int(os.environ.get("UPGRADE_LIMIT", "0"))  # >0 なら今回その件数だけ処理（動作確認用）
COMMIT_EVERY = 50  # 何件ごとに保存するか（途中で止まっても進捗が残るように）
MAX_FAIL = 3       # 何回続けて失敗したら『作れない商品』として諦めるか（増やすと粘る）
REPORT_FILE = "せどり/データ/recon/UPGRADE_EMBEDDINGS.txt"  # 失敗理由の記録
CHANGED_FLAG = "/tmp/souba_db_changed"  # DBを書き換えた時だけ作る目印（無駄な保存を省く）


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


def download_with_reason(url, timeout=20):
    """写真をダウンロードする。成功なら (データ, None)、失敗なら (None, 理由)。
    理由を返すことで『写真が消えている』のか『通信の一時的な失敗』なのかが分かる。
    """
    try:
        req = urllib.request.Request(url, headers=fingerprint.UA)
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read()
        if not raw:
            return None, "写真のデータが空だった"
        return raw, None
    except urllib.error.HTTPError as e:
        if e.code in (403, 404, 410):
            return None, f"写真が消えている（HTTP {e.code}・出品が削除された可能性が高い）"
        return None, f"写真のサーバーがエラーを返した（HTTP {e.code}）"
    except Exception as e:
        return None, f"通信に失敗（{type(e).__name__}）"


def image_problem(raw):
    """写真データが画像として読めるかを調べる。読めなければ理由を返す（読めれば None）"""
    # 画像を読む道具(Pillow)そのものが無い時は『写真のせい』ではないので、判定しない。
    # ここを区別しないと、道具が無い環境で全商品を『読めない』と誤判定して全件諦めてしまう。
    try:
        import io
        from PIL import Image
    except ImportError:
        return None
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
        w, h = img.size
        if w < 16 or h < 16:
            return f"写真が小さすぎる（{w}x{h}）"
        return None
    except Exception as e:
        kind = "HTMLページ" if raw[:15].lstrip().startswith(b"<") else "画像でないデータ"
        return f"画像として読めない（{kind}・{len(raw)}バイト・{type(e).__name__}）"


def ensure_columns(con):
    """新方式の指紋を入れる列(vec3/vec4)と、失敗記録の列が無ければ足す"""
    cols = [r[1] for r in con.execute("PRAGMA table_info(items)")]
    if "vec3" not in cols:
        con.execute("ALTER TABLE items ADD COLUMN vec3 BLOB")  # SigLIP
    if "vec4" not in cols:
        con.execute("ALTER TABLE items ADD COLUMN vec4 BLOB")  # DINOv2-large
    if "upgrade_fail" not in cols:
        con.execute("ALTER TABLE items ADD COLUMN upgrade_fail INTEGER")  # 失敗した回数
    if "upgrade_fail_reason" not in cols:
        con.execute("ALTER TABLE items ADD COLUMN upgrade_fail_reason TEXT")  # 最後の失敗理由
    con.commit()


def main():
    if not os.path.exists(DB_FILE):
        send_discord("🧠 指紋アップグレード中止：相場DBが見つかりません。")
        return
    con = sqlite3.connect(DB_FILE)
    ensure_columns(con)
    total = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    # 対象: 新方式の指紋がまだ無く、写真URLがあり、まだ諦めていない商品
    rows = con.execute(
        "SELECT id, image_url FROM items "
        "WHERE (vec3 IS NULL OR vec4 IS NULL) AND image_url IS NOT NULL AND image_url != '' "
        "AND COALESCE(upgrade_fail, 0) < ?", (MAX_FAIL,)
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
            futures[k] = pool.submit(download_with_reason, rows[k][1])

    for k in range(min(PREFETCH, len(rows))):
        submit(k)

    start = time.time()
    done = 0
    fail = 0
    changed = False  # DBを書き換えたか（書き換えていなければ保存し直さない）
    for i in range(len(rows)):
        if time.time() - start > TIME_BUDGET_SEC:
            print("時間切れ。ここまで保存して次回に続ける")
            break
        iid, url = rows[i]
        reason = None
        try:
            raw, reason = futures.pop(i).result()   # 先取りしておいた画像データ
            submit(i + PREFETCH)                    # 次の画像のダウンロードを補充
            if raw is not None:
                reason = image_problem(raw)
            if reason is None:
                v3, v4 = fingerprint.embed_bytes_v2(raw)
                # 安全弁: 指紋は本来1本(数千バイト)。異常に大きい物は保存しない
                # （SigLIPが系列[729,1152]=1.6MBを返してDBを19GBに膨張させた事故の再発防止）。
                if v3 is None or v4 is None:
                    reason = "AIの指紋化に失敗（画像は読めたが計算できなかった）"
                elif v3.size > 4096 or v4.size > 4096:
                    reason = f"指紋の大きさが異常（{v3.size}/{v4.size}）"
                else:
                    con.execute(
                        "UPDATE items SET vec3=?, vec4=?, upgrade_fail=NULL, "
                        "upgrade_fail_reason=NULL WHERE id=?",
                        (v3.astype("float16").tobytes(), v4.astype("float16").tobytes(), iid),
                    )
                    done += 1
                    changed = True
        except Exception as e:
            reason = f"予期しないエラー（{type(e).__name__}: {str(e)[:60]}）"
        if reason is not None:
            # 失敗した回数と理由を商品ごとに残す（MAX_FAIL回で諦めて対象から外す）
            print(f"  失敗 ({iid}): {reason}")
            con.execute(
                "UPDATE items SET upgrade_fail = COALESCE(upgrade_fail, 0) + 1, "
                "upgrade_fail_reason = ? WHERE id = ?", (reason, iid))
            fail += 1
            changed = True
        if (i + 1) % COMMIT_EVERY == 0:
            con.commit()
            print(f"  進捗 {i + 1}/{len(rows)}（成功{done}/失敗{fail}）")
    con.commit()
    pool.shutdown(wait=False)

    remaining = con.execute(
        "SELECT COUNT(*) FROM items WHERE (vec3 IS NULL OR vec4 IS NULL) "
        "AND image_url IS NOT NULL AND image_url != '' "
        "AND COALESCE(upgrade_fail, 0) < ?", (MAX_FAIL,)
    ).fetchone()[0]
    with_new = con.execute(
        "SELECT COUNT(*) FROM items WHERE vec3 IS NOT NULL AND vec4 IS NOT NULL"
    ).fetchone()[0]
    # 諦めた商品（何度やっても作れない物）とその理由
    given_up = con.execute(
        "SELECT id, upgrade_fail_reason FROM items WHERE (vec3 IS NULL OR vec4 IS NULL) "
        "AND COALESCE(upgrade_fail, 0) >= ?", (MAX_FAIL,)
    ).fetchall()
    # 失敗が続いている（まだ諦めていない）商品とその理由
    failing = con.execute(
        "SELECT id, upgrade_fail, upgrade_fail_reason FROM items "
        "WHERE (vec3 IS NULL OR vec4 IS NULL) AND COALESCE(upgrade_fail, 0) BETWEEN 1 AND ?",
        (MAX_FAIL - 1,)
    ).fetchall()
    con.close()

    lines = [f"🧠 新方式の指紋づくり: 今回 成功{done}/失敗{fail}件。"
             f"完了 {with_new}/{total} 件・残り{remaining}件。"]
    if failing:
        lines.append(f"⚠️ 失敗が続いている商品 {len(failing)}件（{MAX_FAIL}回失敗したら諦めます）:")
        for iid, n, why in failing[:5]:
            lines.append(f"　・{iid}（{n}回目）: {why}")
    if given_up:
        lines.append(f"🚫 作れないため諦めた商品 {len(given_up)}件"
                     "（旧方式の指紋は残っているので画像判定には影響なし）:")
        for iid, why in given_up[:5]:
            lines.append(f"　・{iid}: {why}")
    if remaining == 0:
        lines.append("✅ 作れる商品は全件完了！次は新旧の精度測定に進めます。")
    else:
        lines.append("次回の自動実行で続きをやります。")
    msg = "\n".join(lines)
    print(msg)
    send_discord(msg)

    # 失敗理由の全件を recon に残す（Discordは長さに限りがあるため）
    try:
        os.makedirs(os.path.dirname(REPORT_FILE), exist_ok=True)
        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            f.write(msg + "\n\n")
            f.write("【失敗が続いている商品（全件）】\n")
            for iid, n, why in failing:
                f.write(f"  https://jp.mercari.com/item/{iid} （{n}回目）: {why}\n")
            f.write("\n【諦めた商品（全件）】\n")
            for iid, why in given_up:
                f.write(f"  https://jp.mercari.com/item/{iid} : {why}\n")
    except Exception as e:
        print(f"記録ファイルの保存に失敗: {e}")

    # DBを書き換えた時だけ目印を作る。何も変わっていなければ、ワークフローは
    # 数百MBある相場DBを保存し直さない（3時間ごとの無駄な保存を省く）
    if changed:
        try:
            open(CHANGED_FLAG, "w").close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
