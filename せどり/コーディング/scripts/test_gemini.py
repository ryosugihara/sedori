# -*- coding: utf-8 -*-
"""
Gemini(無料AI)のカギが正しく動くかの即席テスト

相場DBから実在の商品写真を取り出し、AIに2問だけ出題する:
  問1: 同じ写真2枚         → 「同じ」と答えられるか
  問2: 全然違う商品の写真2枚 → 「違う」と答えられるか
"""

import os
import json
import sqlite3
import urllib.request

import verify_ai

DB = "せどり/データ/data/souba_db.sqlite"


def send_discord(msg):
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        return
    data = json.dumps({"content": msg[:1900]}).encode()
    req = urllib.request.Request(
        webhook, data=data,
        headers={"Content-Type": "application/json",
                 "User-Agent": "sedori-bot/1.0 (+https://github.com/ryosugihara/sedori)"})
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception as e:
        print(f"Discord送信失敗: {e}")


def main():
    if not verify_ai.available():
        msg = "🔑 テスト中止：AIのカギ(GEMINI_API_KEY)がまだ設定されていません。"
        print(msg)
        send_discord(msg)
        return

    # 診断: このカギで使えるモデルの一覧を表示（原因調査に役立つ）
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        try:
            url = ("https://generativelanguage.googleapis.com/v1beta/models"
                   f"?key={key}&pageSize=50")
            with urllib.request.urlopen(url, timeout=30) as res:
                models = json.loads(res.read().decode()).get("models", [])
            flash = [m["name"].split("/")[-1] for m in models
                     if "flash" in m["name"] and
                     "generateContent" in m.get("supportedGenerationMethods", [])]
            print("使えるflash系モデル:", flash[:12])
        except Exception as e:
            print(f"モデル一覧の取得に失敗: {e}")

    con = sqlite3.connect(DB)
    # 商品写真を2枚（バッグ1枚・Tシャツ1枚 = 明らかに違う商品）取り出す
    bag = con.execute(
        "SELECT name, image_url FROM items WHERE brand='COACH' "
        "AND image_url != '' LIMIT 1").fetchone()
    tee = con.execute(
        "SELECT name, image_url FROM items WHERE brand='Dior Homme' "
        "AND name LIKE '%Tシャツ%' AND image_url != '' LIMIT 1").fetchone()
    con.close()

    lines = ["🔑 GoogleのAI(Gemini) 動作テスト", ""]

    # 問1: 同じ写真2枚 → 「同じ」が正解
    v1 = verify_ai.same_product(bag[1], bag[1], bag[0], bag[0])
    ok1 = (v1 == "same")
    lines.append(f"問1 同じ写真2枚 → AIの答え: {v1} {'🟢 正解' if ok1 else '🔴 不正解'}")

    # 問2: バッグ vs Tシャツ → 「違う」が正解
    v2 = verify_ai.same_product(bag[1], tee[1], bag[0], tee[0])
    ok2 = (v2 == "different")
    lines.append(f"問2 違う商品2枚 → AIの答え: {v2} {'🟢 正解' if ok2 else '🔴 不正解'}")

    lines.append("")
    if v1 is None or v2 is None:
        lines.append("⚠️ AIへの接続に失敗しました（カギの値や形式を確認します）")
    elif ok1 and ok2:
        lines.append("✅ カギは正常！AIの目が有効になりました。")
    else:
        lines.append("⚠️ 接続はできましたが答えが不安定です（質問文を調整します）")

    report = "\n".join(lines)
    print(report)
    send_discord(report)


if __name__ == "__main__":
    main()
