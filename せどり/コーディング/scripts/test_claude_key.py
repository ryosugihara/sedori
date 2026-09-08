# -*- coding: utf-8 -*-
"""
Anthropic(Claude)のカギの動作テスト（軽量・1回だけ・約0.1円）

このプログラムがすること:
  1. GitHub Secrets に登録された ANTHROPIC_API_KEY で、Claude に短い挨拶を1回送る
  2. 成功/失敗と、失敗した場合は「原因が何か」を日本語で判定する
     （キーの値が違う / Workspace未指定のキー / クレジット不足 など）
  3. 結果を recon/CLAUDE_KEY_TEST.txt に書き、Discordにも送る

メルカリには一切アクセスしない。Geminiのカギも使わない（Claude単体の確認）。
"""
# 再実行メモ: 2026-09-08 シークレット側への登録し直し後の再確認

import os
import json
import datetime
import urllib.request
import urllib.error

import verify_ai  # モデル名(CLAUDE_MODEL)を本番と同じ物にするため参照

REPORT = "せどり/データ/recon/CLAUDE_KEY_TEST.txt"


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


def main():
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    lines = [f"🔑 Anthropic(Claude)のカギ 動作テスト（{now}）", ""]

    if not key:
        lines.append("❌ ANTHROPIC_API_KEY が設定されていません。")
        lines.append("　GitHubの リポジトリ設定 → シークレットと変数 → Actions で、")
        lines.append("　名前が『ANTHROPIC_API_KEY』(一字一句この通り)になっているか確認してください。")
    else:
        payload = {"model": verify_ai.CLAUDE_MODEL, "max_tokens": 10,
                   "messages": [{"role": "user",
                                 "content": "OK とだけ答えてください"}]}
        req = urllib.request.Request(
            verify_ai.CLAUDE_URL, data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json",
                     "x-api-key": key,
                     "anthropic-version": "2023-06-01"})
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                data = json.loads(res.read().decode("utf-8"))
            text = "".join(b.get("text", "") for b in data.get("content", []))
            lines.append(f"✅ カギは正常です！ Claude({verify_ai.CLAUDE_MODEL}) が応答しました: 「{text.strip()[:20]}」")
            lines.append("　これで、無料AI(Gemini)が混雑・枠切れの時に自動でClaudeへ切り替わります。")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:400]
            except Exception:
                pass
            lines.append(f"❌ 接続できましたが、エラーが返りました（HTTP {e.code}）")
            low = detail.lower()
            if e.code == 401:
                lines.append("　原因: キーの値が正しくない可能性が高い（貼り付けミス・削除済み・期限切れ）。")
                lines.append("　→ platform.claude.com の API keys でキーを作り直し、GitHubに登録し直してください。")
            elif e.code == 400 and "workspace" in low:
                lines.append("　原因: キーがWorkspace(作業場所)に紐づいていない。")
                lines.append("　→ キーを作り直す時に Workspace で『Default Workspace』を指定してください。")
            elif e.code == 400 and ("credit" in low or "billing" in low):
                lines.append("　原因: クレジット(前払い残高)が入っていない。")
                lines.append("　→ platform.claude.com の Settings → Billing でクレジットを購入してください。")
            else:
                lines.append(f"　サーバーの返答(先頭部分): {detail[:200]}")
                lines.append("　※この返答にカギの値は含まれていません")
        except Exception as e:
            lines.append(f"❌ 接続自体に失敗しました: {type(e).__name__}: {str(e)[:100]}")

    report = "\n".join(lines)
    print(report)
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(report)
    send_discord(report)


if __name__ == "__main__":
    main()
