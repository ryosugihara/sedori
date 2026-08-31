# -*- coding: utf-8 -*-
"""
AIのカギ（無料のGemini・有料のAnthropic）が正しく動くかの即席テスト

このプログラムがすること:
  1. 2つのカギが登録されているかを確かめる（カギの値そのものは表示しない）
  2. 無料のGemini（ふだん使う方）に、相場DBの実在写真で2問出す
       問1: 同じ写真2枚         → 「同じ」と答えられるか
       問2: 全然違う商品の写真2枚 → 「違う」と答えられるか
  3. 有料のAnthropic（Geminiが夜間の混雑で使えない時の予備）が登録されていれば、
     つながるかを短い質問1回で確かめ、写真の問題も1問だけ出す（費用 約1円）
  4. 結果をDiscordに送る（どちらのカギが効いているか一目で分かるように）
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
    has_gemini = bool(os.environ.get("GEMINI_API_KEY", "").strip())
    has_claude = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    if not has_gemini and not has_claude:
        msg = ("🔑 テスト中止：AIのカギが1つも登録されていません。\n"
               "GitHubのこのリポジトリ → Settings → Secrets and variables → Actions "
               "で、GEMINI_API_KEY（無料）か ANTHROPIC_API_KEY（有料）を登録してください。")
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

    bag = tee = None
    try:
        con = sqlite3.connect(DB)
        # 商品写真を2枚（バッグ1枚・Tシャツ1枚 = 明らかに違う商品）取り出す
        bag = con.execute(
            "SELECT name, image_url FROM items WHERE brand='COACH' "
            "AND image_url != '' LIMIT 1").fetchone()
        tee = con.execute(
            "SELECT name, image_url FROM items WHERE brand='Dior Homme' "
            "AND name LIKE '%Tシャツ%' AND image_url != '' LIMIT 1").fetchone()
        con.close()
    except Exception as e:
        print(f"相場DBを開けませんでした: {e}")

    lines = ["🔑 AIのカギ 動作テスト", ""]
    lines.append("【登録の状況】")
    lines.append(f"・無料のGemini（ふだん使う方）: {'✅ 登録あり' if has_gemini else '⬜ 未登録'}")
    lines.append(f"・有料のAnthropic（夜間の予備）: {'✅ 登録あり' if has_claude else '⬜ 未登録'}")
    lines.append("")

    # 写真の問題は相場DBの実物を使う。DBが無い時は接続確認だけにする
    can_ask = bool(bag and tee and bag[1] and tee[1])
    if not can_ask:
        lines.append("⚠️ 相場DBの写真を用意できなかったため、写真の問題は省略します")
        lines.append("")

    gemini_ok = None
    if has_gemini and not can_ask:
        # 写真が用意できない時は、つながるかどうかだけ確かめる
        lines.append("【無料のGemini】")
        gemini_ok = verify_ai.ping()
        lines.append(f"接続確認: {'🟢 つながりました' if gemini_ok else '🔴 失敗'}"
                     f"{'' if gemini_ok else f'（{verify_ai.LAST_ERROR}）'}")
        lines.append("")
    elif has_gemini:
        lines.append("【無料のGemini】")
        # 問1: 同じ写真2枚 → 「同じ」が正解
        v1 = verify_ai.same_product(bag[1], bag[1], bag[0], bag[0])
        ok1 = (v1 == "same")
        lines.append(f"問1 同じ写真2枚 → AIの答え: {v1} {'🟢 正解' if ok1 else '🔴 不正解'}")

        # 問2: バッグ vs Tシャツ → 「違う」が正解
        v2 = verify_ai.same_product(bag[1], tee[1], bag[0], tee[0])
        ok2 = (v2 == "different")
        lines.append(f"問2 違う商品2枚 → AIの答え: {v2} {'🟢 正解' if ok2 else '🔴 不正解'}")

        if v1 is None or v2 is None:
            gemini_ok = False
            lines.append("⚠️ Geminiにつながりませんでした（回数制限か、カギの値の確認が必要）")
        elif ok1 and ok2:
            gemini_ok = True
            lines.append("✅ Geminiは正常です")
        else:
            gemini_ok = True
            lines.append("⚠️ つながりますが答えが不安定です（質問文の調整が必要）")
        lines.append("")

    claude_ok = None
    if has_claude:
        lines.append("【有料のAnthropic（予備）】")
        ok, why = verify_ai.ping_claude()
        lines.append(f"接続確認: {'🟢 つながりました' if ok else f'🔴 失敗（{why}）'}")
        if ok and can_ask:
            # 写真1問だけ出す（1回 約1円）。予備が本当に判定までできるかの確認
            r = verify_ai.ask_claude_detail(bag[1], bag[1], bag[0], bag[0])
            v = r["verdict"] if r else None
            score = r.get("score") if r else None
            lines.append(f"写真の問題（同じ写真2枚）→ 答え: {v} "
                         f"{'' if score is None else f'({score}点)'} "
                         f"{'🟢 正解' if v == 'same' else '🔴 不正解'}")
            claude_ok = (v == "same")
        else:
            claude_ok = ok
        lines.append("")

    lines.append("【まとめ】")
    if gemini_ok and claude_ok:
        lines.append("✅ 無料AIと有料の予備、どちらも使えます。"
                     "夜にGeminiが混んでいる時も判定が止まりません。")
    elif gemini_ok and not has_claude:
        lines.append("✅ 無料AIは使えます。⬜ 有料の予備（ANTHROPIC_API_KEY）は未登録です。"
                     "登録すると、夜間の混雑で判定できない時も取りこぼしが減ります。")
    elif gemini_ok:
        lines.append("✅ 無料AIは使えますが、有料の予備が動きませんでした（上の理由を確認）。")
    elif claude_ok:
        lines.append("⚠️ 無料AIは今使えませんが、有料の予備が効いているので判定は続きます。")
    else:
        lines.append("🔴 どちらのAIも使えていません。カギの値を確認してください。")

    report = "\n".join(lines)
    print(report)
    send_discord(report)


if __name__ == "__main__":
    main()
