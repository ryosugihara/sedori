# -*- coding: utf-8 -*-
"""
最終確認AI（verify_ai.py）の「点数」の精度測定（校正用テスト）

目的:
  AIが返す一致度（0〜100点）が、「同じ商品」と「違う商品」をどれだけ分けられるかを
  実データで測り、souba.json の合格ライン（AI確認_同じと判定する点数 等）を
  勘ではなく数字で決められるようにする。

方法（calibrate.py と同じペアの作り方）:
  メルカリの1つの商品には写真が複数枚ある（同じ商品を別角度で撮ったもの）。
  検索結果には写真が1枚しか入らないため、商品詳細から写真一覧を取る。
  - 同じ商品ペア = 1つの商品の写真1枚目 vs 2枚目（正解: 同じ）
  - 違う商品ペア = 別々の商品の写真1枚目どうし（正解: 違う）
    ※同じブランド・同じ種類の商品どうしなので、一番間違えやすい組み合わせ
  それぞれAIに点数を付けさせ、分布と「この点数で線を引いた時の正答率」を出す。

メルカリへの負荷:
  検索APIは KEYWORDS の数（既定3回）。商品詳細は1件1秒間隔で、
  1キーワードあたり最大 PAIRS_PER_KW×3 回まで（写真1枚の商品はスキップするため余裕を持つ）。
  写真は配信サーバー(CDN)から取る。AIの呼び出しは5秒間隔・合計 MAX_AI_CALLS 回まで。

結果は recon/AI_VERIFY_CALIB.txt に保存し、Discordに要約を送る。
※429(回数制限)で失敗した分は理由がレポートに残る。8回連続失敗で自動中断する。
※測定は「同じ」「違う」を交互に行い、生データは AI_VERIFY_CALIB_RAW.json に累積する。
  途中で中断しても実行のたびにデータが貯まり、レポートは累積全体から作られる。
"""

import os
import json
import time
import random
import datetime
import urllib.request

import mercari
import verify_ai

# (キーワード, カテゴリ)。服とバッグの両方を含める（バッグは柄物が多く難所）
KEYWORDS = [
    ("サンローラン デニム", "服"),
    ("ナンバーナイン パーカー", "服"),
    ("グッチ バッグ", "バッグ"),
]
PAIRS_PER_KW = int(os.environ.get("TEST_PAIRS", "8"))   # 1キーワードあたりの同商品ペア数
MAX_AI_CALLS = int(os.environ.get("MAX_AI_CALLS", "60"))  # AI呼び出しの上限（無料枠を守る）
REQUEST_WAIT = 1.5
REPORT = "せどり/データ/recon/AI_VERIFY_CALIB.txt"
RAW = "せどり/データ/recon/AI_VERIFY_CALIB_RAW.json"  # 測定の生データ（実行をまたいで累積）


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


def pct(sorted_list, p):
    """パーセンタイル（下からp%の位置の値）"""
    if not sorted_list:
        return None
    i = min(len(sorted_list) - 1, int(len(sorted_list) * p / 100))
    return sorted_list[i]


def main():
    if not verify_ai.available():
        msg = "🔑 AI点数の精度測定 中止：AIのカギ(GEMINI_API_KEY / ANTHROPIC_API_KEY)が未設定です"
        print(msg)
        send_discord(msg)
        return

    # 最初にAIの生存確認（画像なしの軽い1回）。AIが使えない時にメルカリの
    # 検索・詳細取得だけを無駄に行わないための門番。
    if not verify_ai.ping():
        msg = (f"⏸️ AI点数の精度測定 中止: AIが現在使えません（{verify_ai.LAST_ERROR}）。"
               "メルカリには一切アクセスしていません。時間をおいて再実行してください")
        print(msg)
        send_discord(msg)
        return

    random.seed(7)  # 毎回同じ選び方になるように（結果を再現できる）
    pos, neg = [], []   # (点数, 判定, カテゴリ, 商品名A, URL A, 商品名B, URL B, 結論)
    calls = 0
    fails = {}          # 失敗の理由 → 回数（原因調査のためレポートに残す）
    fail_streak = [0]   # 連続失敗の数。続くようなら無駄撃ちせず中断する
    aborted = [False]

    def record(r):
        """AIの返事を記録する。点数が取れたらTrue。失敗理由は fails に数える"""
        if r and r.get("score") is not None:
            fail_streak[0] = 0
            return True
        if r is None:
            reason = verify_ai.LAST_ERROR or "不明なエラー"
        else:
            reason = f"点数なし: {r.get('reason', '')[:60]}"
        fails[reason] = fails.get(reason, 0) + 1
        fail_streak[0] += 1
        if fail_streak[0] >= 8:
            aborted[0] = True  # 8回連続で失敗＝原因が直らない限り続けても無駄
        return False

    for kw, cat in KEYWORDS:
        if aborted[0]:
            print("連続失敗が続いたため、以降のキーワードを中断します")
            break
        try:
            items = mercari.fetch_sold(kw)
        except Exception as e:
            print(f"「{kw}」取得失敗: {e}")
            continue
        time.sleep(REQUEST_WAIT)
        random.shuffle(items)

        # 検索結果の写真は1枚だけなので、商品詳細から写真一覧を取る（calibrate.pyと同じ）。
        # 写真が2枚以上ある商品を PAIRS_PER_KW 個集める。詳細の取得は1秒間隔・
        # 最大 PAIRS_PER_KW×3 回まで（メルカリに優しく）。
        multi = []   # (item, photos)
        detail_calls = 0
        for it in items:
            if len(multi) >= PAIRS_PER_KW or detail_calls >= PAIRS_PER_KW * 3:
                break
            detail = mercari.fetch_item(it["id"])
            detail_calls += 1
            time.sleep(1.0)
            photos = (detail or {}).get("photos") or []
            if len(photos) >= 2:
                multi.append((it, photos))
        print(f"「{kw}」({cat}) 検索結果 {len(items)}件 / 詳細取得 {detail_calls}回 / "
              f"写真2枚以上 {len(multi)}件を使用")

        # 「同じ商品ペア」と「違う商品ペア」を交互に測る。
        # 以前は同じペアを全部測ってから違うペアだったため、途中で中断すると
        # 「違う商品が0組」になり合格ラインを決められなかった。
        combos = [(i, j) for i in range(len(multi)) for j in range(i + 1, len(multi))]
        random.shuffle(combos)
        tasks = []
        for k in range(len(multi)):
            it, photos = multi[k]
            url = f"https://jp.mercari.com/item/{it['id']}"
            tasks.append(("same", photos[0], photos[1], it["name"], url, it["name"], url))
            if k < len(combos):
                (a, pa), (b, pb) = multi[combos[k][0]], multi[combos[k][1]]
                tasks.append(("diff", pa[0], pb[0],
                              a["name"], f"https://jp.mercari.com/item/{a['id']}",
                              b["name"], f"https://jp.mercari.com/item/{b['id']}"))

        for kind, img_a, img_b, na, ua, nb, ub in tasks:
            if calls >= MAX_AI_CALLS or aborted[0]:
                break
            r = verify_ai.same_product_detail(img_a, img_b, na, nb)
            calls += 1
            label = "同商品" if kind == "same" else "別商品"
            if record(r):
                row = (r["score"], r["verdict"], cat, na, ua, nb, ub, r.get("reason", ""))
                (pos if kind == "same" else neg).append(row)
                print(f"  {label} {r['score']:3d}点 {r['verdict']:9s} {na[:30]}")
            else:
                print(f"  {label} 失敗/点数なし {na[:30]}")

    # 今回の測定分を生データ(RAW)に追記し、レポートは累積全体から作る
    # （1回の実行が途中で失敗しても、繰り返すうちにデータが貯まる）
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    history = []
    try:
        with open(RAW, "r", encoding="utf-8") as f:
            history = json.load(f)
    except Exception:
        history = []
    for kind, rows in (("same", pos), ("diff", neg)):
        for score, verdict, cat, na, ua, nb, ub, reason in rows:
            history.append({"ts": ts, "kind": kind, "score": score, "verdict": verdict,
                            "cat": cat, "name_a": na, "url_a": ua,
                            "name_b": nb, "url_b": ub, "reason": reason})
    history = history[-2000:]  # 太りすぎ防止
    new_same, new_diff = len(pos), len(neg)
    if history:
        os.makedirs(os.path.dirname(RAW), exist_ok=True)
        with open(RAW, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=1)
    # 累積全体を pos/neg に読み直す
    pos = [(h["score"], h["verdict"], h["cat"], h["name_a"], h["url_a"],
            h["name_b"], h["url_b"], h.get("reason", ""))
           for h in history if h["kind"] == "same"]
    neg = [(h["score"], h["verdict"], h["cat"], h["name_a"], h["url_a"],
            h["name_b"], h["url_b"], h.get("reason", ""))
           for h in history if h["kind"] == "diff"]

    if not pos and not neg:
        msg = "⚠️ AI点数の精度測定: ペアを1組も測れませんでした（AI接続かメルカリ取得の失敗）"
        print(msg)
        send_discord(msg)
        return

    ps = sorted(s for s, *_ in pos)
    ns = sorted(s for s, *_ in neg)
    same_th, diff_th = verify_ai._thresholds()

    lines = [f"最終確認AIの点数 精度測定（累計）  同じ商品ペア {len(pos)}組 / 違う商品ペア {len(neg)}組",
             f"  今回の実行: 同じ {new_same}組 / 違う {new_diff}組"
             f"（AI呼び出し {calls}回・うち失敗 {sum(fails.values())}回）", ""]
    if fails:
        lines.append("【測定できなかった呼び出しの内訳】")
        for reason, n in sorted(fails.items(), key=lambda x: -x[1]):
            lines.append(f"  {n}回: {reason}")
        if aborted[0]:
            lines.append("  ※失敗が8回連続したため途中で中断した")
        lines.append("")
    lines.append("【点数の分布】")
    lines.append(f"  同じ商品:  最低 {pct(ps, 0)} / 25% {pct(ps, 25)} / 中央 {pct(ps, 50)} / "
                 f"75% {pct(ps, 75)} / 最高 {pct(ps, 100)}")
    lines.append(f"  違う商品:  最低 {pct(ns, 0)} / 25% {pct(ns, 25)} / 中央 {pct(ns, 50)} / "
                 f"75% {pct(ns, 75)} / 最高 {pct(ns, 100)}")
    lines.append("")
    lines.append("【『同じ』と判定する点数を変えた時】")
    lines.append("  合格ライン   同じ商品を拾える率   違う商品を誤って拾う率")
    best = None
    for th in (50, 60, 70, 80, 90, 95):
        tp = sum(1 for s in ps if s >= th) / len(ps) * 100 if ps else 0
        fp = sum(1 for s in ns if s >= th) / len(ns) * 100 if ns else 0
        mark = " ← 今の設定" if th == same_th else ""
        lines.append(f"  {th:>3}点以上       {tp:5.1f}%               {fp:5.2f}%{mark}")
        if fp == 0 and best is None:
            best = th
    lines.append("")
    if best is not None:
        lines.append(f"  → 違う商品の誤合格が0件になる一番低いライン: {best}点"
                     f"（今の設定 {same_th}点）")
    else:
        lines.append("  → 95点でも違う商品が混ざる。AIの指示書の見直しが必要")
    lines.append("")
    lines.append("【カテゴリ別の中央値】")
    for cat in sorted({x[2] for x in pos + neg}):
        cp = sorted(s for s, _, c, *_ in pos if c == cat)
        cn = sorted(s for s, _, c, *_ in neg if c == cat)
        lines.append(f"  {cat}: 同じ商品 中央 {pct(cp, 50)}（{len(cp)}組） / "
                     f"違う商品 中央 {pct(cn, 50)}（{len(cn)}組）")
    lines.append("")
    lines.append("【要確認の組（人の目で見直す用）】")
    low_same = sorted(pos, key=lambda x: x[0])[:5]
    high_diff = sorted(neg, key=lambda x: -x[0])[:5]
    lines.append("  同じ商品なのに点数が低かった:")
    for s, v, c, na, ua, nb, ub, reason in low_same:
        lines.append(f"    {s:3d}点 {na[:30]} {ua}  結論: {reason[:60]}")
    lines.append("  違う商品なのに点数が高かった:")
    for s, v, c, na, ua, nb, ub, reason in high_diff:
        lines.append(f"    {s:3d}点 {na[:25]} {ua} / {nb[:25]} {ub}  結論: {reason[:60]}")

    report = "\n".join(lines)
    print(report)
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(report)
    send_discord("🧪 最終確認AIの点数テスト（校正用）\n" + "\n".join(lines[:16]))


if __name__ == "__main__":
    main()
