# -*- coding: utf-8 -*-
"""
新旧の目の精度くらべ（キャリブレーションv2）

旧の目: CLIP(ViT-B/32) + DINOv2-base を『元画像』で測る
新の目: SigLIP(so400m) + DINOv2-large を『背景切り抜き後』で測る（v2方式）

同じ考え方(calibrate.py)で 同じ商品ペア/違う商品ペア を作り、両方の目で類似度を測る。
どちらが「同じ商品をよく拾い、違う商品を誤って拾わない」かを、しきい値ごとの
成績表で比べる。結果は recon/CALIB_V2.txt に保存。
"""

import time
import os
import random
import urllib.request

import numpy as np

import mercari
import fingerprint

KEYWORDS = [
    ("サンローラン デニム", "服"),
    ("ディオールオム Tシャツ", "服"),
    ("ナンバーナイン パーカー", "服"),
    ("バルマン ジャケット", "服"),
    ("グッチ アーカイブ バッグ", "バッグ"),
    ("プラダ アーカイブ バッグ", "バッグ"),
    ("セリーヌ バッグ", "バッグ"),
    ("オールドコーチ バッグ", "バッグ"),
]
PAIRS_PER_KW = 10
REPORT = "せどり/データ/recon/CALIB_V2.txt"


def fetch_raw(url):
    req = urllib.request.Request(url, headers=fingerprint.UA)
    with urllib.request.urlopen(req, timeout=20) as res:
        return res.read()


def cos(a, b):
    if a is None or b is None:
        return None
    return float(np.dot(a, b))


def measure(a_raw, b_raw):
    """(旧CLIP, 旧DINO, 新SigLIP, 新DINO-large) の4類似度を返す。作れなければNone。"""
    oc = cos(fingerprint.embed_image_bytes(a_raw), fingerprint.embed_image_bytes(b_raw))
    od = cos(fingerprint.embed_image_bytes_dino(a_raw), fingerprint.embed_image_bytes_dino(b_raw))
    ia = fingerprint.remove_bg_and_crop(a_raw)
    ib = fingerprint.remove_bg_and_crop(b_raw)
    ns = nd = None
    if ia is not None and ib is not None:
        ns = cos(fingerprint.embed_siglip(ia), fingerprint.embed_siglip(ib))
        nd = cos(fingerprint.embed_dino_large(ia), fingerprint.embed_dino_large(ib))
    return oc, od, ns, nd


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p / 100))]


def sweep(pos, neg, ia, ib, grid_a, grid_b, label_a, label_b):
    """2指標(ia番目,ib番目)のしきい値グリッドで TP(同一を残す率)/FP(別物を拾う率)。"""
    lines = [f"  {label_a}/{label_b}   同じ商品を拾う率   違う商品を誤って拾う率"]
    best = None
    for ta in grid_a:
        for tb in grid_b:
            tp = sum(1 for r in pos if r[ia] is not None and r[ib] is not None
                     and r[ia] >= ta and r[ib] >= tb) / max(1, len(pos))
            fp = sum(1 for r in neg if r[ia] is not None and r[ib] is not None
                     and r[ia] >= ta and r[ib] >= tb) / max(1, len(neg))
            lines.append(f"  {ta:.2f}/{tb:.2f}      {tp*100:5.1f}%            {fp*100:5.2f}%")
            # 誤検知1%以下の中で、同一を最も多く拾える組を「おすすめ」に
            if fp <= 0.01 and (best is None or tp > best[0]):
                best = (tp, fp, ta, tb)
    if best:
        lines.append(f"  → おすすめ({label_a}{best[2]:.2f}/{label_b}{best[3]:.2f}): "
                     f"同一{best[0]*100:.1f}%拾える・誤検知{best[1]*100:.2f}%")
    return lines, best


def main():
    random.seed(7)
    pos, neg = [], []

    for kw, cat in KEYWORDS:
        try:
            items = mercari.fetch_sold(kw)
        except Exception as e:
            print(f"「{kw}」検索失敗: {e}")
            continue
        time.sleep(1.5)
        random.shuffle(items)
        print(f"「{kw}」({cat}) {len(items)}件")
        firsts = []
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
                r = measure(a, b)
                pos.append(r + (cat,))
                firsts.append(a)
                used += 1
                print(f"  同{used}: 旧C{r[0]:.3f}/D{r[1]:.3f}  新S{(r[2] or -1):.3f}/D{(r[3] or -1):.3f}")
            except Exception as e:
                print(f"  スキップ: {e}")
        for i in range(len(firsts)):
            for j in range(i + 1, len(firsts)):
                try:
                    neg.append(measure(firsts[i], firsts[j]) + (cat,))
                except Exception as e:
                    print(f"  スキップ(neg): {e}")

    os.makedirs("せどり/データ/recon", exist_ok=True)
    L = [f"新旧の目 精度くらべ  同じ商品{len(pos)}組 / 違う商品{len(neg)}組", ""]
    if pos and neg:
        for idx, nm in [(0, "旧CLIP"), (1, "旧DINO"), (2, "新SigLIP"), (3, "新DINO-large")]:
            p = [r[idx] for r in pos if r[idx] is not None]
            n = [r[idx] for r in neg if r[idx] is not None]
            if p and n:
                L.append(f"【{nm}】同じ:5%={pct(p,5):.3f}/中央={pct(p,50):.3f}  違う:中央={pct(n,50):.3f}/95%={pct(n,95):.3f}/最高={max(n):.3f}")
        L.append("")
        L.append("=== 旧の目（CLIP/DINO・元画像） ===")
        lo, bo = sweep(pos, neg, 0, 1,
                       [0.85, 0.88, 0.90, 0.92, 0.93], [0.75, 0.80, 0.85, 0.90], "C", "D")
        L += lo
        L.append("")
        L.append("=== 新の目（SigLIP/DINO-large・背景切り抜き） ===")
        ln, bn = sweep(pos, neg, 2, 3,
                       [0.70, 0.80, 0.85, 0.90, 0.93, 0.95], [0.55, 0.65, 0.75, 0.85, 0.90], "S", "D")
        L += ln
        L.append("")
        L.append("=== 結論 ===")
        if bo and bn:
            L.append(f"旧の目 おすすめ: 同一{bo[0]*100:.1f}%拾える(誤検知{bo[1]*100:.2f}%)")
            L.append(f"新の目 おすすめ: 同一{bn[0]*100:.1f}%拾える(誤検知{bn[1]*100:.2f}%)")
            L.append("→ 同じ『誤検知ほぼ0』の条件で、同一商品をより多く拾えた方が優秀")
    report = "\n".join(L)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(report)
    print("\n" + report)


if __name__ == "__main__":
    main()
