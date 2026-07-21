# -*- coding: utf-8 -*-
"""
色比較の精度テスト（改良版ヒストグラム vs 旧・平均色）

目的:
  「色違いの別商品」を弾くための色比較が、どのしきい値なら
  ・同じ商品(色は同じ)を誤って弾かない
  ・違う商品(色が違う)をちゃんと弾ける
  かを、実データで確かめて安全なしきい値を決める。

方法（calibrate.py と同じ考え方）:
  1商品には写真が複数枚ある → 写真1枚目 vs 2枚目 = 同じ商品(＝色も同じ)ペア。
  別々の商品の1枚目どうし = 違う商品ペア（色が違うことが多い）。
  それぞれで 旧color_distance と 新color_hist_distance を測り、分布を比べる。
結果は recon/COLOR_CHECK.txt に保存。
"""

import os
import time
import random
import urllib.request

import mercari
import fingerprint
import geom_verify

KEYWORDS = [
    "サンローラン デニム", "ディオールオム Tシャツ", "ナンバーナイン パーカー",
    "グッチ バッグ", "プラダ バッグ", "セリーヌ バッグ",
]
PAIRS_PER_KW = 10
REPORT = "せどり/データ/recon/COLOR_CHECK.txt"


def fetch_raw(url):
    req = urllib.request.Request(url, headers=fingerprint.UA)
    with urllib.request.urlopen(req, timeout=20) as res:
        return res.read()


def colors(a, b):
    """(旧・平均色の距離, 新・色内訳の距離0〜1) を返す"""
    old = geom_verify.color_distance(a, b)
    new = geom_verify.color_hist_distance(a, b)
    return old, new


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    i = min(len(xs) - 1, int(len(xs) * p / 100))
    return xs[i]


def main():
    random.seed(7)
    pos, neg = [], []  # (旧距離, 新距離)

    for kw in KEYWORDS:
        try:
            items = mercari.fetch_sold(kw)
        except Exception as e:
            print(f"「{kw}」検索失敗: {e}")
            continue
        time.sleep(1.5)
        random.shuffle(items)
        print(f"「{kw}」検索結果: {len(items)}件")

        firsts = []  # 各商品の1枚目（違う商品ペアに再利用）
        used = 0
        for it in items:
            if used >= PAIRS_PER_KW:
                break
            detail = mercari.fetch_item(it["id"])
            time.sleep(1.0)
            photos = (detail or {}).get("photos") or []
            if len(photos) < 2:
                continue
            try:
                a = fetch_raw(photos[0])
                b = fetch_raw(photos[1])
                o, n = colors(a, b)
                if n is not None:
                    pos.append((o, n))
                    firsts.append(a)
                    used += 1
                    print(f"  同商品ペア{used} 旧={o} 新={n:.3f}")
            except Exception as e:
                print(f"  スキップ: {e}")

        for i in range(len(firsts)):
            for j in range(i + 1, len(firsts)):
                o, n = colors(firsts[i], firsts[j])
                if n is not None:
                    neg.append((o, n))

    os.makedirs("せどり/データ/recon", exist_ok=True)
    lines = [f"色比較テスト  同じ商品ペア {len(pos)}組 / 違う商品ペア {len(neg)}組", ""]
    if pos and neg:
        pn = [n for _, n in pos]
        nn = [n for _, n in neg]
        lines += [
            "【新・色内訳(ヒストグラム) 距離 0=同色〜1=全然違う】",
            f"  同じ商品:  中央 {pct(pn,50):.3f} / 75% {pct(pn,75):.3f} / 90% {pct(pn,90):.3f} / 最大 {max(pn):.3f}",
            f"  違う商品:  10% {pct(nn,10):.3f} / 25% {pct(nn,25):.3f} / 中央 {pct(nn,50):.3f}",
            "",
            "【しきい値ごとの成績（新・色内訳）】",
            "  しきい値   同じ商品を残せる率   違う商品を弾ける率",
        ]
        for t in [0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]:
            keep = sum(1 for n in pn if n <= t) / len(pn)      # 同商品を残す=正しい
            block = sum(1 for n in nn if n > t) / len(nn)      # 違う商品を弾く=正しい
            lines.append(f"   {t:.2f}      {keep*100:5.1f}%             {block*100:5.1f}%")
        # 参考: 旧・平均色でも同じ表
        po = [o for o, _ in pos if o is not None]
        no = [o for o, _ in neg if o is not None]
        if po and no:
            lines += ["", "【参考: 旧・平均色 距離 での成績】",
                      "  しきい値   同じ商品を残せる率   違う商品を弾ける率"]
            for t in [20, 25, 30, 35, 40, 50]:
                keep = sum(1 for o in po if o <= t) / len(po)
                block = sum(1 for o in no if o > t) / len(no)
                lines.append(f"   {t:>4}     {keep*100:5.1f}%             {block*100:5.1f}%")
    report = "\n".join(lines)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(report)
    print("\n" + report)


if __name__ == "__main__":
    main()
