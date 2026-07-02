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
    pos_pairs, neg_pairs = [], []  # (CLIP点, DINO点) のペアたち

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
                pos_pairs.append((c, d))
                used.append((it, a))  # 1枚目は違う商品ペアにも再利用する
                print(f"  同商品ペア {len(used)}組目 CLIP={c:.3f} DINO={d:.3f}")
            except Exception as e:
                print(f"  スキップ: {e}")

        # --- 違う商品ペア（別々の商品の1枚目どうし・全組み合わせ）---
        for i in range(len(used)):
            for j in range(i + 1, len(used)):
                try:
                    c, d = sims(used[i][1], used[j][1])
                    neg_pairs.append((c, d))
                except Exception as e:
                    print(f"  スキップ: {e}")

    import json as _json
    os.makedirs("recon", exist_ok=True)
    with open("recon/CALIB_RAW.json", "w", encoding="utf-8") as f:
        _json.dump({"pos": pos_pairs, "neg": neg_pairs}, f)

    if not pos_pairs or not neg_pairs:
        lines = [f"測定できず: 同じ商品ペア {len(pos_pairs)}組 / 違う商品ペア {len(neg_pairs)}組"]
    else:
        pos_c = sorted(c for c, _ in pos_pairs); pos_d = sorted(d for _, d in pos_pairs)
        neg_c = sorted(c for c, _ in neg_pairs); neg_d = sorted(d for _, d in neg_pairs)
        lines = [
            f"精度測定の結果  同じ商品ペア {len(pos_pairs)}組 / 違う商品ペア {len(neg_pairs)}組",
            "",
            "【CLIP】",
            f"  同じ商品:  低い方5% {pct(pos_c,5):.3f} / 25% {pct(pos_c,25):.3f} / 中央 {pct(pos_c,50):.3f}",
            f"  違う商品:  中央 {pct(neg_c,50):.3f} / 95% {pct(neg_c,95):.3f} / 最高 {neg_c[-1]:.3f}",
            "",
            "【DINOv2】",
            f"  同じ商品:  低い方5% {pct(pos_d,5):.3f} / 25% {pct(pos_d,25):.3f} / 中央 {pct(pos_d,50):.3f}",
            f"  違う商品:  中央 {pct(neg_d,50):.3f} / 95% {pct(neg_d,95):.3f} / 最高 {neg_d[-1]:.3f}",
            "",
            "【合わせ技（CLIP≧c かつ DINO≧d を合格とした場合）】",
            "  合格ライン        同じ商品を拾える率   違う商品を誤って拾う率",
        ]
        for ct, dt in [(0.85, 0.75), (0.88, 0.80), (0.90, 0.85), (0.92, 0.88),
                       (0.93, 0.90), (0.95, 0.92)]:
            tp = sum(1 for c, d in pos_pairs if c >= ct and d >= dt) / len(pos_pairs)
            fp = sum(1 for c, d in neg_pairs if c >= ct and d >= dt) / len(neg_pairs)
            lines.append(f"  CLIP{ct:.2f}/DINO{dt:.2f}   {tp*100:5.1f}%            {fp*100:5.2f}%")
    report = "\n".join(lines)
    os.makedirs("recon", exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)


if __name__ == "__main__":
    main()
