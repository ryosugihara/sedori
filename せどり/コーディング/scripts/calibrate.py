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
  それぞれ CLIP・DINOv2・幾何検証(ORB特徴点)の3つの物差しを測り、
  点数の分布がどう分かれるか見る。

  キーワードは「服」と「バッグ」の両方を含む。バッグ（GG柄・マカダム柄等の
  繰り返し模様）は服より別商品を誤って同一視しやすい難所なので、服だけで
  測った精度をそのままバッグに使うと甘くなっている可能性がある。
  そのため服/バッグを分けて集計し、違いがあるか確認する。

結果は recon/CALIB.txt に保存する。
"""

import io
import os
import time
import random
import urllib.request

import mercari
import fingerprint
import geom_verify

# (キーワード, カテゴリ) のペア。カテゴリ別に精度を分けて見るために使う。
KEYWORDS = [
    ("サンローラン デニム", "服"),
    ("ディオールオム Tシャツ", "服"),
    ("バルマン ジャケット", "服"),
    ("ナンバーナイン パーカー", "服"),
    ("グッチ アーカイブ バッグ", "バッグ"),
    ("プラダ アーカイブ バッグ", "バッグ"),
    ("ミュウミュウ アーカイブ バッグ", "バッグ"),
    ("セリーヌ バッグ", "バッグ"),
    ("オールドコーチ バッグ", "バッグ"),
]
PAIRS_PER_KW = 15       # 1キーワードあたり同商品ペアをいくつ測るか
REPORT = "せどり/データ/recon/CALIB.txt"


def fetch_raw(url):
    req = urllib.request.Request(url, headers=fingerprint.UA)
    with urllib.request.urlopen(req, timeout=20) as res:
        return res.read()


def sims(raw_a, raw_b):
    """2枚の画像の (CLIP類似度, DINOv2類似度, 幾何一致点数) を返す"""
    ca = fingerprint.embed_image_bytes(raw_a)
    cb = fingerprint.embed_image_bytes(raw_b)
    da = fingerprint.embed_image_bytes_dino(raw_a)
    db = fingerprint.embed_image_bytes_dino(raw_b)
    geo = geom_verify.inlier_count(raw_a, raw_b)
    return float((ca * cb).sum()), float((da * db).sum()), geo


def pct(sorted_list, p):
    """パーセンタイル（下からp%の位置の値）"""
    if not sorted_list:
        return None
    i = min(len(sorted_list) - 1, int(len(sorted_list) * p / 100))
    return sorted_list[i]


def main():
    random.seed(7)  # 毎回同じ選び方になるように（結果を再現できる）
    pos_pairs, neg_pairs = [], []  # (CLIP点, DINO点, 幾何点, カテゴリ) のペアたち

    for kw, cat in KEYWORDS:
        items = mercari.fetch_sold(kw)
        time.sleep(1.5)
        random.shuffle(items)
        print(f"「{kw}」({cat}) 検索結果: {len(items)}件")

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
                c, d, g = sims(a, b)
                pos_pairs.append((c, d, g, cat))
                used.append((it, a))  # 1枚目は違う商品ペアにも再利用する
                print(f"  同商品ペア {len(used)}組目 CLIP={c:.3f} DINO={d:.3f} 幾何={g}")
            except Exception as e:
                print(f"  スキップ: {e}")

        # --- 違う商品ペア（別々の商品の1枚目どうし・全組み合わせ）---
        for i in range(len(used)):
            for j in range(i + 1, len(used)):
                try:
                    c, d, g = sims(used[i][1], used[j][1])
                    neg_pairs.append((c, d, g, cat))
                except Exception as e:
                    print(f"  スキップ: {e}")

    import json as _json
    os.makedirs("せどり/データ/recon", exist_ok=True)
    with open("せどり/データ/recon/CALIB_RAW.json", "w", encoding="utf-8") as f:
        _json.dump({"pos": pos_pairs, "neg": neg_pairs}, f)

    if not pos_pairs or not neg_pairs:
        lines = [f"測定できず: 同じ商品ペア {len(pos_pairs)}組 / 違う商品ペア {len(neg_pairs)}組"]
    else:
        pos_c = sorted(c for c, _, _, _ in pos_pairs); pos_d = sorted(d for _, d, _, _ in pos_pairs)
        neg_c = sorted(c for c, _, _, _ in neg_pairs); neg_d = sorted(d for _, d, _, _ in neg_pairs)
        pos_g = sorted(g for _, _, g, _ in pos_pairs); neg_g = sorted(g for _, _, g, _ in neg_pairs)
        lines = [
            f"精度測定の結果  同じ商品ペア {len(pos_pairs)}組 / 違う商品ペア {len(neg_pairs)}組",
            f"（内訳: 服 {sum(1 for *_, cat in pos_pairs if cat=='服')}組 / "
            f"バッグ {sum(1 for *_, cat in pos_pairs if cat=='バッグ')}組 が同じ商品ペア）",
            "",
            "【CLIP】",
            f"  同じ商品:  低い方5% {pct(pos_c,5):.3f} / 25% {pct(pos_c,25):.3f} / 中央 {pct(pos_c,50):.3f}",
            f"  違う商品:  中央 {pct(neg_c,50):.3f} / 95% {pct(neg_c,95):.3f} / 最高 {neg_c[-1]:.3f}",
            "",
            "【DINOv2】",
            f"  同じ商品:  低い方5% {pct(pos_d,5):.3f} / 25% {pct(pos_d,25):.3f} / 中央 {pct(pos_d,50):.3f}",
            f"  違う商品:  中央 {pct(neg_d,50):.3f} / 95% {pct(neg_d,95):.3f} / 最高 {neg_d[-1]:.3f}",
            "",
            "【幾何検証（ORB特徴点の一致数）】",
            f"  同じ商品:  低い方5% {pct(pos_g,5)} / 25% {pct(pos_g,25)} / 中央 {pct(pos_g,50)}",
            f"  違う商品:  中央 {pct(neg_g,50)} / 95% {pct(neg_g,95)} / 最高 {neg_g[-1]}",
            "",
            "【合わせ技（CLIP≧c かつ DINO≧d を合格とした場合）全体】",
            "  合格ライン        同じ商品を拾える率   違う商品を誤って拾う率",
        ]
        combos = [(0.85, 0.75), (0.88, 0.80), (0.90, 0.85), (0.92, 0.88),
                  (0.93, 0.90), (0.95, 0.92)]
        for ct, dt in combos:
            tp = sum(1 for c, d, _, _ in pos_pairs if c >= ct and d >= dt) / len(pos_pairs)
            fp = sum(1 for c, d, _, _ in neg_pairs if c >= ct and d >= dt) / len(neg_pairs)
            lines.append(f"  CLIP{ct:.2f}/DINO{dt:.2f}   {tp*100:5.1f}%            {fp*100:5.2f}%")

        # カテゴリ別（服 / バッグ）で同じ表を出す。バッグだけ精度が落ちていないか確認するため。
        for cat in ("服", "バッグ"):
            cp = [p for p in pos_pairs if p[3] == cat]
            cn = [p for p in neg_pairs if p[3] == cat]
            if not cp or not cn:
                continue
            lines.append("")
            lines.append(f"【合わせ技　カテゴリ別: {cat}（同じ商品{len(cp)}組/違う商品{len(cn)}組）】")
            lines.append("  合格ライン        同じ商品を拾える率   違う商品を誤って拾う率")
            for ct, dt in combos:
                tp = sum(1 for c, d, _, _ in cp if c >= ct and d >= dt) / len(cp)
                fp = sum(1 for c, d, _, _ in cn if c >= ct and d >= dt) / len(cn)
                lines.append(f"  CLIP{ct:.2f}/DINO{dt:.2f}   {tp*100:5.1f}%            {fp*100:5.2f}%")

        lines.append("")
        lines.append("【幾何検証だけの合格ライン別 精度（一致点数の下限）】")
        lines.append("  一致点数          同じ商品を拾える率   違う商品を誤って拾う率")
        for gt in [5, 8, 10, 12, 15, 18, 20, 25, 30]:
            tp = sum(1 for _, _, g, _ in pos_pairs if g >= gt) / len(pos_pairs)
            fp = sum(1 for _, _, g, _ in neg_pairs if g >= gt) / len(neg_pairs)
            lines.append(f"  {gt:3d}点以上         {tp*100:5.1f}%            {fp*100:5.2f}%")

        lines.append("")
        lines.append("【本番と同じ2段階チェック（CLIP/DINO合格 → 幾何検証）の最終精度】")
        lines.append("  ここが実際に通知される『同デザイン』の最終的な正解率・誤り率")
        lines.append("  CLIP/DINO合格ライン + 幾何ライン    最終的に拾える率   最終的な誤り率")
        for ct, dt in [(0.90, 0.85), (0.92, 0.90), (0.93, 0.90)]:
            for gt in [10, 15, 20]:
                tp = sum(1 for c, d, g, _ in pos_pairs if c >= ct and d >= dt and g >= gt) / len(pos_pairs)
                fp = sum(1 for c, d, g, _ in neg_pairs if c >= ct and d >= dt and g >= gt) / len(neg_pairs)
                lines.append(f"  CLIP{ct:.2f}/DINO{dt:.2f}/幾何{gt}点   {tp*100:5.1f}%            {fp*100:5.2f}%")
    report = "\n".join(lines)
    os.makedirs("せどり/データ/recon", exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)


if __name__ == "__main__":
    main()
