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
# 柄の有無の正解ラベルを付けるための言葉
PATTERN_WORDS = ["総柄", "柄物", "プリント", "グラフィック", "刺繍", "ワッペン", "パッチ",
                 "スタッズ", "スパンコール", "ダメージ", "クラッシュ", "ペイント", "ペンキ",
                 "タイダイ", "カモフラ", "迷彩", "レオパード", "ヒョウ柄", "ゼブラ",
                 "ペイズリー", "チェック", "ストライプ", "ボーダー", "花柄", "ボタニカル",
                 "スカル", "ドクロ", "バンダナ", "アロハ"]
PLAIN_WORDS = ["無地", "プレーン", "ソリッド", "シンプル"]
COLOR_WORDS = ["黒", "ブラック", "白", "ホワイト", "紺", "ネイビー", "グレー", "ベージュ",
               "カーキ", "ブラウン", "茶", "インディゴ"]

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
        "SELECT name, keyword, vec, vec2 FROM items "
        "WHERE vec IS NOT NULL AND vec2 IS NOT NULL"
    ).fetchall()
    con.close()
    print(f"相場DBから {len(rows)} 件を読み込みました")

    def unit(blob):
        """指紋を読み込んで長さ1にそろえる（近さの計算を正確にするため）"""
        v = np.frombuffer(blob, dtype=np.float16).astype("float32")
        n = np.linalg.norm(v)
        return None if n == 0 else v / n

    # 正解ラベルが付けられる物だけを測定に使う
    samples = []  # (ラベル, CLIP指紋, 商品名, DINOv2指紋)
    for name, keyword, vec, vec2 in rows:
        lab = label_of(keyword, name)
        if not lab:
            continue
        v, v2 = unit(vec), unit(vec2)
        if v is None or v2 is None:
            continue
        samples.append((lab, v, name or "", v2))
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
    for lab, vec, name, vec2 in samples:
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

    # ========== ここから「柄があるか・単調か」の精度測定 ==========
    lines.append("")
    lines.append("=" * 60)
    lines.append("柄判定（派手な柄か・無地で単調か）の精度測定")

    def pattern_label(name):
        """商品名から『派手』『単調』の正解ラベルを付ける。決められなければ None"""
        nm = (name or "").lower()
        has_pat = any(w.lower() in nm for w in PATTERN_WORDS)
        has_plain = any(w.lower() in nm for w in PLAIN_WORDS)
        # 両方の言葉がある商品名は、どちらとも言えないので測定から外す
        # （例:「スタッズ Tシャツ 無地T 黒」）
        if has_pat and has_plain:
            return None
        if has_pat:
            return "派手"
        if has_plain:
            return "単調"
        # 色名が書いてあり、柄の言葉が1つも無い服は『単調』とみなす
        if any(w.lower() in nm for w in COLOR_WORDS):
            return "単調"
        return None

    pat = []  # (ラベル, 派手の点数, 単調の点数, 商品名)
    for lab, vec, name, vec2 in samples:
        if lab != "服":
            continue  # 服だけで測る（バッグ等は対象外）
        plab = pattern_label(name)
        if not plab:
            continue
        sc = priority.pattern_scores(vec)
        if not sc or "派手" not in sc or "単調" not in sc:
            continue
        pat.append((plab, sc["派手"], sc["単調"], name or ""))

    n_hade = sum(1 for x in pat if x[0] == "派手")
    n_plain = sum(1 for x in pat if x[0] == "単調")
    lines.append(f"  測定に使えたデータ: 派手 {n_hade}件 / 単調 {n_plain}件")
    if n_hade >= 20 and n_plain >= 20:
        lines.append("")
        lines.append("  差      派手な服を誤って捨てる率   単調な服を正しく弾く率")
        best_p = None
        for margin in (0.00, 0.01, 0.02, 0.03, 0.05, 0.08):
            miss = sum(1 for l, h, pl, n in pat if l == "派手" and pl - h >= margin)
            catch = sum(1 for l, h, pl, n in pat if l == "単調" and pl - h >= margin)
            miss_r = miss / n_hade * 100
            catch_r = catch / n_plain * 100
            lines.append(f"  {margin:.2f}    {miss_r:5.1f}%                  {catch_r:5.1f}%")
            if miss_r <= 3.0 and (best_p is None or catch_r > best_p[1]):
                best_p = (margin, catch_r, miss_r)
        lines.append("")
        if best_p:
            lines.append(f"  → 推奨: 差 {best_p[0]:.2f}（派手な服の取りこぼし {best_p[2]:.1f}% / "
                         f"単調な服を {best_p[1]:.1f}% 弾ける）")
        else:
            lines.append("  → どの設定でも派手な服の取りこぼしが3%を超える。柄判定は有効にしない方がよい")
        lines.append("")
        lines.append("  【要確認: 派手なのに単調と判定された商品（上位5件）】")
        for l, h, pl, n in sorted([x for x in pat if x[0] == "派手" and x[2] > x[1]],
                                  key=lambda x: -(x[2] - x[1]))[:5]:
            lines.append(f"    差{pl - h:+.3f} {n[:45]}")
    else:
        lines.append("  データ不足のため測定できませんでした")

    # ========== 平均像くらべ（保存済みの指紋から派手/単調を見分ける）==========
    # やり方: 過去の『派手な服たち』『単調な服たち』それぞれの指紋の平均（＝平均像）を
    # 作り、新しい商品がどちらの平均像に近いかで判定する。実際の商品から学ぶぶん、
    # 文章と比べるゼロショットより細部に強いはず。
    # 公平に測るため、平均像を作る7割と、答え合わせに使う3割に分けて測定する。
    lines.append("")
    lines.append("=" * 60)
    lines.append("平均像くらべ（保存済み指紋から派手/単調を見分ける）")

    pat_all = []  # (ラベル, CLIP指紋, DINO指紋, 商品名)
    for lab, vec, name, vec2 in samples:
        if lab != "服":
            continue
        plab = pattern_label(name)
        if plab:
            pat_all.append((plab, vec, vec2, name))

    import random
    random.seed(7)  # 毎回同じ分け方になるように
    random.shuffle(pat_all)
    split = int(len(pat_all) * 0.7)
    train, test = pat_all[:split], pat_all[split:]
    lines.append(f"  平均像を作るのに使う: {len(train)}件 / 答え合わせに使う: {len(test)}件")

    for which, idx in (("CLIP", 1), ("DINOv2", 2)):
        tr_h = [t[idx] for t in train if t[0] == "派手"]
        tr_p = [t[idx] for t in train if t[0] == "単調"]
        if len(tr_h) < 20 or len(tr_p) < 20:
            lines.append(f"  {which}: 学習データ不足")
            continue
        c_h = np.mean(np.stack(tr_h), axis=0)
        c_p = np.mean(np.stack(tr_p), axis=0)
        c_h = c_h / (np.linalg.norm(c_h) or 1)
        c_p = c_p / (np.linalg.norm(c_p) or 1)
        # 単調の平均像に近いほど差が大きくなる
        diffs = [(t[0], float(t[idx] @ c_p - t[idx] @ c_h)) for t in test]
        n_h = sum(1 for l, d in diffs if l == "派手")
        n_p = sum(1 for l, d in diffs if l == "単調")
        if not n_h or not n_p:
            continue
        lines.append("")
        lines.append(f"  【{which}の平均像くらべ】答え合わせ 派手{n_h}件 / 単調{n_p}件")
        lines.append("    差      派手を誤って捨てる率   単調を正しく弾く率")
        best_c = None
        for margin in (0.00, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12):
            miss = sum(1 for l, d in diffs if l == "派手" and d >= margin) / n_h * 100
            catch = sum(1 for l, d in diffs if l == "単調" and d >= margin) / n_p * 100
            lines.append(f"    {margin:.2f}    {miss:5.1f}%                {catch:5.1f}%")
            if miss <= 3.0 and (best_c is None or catch > best_c[1]):
                best_c = (margin, catch, miss)
        if best_c:
            lines.append(f"    → 推奨: 差 {best_c[0]:.2f}"
                         f"（取りこぼし {best_c[2]:.1f}% / 単調を {best_c[1]:.1f}% 弾ける）")
        else:
            lines.append("    → 取りこぼし3%以下で使える設定なし")

    report = "\n".join(lines)
    print(report)
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(report)
    send_discord("🖼️ 写真判定の精度測定\n" + "\n".join(lines[:14]))


if __name__ == "__main__":
    main()
