# -*- coding: utf-8 -*-
"""
画像判定の「精度測定」（キャリブレーション）

目的:
  「同じ商品」と「違う商品」を確実に見分けられる合格ラインを、
  勘ではなく実データで決める。

方法:
  メルカリの1つの商品には写真が複数枚ある（同じ商品を別角度で撮ったもの）。
  - 同じ商品ペア   = 1つの商品の写真1枚目 vs 2枚目 （正解: 同じ）
  - 違う商品ペア   = 別々の商品の写真1枚目どうし   （正解: 違う）
  それぞれ CLIP と DINOv2 の類似度を測り、点数の分布がどう分かれるか見る。

結果は recon/CALIB.txt に保存する。
"""

import io
import os
import time
import random
import urllib.request

import mercari
import fingerprint

KEYWORDS = [
    "サンローラン デニム",
    "ディオールオム Tシャツ",
    "バルマン ジャケット",
    "ナンバーナイン パーカー",
]
PAIRS_PER_KW = 15       # 1キーワードあたり同商品ペアをいくつ測るか
REPORT = "recon/CALIB.txt"


def fetch_raw(url):
    req = urllib.request.Request(url, headers=fingerprint.UA)
    with urllib.request.urlopen(req, timeout=20) as res:
        return res.read()


def sims(raw_a, raw_b):
    """2枚の画像の (CLIP類似度, DINOv2類似度) を返す"""
    ca = fingerprint.embed_image_bytes(raw_a)
    cb = fingerprint.embed_image_bytes(raw_b)
    da = fingerprint.embed_image_bytes_dino(raw_a)
    db = fingerprint.embed_image_bytes_dino(raw_b)
    return float((ca * cb).sum()), float((da * db).sum())


def pct(sorted_list, p):
    """パーセンタイル（下からp%の位置の値）"""
    if not sorted_list:
        return None
    i = min(len(sorted_list) - 1, int(len(sorted_list) * p / 100))
    return sorted_list[i]


def main():
    random.seed(7)  # 毎回同じ選び方になるように（結果を再現できる）
    pos_c, pos_d = [], []  # 同じ商品ペアの点数 (CLIP, DINO)
    neg_c, neg_d = [], []  # 違う商品ペアの点数

    for kw in KEYWORDS:
        items = mercari.fetch_sold(kw)
        time.sleep(1.5)
        random.shuffle(items)
        print(f"「{kw}」 検索結果: {len(items)}件")

        # --- 同じ商品ペア（商品詳細から写真1枚目 vs 2枚目）---
        used = []
        for it in items:
            if len(used) >= PAIRS_PER_KW:
                break
            detail = mercari.fetch_item(it["id"])
            time.sleep(1.0)  # 詳細の取得はゆっくり（メルカリに優しく）
            photos = (detail or {}).get("photos") or []
            if len(photos) < 2:
                continue
            try:
                a = fetch_raw(photos[0])
                b = fetch_raw(photos[1])
                c, d = sims(a, b)
                pos_c.append(c); pos_d.append(d)
                used.append((it, a))  # 1枚目は違う商品ペアにも再利用する
                print(f"  同商品ペア {len(used)}組目 CLIP={c:.3f} DINO={d:.3f}")
            except Exception as e:
                print(f"  スキップ: {e}")

        # --- 違う商品ペア（別々の商品の1枚目どうし）---
        for i in range(len(used) - 1):
            try:
                c, d = sims(used[i][1], used[i + 1][1])
                neg_c.append(c); neg_d.append(d)
            except Exception as e:
                print(f"  スキップ: {e}")

    pos_c.sort(); pos_d.sort(); neg_c.sort(); neg_d.sort()
    if not pos_c or not neg_c:
        lines = [f"測定できず: 同じ商品ペア {len(pos_c)}組 / 違う商品ペア {len(neg_c)}組",
                 "（商品詳細から写真一覧が取れなかった可能性）"]
    else:
        lines = [
            f"精度測定の結果  同じ商品ペア {len(pos_c)}組 / 違う商品ペア {len(neg_c)}組",
            "",
            "【CLIP】",
            f"  同じ商品:  低い方5% {pct(pos_c,5):.3f} / 25% {pct(pos_c,25):.3f} / 中央 {pct(pos_c,50):.3f}",
            f"  違う商品:  中央 {pct(neg_c,50):.3f} / 95% {pct(neg_c,95):.3f} / 最高 {neg_c[-1]:.3f}",
            "",
            "【DINOv2】",
            f"  同じ商品:  低い方5% {pct(pos_d,5):.3f} / 25% {pct(pos_d,25):.3f} / 中央 {pct(pos_d,50):.3f}",
            f"  違う商品:  中央 {pct(neg_d,50):.3f} / 95% {pct(neg_d,95):.3f} / 最高 {neg_d[-1]:.3f}",
            "",
            "→『違う商品の95%/最高』より上に『同じ商品の大半』が来るAIほど使える。",
        ]
    report = "\n".join(lines)
    os.makedirs("recon", exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)


if __name__ == "__main__":
    main()
