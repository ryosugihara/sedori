# -*- coding: utf-8 -*-
"""
季節で相場が変わるかの測定

なぜ必要か:
  今の相場の計算は『1年以内に売れた実例』を全部まとめて中央値を出していて、
  いつ売れたかを区別していない。もしダウンやコートが「冬は高く・夏は安く」売れて
  いるなら、夏に売れた安い実例が混ざって相場を押し下げ、本当は利益が出る冬物を
  『利益が足りない』と見送っている可能性がある。
  直す前に、本当に差があるのかを手元のデータで確かめる。

このプログラムがすること:
  1. 相場DBの商品を、商品名から『冬物』『夏物』『通年物』に分ける
  2. 売れた月から『シーズン中』『シーズン外』に分ける
       冬物のシーズン中 = 10〜3月 / 夏物のシーズン中 = 4〜9月
  3. ブランド×種類のまとまりごとに、シーズン中とシーズン外の中央値を比べる
  4. 『通年物（デニム・パンツ）』も同じやり方で測る ← 対照群
       もし通年物にも同じだけ差が出るなら、その差は季節のせいではなく
       『比べている商品が違うだけ』なので、対策しても意味がないと分かる

メルカリには一切アクセスしない。重いAI(torch等)も使わない。
方法2では保存済みの写真の指紋を使うので numpy だけ必要。
結果は recon/SEASON_PRICE.txt に保存し、Discordにも送る。
"""

import os
import json
import sqlite3
import datetime
import statistics
import urllib.request

DB_FILE = "せどり/データ/data/souba_db.sqlite"
REPORT_FILE = "せどり/データ/recon/SEASON_PRICE.txt"
MIN_PER_SIDE = 15  # 片側がこの件数未満のまとまりは、偶然のブレが大きいので測らない

# 商品名から種類を見分ける言葉（上から順に当てはめる）
CATEGORIES = [
    ("冬物", ["ダウン", "コート", "ニット", "セーター", "フリース", "ムートン",
            "ボア", "中綿", "カシミヤ", "ウール"], {10, 11, 12, 1, 2, 3}),
    ("夏物", ["半袖", "tシャツ", "ｔシャツ", "ポロシャツ", "タンクトップ",
            "ショートパンツ", "ハーフパンツ", "短パン", "ノースリーブ"],
     {4, 5, 6, 7, 8, 9}),
    # 対照群: 季節に関係なく売れる物。ここに差が出なければ、上の差は本物
    ("通年物(対照群)", ["デニム", "ジーンズ", "スキニー", "パンツ", "スラックス"],
     {10, 11, 12, 1, 2, 3}),
]


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


def category_of(name):
    """商品名から (種類, シーズン中の月) を返す。分からなければ None"""
    t = (name or "").lower()
    for label, words, months in CATEGORIES:
        if any(w.lower() in t for w in words):
            return label, months
    return None, None


def main():
    if not os.path.exists(DB_FILE):
        send_discord("📅 季節と相場の測定 中止：相場DBが見つかりません")
        return

    con = sqlite3.connect(DB_FILE)
    rows = con.execute(
        "SELECT name, price, brand, updated FROM items "
        "WHERE price > 0 AND updated IS NOT NULL"
    ).fetchall()
    con.close()
    print(f"相場DBから {len(rows)} 件を読み込みました")

    # まとまり（ブランド×種類）ごとに、シーズン中/外の値段を集める
    groups = {}  # (ブランド, 種類) → {"中": [値段], "外": [値段]}
    for name, price, brand, updated in rows:
        label, months = category_of(name)
        if not label:
            continue
        month = datetime.datetime.fromtimestamp(updated).month
        key = (brand or "(不明)", label)
        side = "中" if month in months else "外"
        groups.setdefault(key, {"中": [], "外": []})[side].append(int(price))

    lines = [f"📅 季節で相場が変わるかの測定  （相場DB {len(rows)}件から）", ""]
    lines.append("見方: 『外÷中』が1.0より小さいほど、シーズン外は安く売れている")
    lines.append("　　  通年物(対照群)にも同じ差が出るなら、季節のせいではない")
    lines.append("")

    by_label = {}  # 種類 → [(ブランド, 中央値中, 中央値外, 比, 件数中, 件数外)]
    for (brand, label), v in sorted(groups.items()):
        if len(v["中"]) < MIN_PER_SIDE or len(v["外"]) < MIN_PER_SIDE:
            continue
        m_in = statistics.median(v["中"])
        m_out = statistics.median(v["外"])
        ratio = m_out / m_in if m_in else 0
        by_label.setdefault(label, []).append(
            (brand, m_in, m_out, ratio, len(v["中"]), len(v["外"])))

    for label, _w, _m in CATEGORIES:
        rec = by_label.get(label, [])
        if not rec:
            lines.append(f"【{label}】測れるまとまりがありませんでした"
                         f"（片側{MIN_PER_SIDE}件以上が必要）")
            lines.append("")
            continue
        lines.append(f"【{label}】")
        lines.append("  ブランド            シーズン中    シーズン外    外÷中   件数(中/外)")
        for brand, m_in, m_out, ratio, n_in, n_out in sorted(rec, key=lambda x: x[3]):
            lines.append(f"  {brand[:18]:18s} ¥{m_in:>8,.0f}  ¥{m_out:>8,.0f}   "
                         f"{ratio:.2f}   {n_in}/{n_out}")
        ratios = [r[3] for r in rec]
        med = statistics.median(ratios)
        lines.append(f"  → まとめ: 外÷中の中央値 {med:.2f}"
                     f"（シーズン外に売れた物は {(med - 1) * 100:+.0f}% の値段）")
        lines.append("")

    # 判定
    lines.append("【方法1の結論の目安】")
    lines.append("  ※対照群(通年物)が1.0から大きく外れている時は、比べている商品が")
    lines.append("　　違うだけなので、方法1の数字は信用できない")
    w = by_label.get("冬物", [])
    s = by_label.get("夏物", [])
    c = by_label.get("通年物(対照群)", [])
    def med_ratio(rec):
        return statistics.median([r[3] for r in rec]) if rec else None
    mw, ms, mc = med_ratio(w), med_ratio(s), med_ratio(c)
    if mc is None:
        lines.append("  対照群が測れなかったため、季節の影響かどうか判断できません")
    else:
        for label, m in (("冬物", mw), ("夏物", ms)):
            if m is None:
                lines.append(f"  {label}: 測れませんでした")
                continue
            diff = (mc - m) * 100
            if diff >= 10:
                lines.append(f"  {label}: 対照群より {diff:.0f}ポイント大きく下がる"
                             " → 季節の影響あり。相場の計算に季節を入れる価値がある")
            elif diff <= -10:
                lines.append(f"  {label}: 対照群より {-diff:.0f}ポイント小さい"
                             " → 予想と逆。対策不要")
            else:
                lines.append(f"  {label}: 対照群との差は {diff:+.0f}ポイント"
                             " → 季節の影響は小さい。対策しても効果は薄い")

    # ========== 方法2: 同じ商品どうしで比べる（こちらが本命）==========
    # 方法1は『比べている商品が違う』ことに弱い。実際、季節に関係ないはずの
    # 通年物にも大きな差が出てしまった（相場DBは最近売れた物ほど多く集まるため、
    # 『シーズン中＝古いデータ / シーズン外＝新しいデータ』になっていた）。
    # そこで写真の指紋で『同じ商品』の組を見つけ、その組の中だけで
    # 『シーズン中に売れた値段』と『シーズン外に売れた値段』を比べる。
    lines.append("")
    lines.append("=" * 58)
    lines.append("【方法2: 同じ商品どうしで比べる】")
    lines.append("  写真の指紋がそっくりな組だけを使うので、商品の違いに影響されない")
    try:
        import numpy as np
        con = sqlite3.connect(DB_FILE)
        rows2 = con.execute(
            "SELECT name, price, brand, updated, vec, vec2 FROM items "
            "WHERE price > 0 AND updated IS NOT NULL AND vec IS NOT NULL AND vec2 IS NOT NULL"
        ).fetchall()
        con.close()

        def unit(b):
            v = np.frombuffer(b, dtype=np.float16).astype("float32")
            n = np.linalg.norm(v)
            return None if n == 0 else v / n

        # ブランド×種類ごとに、同じ商品の組を探す
        buckets = {}
        for name, price, brand, updated, vec, vec2 in rows2:
            label, months = category_of(name)
            if not label:
                continue
            a, b = unit(vec), unit(vec2)
            if a is None or b is None:
                continue
            month = datetime.datetime.fromtimestamp(updated).month
            buckets.setdefault((brand or "(不明)", label), []).append(
                (int(price), month in months, a, b, name))

        for label, _w, _m in CATEGORIES:
            ratios, pairs = [], 0
            for (brand, lb), items in buckets.items():
                if lb != label or len(items) < 2:
                    continue
                mat_c = np.stack([x[2] for x in items])
                mat_d = np.stack([x[3] for x in items])
                sims_c = mat_c @ mat_c.T
                sims_d = mat_d @ mat_d.T
                n = len(items)
                for i in range(n):
                    if not items[i][1]:
                        continue  # iはシーズン中の物だけ
                    for j in range(n):
                        if items[j][1] or i == j:
                            continue  # jはシーズン外の物だけ
                        # 『同デザイン』の合格ラインより厳しめ＝ほぼ同じ商品
                        if sims_c[i, j] >= 0.90 and sims_d[i, j] >= 0.88:
                            ratios.append(items[j][0] / items[i][0])
                            pairs += 1
            if len(ratios) >= 10:
                med = statistics.median(ratios)
                lines.append(f"  {label}: 同じ商品の組 {pairs}組 → "
                             f"シーズン外は {(med - 1) * 100:+.0f}% の値段（比 {med:.2f}）")
            else:
                lines.append(f"  {label}: 同じ商品の組が{len(ratios)}組しか無く、測れません")
    except Exception as e:
        lines.append(f"  測定に失敗しました: {type(e).__name__}: {str(e)[:80]}")

    report = "\n".join(lines)
    print(report)
    os.makedirs(os.path.dirname(REPORT_FILE), exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report)
    send_discord("\n".join(lines[:20]))


if __name__ == "__main__":
    main()
