# -*- coding: utf-8 -*-
"""
写真判定（服かバッグか）の精度測定

なぜ必要か:
  安く出ている狙い目の商品は商品名が雑なことが多く、名前だけで『服以外』を弾くと
  利益の出る商品まで見送ってしまう。そこで写真そのものから種類を判定したいが、
  外し方によっては服を捨ててしまうので、有効にする前に実データで精度を測る。

このプログラムがすること:
  1. 相場DB(souba_db.sqlite)から、保存済みの写真の指紋(CLIP)と検索キーワードを読む
  2. 検索キーワードから正解ラベルを付ける（例:「グッチ バッグ」→服以外 /
     「サンローラン デニム」→服）。どちらとも言えないキーワードは測定から外す
  3. 写真判定（priority.image_scores）を全件に走らせ、しきい値（服以外と判断する差）を
     変えながら「服を誤って捨てる率」と「服以外を正しく弾く率」を測る
  4. 結果を recon/IMAGE_CATEGORY_CALIB.txt に保存し、Discordに要約を送る

メルカリには一切アクセスしない（写真も再ダウンロードしない）。指紋は保存済みの物を使う。
"""

import os
import json
import sqlite3
import urllib.request

import priority

DB_FILE = "せどり/データ/data/souba_db.sqlite"
REPORT = "せどり/データ/recon/IMAGE_CATEGORY_CALIB.txt"

# 正解ラベルを付けるための言葉（※1回目の測定では腕時計・ネックレス等が抜けており、
# 本当は小物なのに『服』と誤ってラベル付けしていた。その分だけ『服を誤って捨てる率』が
# 실際より悪く出ていたので、言葉を足して測り直す）
NON_CLOTHING_WORDS = ["バッグ", "バック", "リュック", "ショルダー", "トート", "財布",
                      "ウォレット", "スニーカー", "ブーツ", "シューズ", "ベルト",
                      "サドルバッグ", "アンティゴナ", "マテラッセ", "メトロポリタン",
                      "ターンロック", "サックドジュール", "ボーラン", "シティバッグ",
                      "ハンドバッグ", "ポーチ", "クラッチ", "ボストン", "巾着", "ミューズ",
                      "腕時計", "時計", "ウォッチ", "ネックレス", "ペンダント", "チョーカー",
                      "ブレスレット", "バングル", "ピアス", "イヤリング", "指輪", "リング",
                      "ブローチ", "サングラス", "メガネ", "眼鏡", "帽子", "キャップ",
                      "ハット", "ベレー", "マフラー", "ストール", "スカーフ", "手袋",
                      "グローブ", "ネクタイ", "キーケース", "キーホルダー", "カードケース",
                      "名刺入れ", "コインケース", "小銭入れ", "パンプス", "ローファー",
                      "サンダル", "靴下", "ソックス", "香水", "傘"]
CLOTHING_WORDS = ["デニム", "tシャツ", "ｔシャツ", "シャツ", "ジャケット", "パーカー",
                  "ニット", "コート", "スキニー", "パンツ", "スウェット", "ジャージ",
                  "ライダース", "ブルゾン", "カーディガン", "ダウン", "トラックジャケット"]


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


def label_of(keyword, name):
    """正解ラベルを決める。決められなければ None。

    順番が大事: 『商品名』を先に見る。検索キーワードが「ジバンシィ Tシャツ」でも、
    実際に出てきた商品が「ジバンシィ ネックレス」のことがあるため、
    キーワードを優先すると正解ラベルが間違う（1回目の測定の反省）。
    """
    nm = (name or "").lower()
    kw = (keyword or "").lower()
    # 1) 商品名に種類が書いてあれば、それが一番確か
    if any(w.lower() in nm for w in NON_CLOTHING_WORDS):
        return "服以外"
    if any(w.lower() in nm for w in CLOTHING_WORDS):
        return "服"
    # 2) 商品名で分からない時だけ、検索キーワードで判断する
    if any(w.lower() in kw for w in NON_CLOTHING_WORDS):
        return "服以外"
    if any(w.lower() in kw for w in CLOTHING_WORDS):
        return "服"
    return None


def main():
    if not os.path.exists(DB_FILE):
        msg = "🖼️ 写真判定の精度測定 中止: 相場DBがありません"
        print(msg)
        send_discord(msg)
        return

    import numpy as np

    con = sqlite3.connect(DB_FILE)
    rows = con.execute(
        "SELECT name, keyword, vec FROM items WHERE vec IS NOT NULL"
    ).fetchall()
    con.close()
    print(f"相場DBから {len(rows)} 件を読み込みました")

    # 正解ラベルが付けられる物だけを測定に使う
    samples = []  # (ラベル, 指紋, 商品名)
    for name, keyword, vec in rows:
        lab = label_of(keyword, name)
        if not lab:
            continue
        v = np.frombuffer(vec, dtype=np.float16).astype("float32")
        n = np.linalg.norm(v)
        if n == 0:
            continue
        samples.append((lab, v / n, name or ""))
    n_cloth = sum(1 for s in samples if s[0] == "服")
    n_other = sum(1 for s in samples if s[0] == "服以外")
    print(f"測定に使えるデータ: 服 {n_cloth}件 / 服以外 {n_other}件")

    if n_cloth < 20 or n_other < 20:
        msg = (f"🖼️ 写真判定の精度測定 中止: データ不足"
               f"（服 {n_cloth}件 / 服以外 {n_other}件）")
        print(msg)
        send_discord(msg)
        return

    # 全件のグループ別スコアを計算（文章の指紋は初回1回だけ作られる）
    scored = []  # (ラベル, 服の点数, 服以外で一番高い点数, 一番近いグループ, 商品名)
    for lab, vec, name in samples:
        sc = priority.image_scores(vec)
        if not sc or "服" not in sc:
            continue
        others = {g: v for g, v in sc.items() if g != "服"}
        top_g = max(others, key=others.get)
        scored.append((lab, sc["服"], others[top_g], top_g, name))

    if not scored:
        msg = "🖼️ 写真判定の精度測定 中止: 判定用の文章の指紋を作れませんでした"
        print(msg)
        send_discord(msg)
        return

    lines = [f"写真判定（服かバッグか）の精度測定  服 {n_cloth}件 / 服以外 {n_other}件",
             "", "【『服以外と判断する差』を変えた時】",
             "  差      服を誤って捨てる率     服以外を正しく弾く率"]
    best = None
    for margin in (0.00, 0.01, 0.02, 0.03, 0.05, 0.08, 0.10):
        miss = sum(1 for lab, c, o, g, n in scored
                   if lab == "服" and o - c >= margin)
        catch = sum(1 for lab, c, o, g, n in scored
                    if lab == "服以外" and o - c >= margin)
        tot_c = sum(1 for s in scored if s[0] == "服")
        tot_o = sum(1 for s in scored if s[0] == "服以外")
        miss_r = miss / tot_c * 100 if tot_c else 0
        catch_r = catch / tot_o * 100 if tot_o else 0
        lines.append(f"  {margin:.2f}    {miss_r:5.1f}%                {catch_r:5.1f}%")
        # 服の取りこぼしが1%以下で、一番よく弾ける設定を推奨にする
        if miss_r <= 1.0 and (best is None or catch_r > best[1]):
            best = (margin, catch_r, miss_r)
    lines.append("")
    if best:
        lines.append(f"  → 推奨: 差 {best[0]:.2f}"
                     f"（服の取りこぼし {best[2]:.1f}% / 服以外を {best[1]:.1f}% 弾ける）")
    else:
        lines.append("  → どの設定でも服の取りこぼしが1%を超える。写真判定は有効にしない方がよい")

    # グループ別の内訳（どの種類が得意/苦手か）
    lines.append("")
    lines.append("【服以外を種類ごとに見た時（差0.03の場合）】")
    for g in ("バッグ", "靴", "小物"):
        sub = [s for s in scored if s[0] == "服以外" and s[3] == g]
        if sub:
            hit = sum(1 for s in sub if s[2] - s[1] >= 0.03)
            lines.append(f"  {g}と判定された{len(sub)}件のうち、差0.03以上で弾けた: "
                         f"{hit}件 ({hit / len(sub) * 100:.1f}%)")

    # 人の目で見直す用: 服なのに『服以外』と判定されそうな物
    lines.append("")
    lines.append("【要確認: 服なのに服以外と判定された商品（上位10件）】")
    miss_list = sorted([s for s in scored if s[0] == "服" and s[2] > s[1]],
                       key=lambda s: -(s[2] - s[1]))[:10]
    for lab, c, o, g, name in miss_list:
        lines.append(f"  差{o - c:+.3f} ({g}と判定) {name[:45]}")
    lines.append("")
    lines.append("【参考: 服以外を正しく弾けた例（上位5件）】")
    hit_list = sorted([s for s in scored if s[0] == "服以外" and s[2] > s[1]],
                      key=lambda s: -(s[2] - s[1]))[:5]
    for lab, c, o, g, name in hit_list:
        lines.append(f"  差{o - c:+.3f} ({g}と判定) {name[:45]}")

    report = "\n".join(lines)
    print(report)
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(report)
    send_discord("🖼️ 写真判定の精度測定\n" + "\n".join(lines[:14]))


if __name__ == "__main__":
    main()
