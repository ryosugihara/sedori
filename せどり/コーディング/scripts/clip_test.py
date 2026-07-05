# -*- coding: utf-8 -*-
"""
CLIP 動作実験（無料の画像AI が GitHub の無料マシンで動くか確認する）

CLIPとは:
  画像や文章を「数値の指紋（ベクトル）」に変える無料公開のAI。
  指紋どうしが近い ＝ 見た目や意味が似ている、と判定できる。

この実験で確かめること:
  1. GitHubの無料マシンに CLIP を入れて動かせるか（時間はどれくらいか）
  2. 実際の商品画像（KINDALのサンローラン）で、
     「デニム同士 は近い」「デニム と バッグ は遠い」が正しく出るか
  3. 1枚あたりの処理時間（監視で使えるスピードか）

結果は recon/CLIP_RESULT.txt に保存し、Discordにも送る。
"""

import io
import os
import json
import time
import datetime
import urllib.request

RESULT_FILE = "せどり/データ/recon/CLIP_RESULT.txt"

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
}


def fetch_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def fetch_image(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read()


def pick_test_images():
    """KINDALのサンローラン売り場から、デニム系2枚＋バッグ等1枚を選ぶ"""
    url = "https://shop.kind.co.jp/collections/saint-laurent-paris/products.json?limit=250"
    products = fetch_json(url).get("products", [])
    denims, others = [], []
    for p in products:
        images = p.get("images") or []
        if not images:
            continue
        text = (p.get("title", "") + " " + p.get("product_type", "")).lower()
        entry = (p.get("title", ""), images[0].get("src"))
        if any(k in text for k in ["デニム", "denim", "ジーンズ", "スキニー", "パンツ"]):
            denims.append(entry)
        elif any(k in text for k in ["バッグ", "bag", "財布", "ポーチ"]):
            others.append(entry)
    if len(denims) >= 2 and others:
        return denims[0], denims[1], others[0]
    return None, None, None


def main():
    os.makedirs("せどり/データ/recon", exist_ok=True)
    lines = ["CLIP 動作実験  " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ""]

    # --- 1. CLIPの読み込み（時間を計る）---
    t0 = time.time()
    from sentence_transformers import SentenceTransformer  # ここで初めて読み込む
    from PIL import Image
    model = SentenceTransformer("clip-ViT-B-32")
    load_sec = time.time() - t0
    lines.append(f"1) モデル読み込み: {load_sec:.1f} 秒")

    # --- 2. テスト画像を用意（KINDALの実物商品）---
    d1, d2, other = pick_test_images()
    if not d1:
        lines.append("テスト画像を選べませんでした（KINDAL取得失敗）")
    else:
        imgs, names = [], []
        for title, src in (d1, d2, other):
            raw = fetch_image(src)
            imgs.append(Image.open(io.BytesIO(raw)).convert("RGB"))
            names.append(title[:38])

        # --- 3. 指紋化して近さを計算 ---
        t1 = time.time()
        vecs = model.encode(imgs, normalize_embeddings=True)
        embed_sec = (time.time() - t1) / len(imgs)

        def sim(a, b):
            return float((vecs[a] * vecs[b]).sum())  # コサイン類似度(1に近い=似てる)

        s_dd = sim(0, 1)   # デニム vs デニム
        s_d1o = sim(0, 2)  # デニム vs バッグ等
        s_d2o = sim(1, 2)
        ok = s_dd > max(s_d1o, s_d2o)

        lines.append(f"2) 指紋化スピード: 1枚あたり {embed_sec:.2f} 秒")
        lines.append("")
        lines.append(f"   [A] {names[0]}")
        lines.append(f"   [B] {names[1]}")
        lines.append(f"   [C] {names[2]}")
        lines.append(f"3) 似てる度  A-B(デニム同士) = {s_dd:.3f}")
        lines.append(f"            A-C(デニムvs別物) = {s_d1o:.3f}")
        lines.append(f"            B-C(デニムvs別物) = {s_d2o:.3f}")
        lines.append("")
        lines.append("判定: " + ("🟢 成功（同系统どうしが一番近い）" if ok
                                 else "🔴 失敗（近さの順番が想定と違う）"))

        # --- 4. 文字と画像の橋渡しも確認（CLIPの強み）---
        tv = model.encode(["black skinny denim jeans", "leather bag"],
                          normalize_embeddings=True)
        t_dd = float((vecs[0] * tv[0]).sum())
        t_do = float((vecs[0] * tv[1]).sum())
        lines.append(f"4) 文字→画像: デニム画像 vs『denim』{t_dd:.3f} / vs『bag』{t_do:.3f}"
                     + ("  🟢" if t_dd > t_do else "  🔴"))

    report = "\n".join(lines)
    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)

    # Discordにも送る
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if webhook:
        data = json.dumps({"content": "【CLIP 動作実験】\n" + report[:1800]}).encode()
        req = urllib.request.Request(
            webhook, data=data,
            headers={"Content-Type": "application/json",
                     "User-Agent": "sedori-bot/1.0 (+https://github.com/ryosugihara/sedori)"})
        try:
            urllib.request.urlopen(req, timeout=30)
        except Exception as e:
            print(f"Discord送信失敗: {e}")


if __name__ == "__main__":
    main()
