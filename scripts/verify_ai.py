# -*- coding: utf-8 -*-
"""
AIによる「同じ商品か」の最終確認 部品（任意機能）

写真2枚をAIに見せて「同じ商品ですか？」と質問し、人の目レベルの判定をもらう。

使えるAI（どちらかのカギがあれば動く。無ければ静かにスキップ）:
  1. GEMINI_API_KEY    … GoogleのAI。無料枠あり（カード登録不要）← おすすめ
     カギの作り方: https://aistudio.google.com/apikey で「APIキーを作成」
  2. ANTHROPIC_API_KEY … Claude。精度高いが有料（1判定 約0.2〜0.5円）
"""

import os
import json
import base64
import urllib.request
import urllib.error

# Geminiのモデル名は世代交代が早く、古い名前は無料枠の対象外になることがある。
# 上から順に試して、動いた物を覚えて使い続ける。
GEMINI_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-flash-latest",
    "gemini-2.0-flash",
]
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_gemini_model = None  # 動いたモデル名を覚えておく
CLAUDE_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
}


def available():
    """AI最終確認が使えるか（どちらかのカギが設定されているか）"""
    return bool(os.environ.get("GEMINI_API_KEY", "").strip()
                or os.environ.get("ANTHROPIC_API_KEY", "").strip())


def _fetch_b64(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as res:
        raw = res.read()
    media = "image/png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    return base64.b64encode(raw).decode(), media


def _prompt(title_a, title_b):
    return (
        "あなたはブランド古着の鑑定士です。1枚目と2枚目の写真の商品が"
        "『同一のモデル・デザインの商品』かを判定してください。\n"
        f"1枚目の商品名: {title_a[:80]}\n"
        f"2枚目の商品名: {title_b[:80]}\n"
        "色違い・型番違い・ディテール（金具/ポケット/ステッチ/プリント/形状）の"
        "違いがあれば『違う』としてください。\n"
        "回答は次の1語だけ: 同じ / 違う / 不明"
    )


def _parse(text):
    text = (text or "").strip()
    if text.startswith("同じ"):
        return "same"
    if text.startswith("違う"):
        return "different"
    return "unsure"


def _ask_gemini(url_a, url_b, title_a, title_b):
    global _gemini_model
    b64a, ma = _fetch_b64(url_a)
    b64b, mb = _fetch_b64(url_b)
    payload = {"contents": [{"parts": [
        {"inline_data": {"mime_type": ma, "data": b64a}},
        {"inline_data": {"mime_type": mb, "data": b64b}},
        {"text": _prompt(title_a, title_b)},
    ]}]}
    key = os.environ["GEMINI_API_KEY"].strip()
    body = json.dumps(payload).encode("utf-8")

    # 動くモデルが分かっていればそれだけ、まだなら候補を上から順に試す
    models = [_gemini_model] if _gemini_model else GEMINI_MODELS
    last_err = None
    for model in models:
        req = urllib.request.Request(
            f"{GEMINI_BASE}/{model}:generateContent?key={key}",
            data=body, method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                data = json.loads(res.read().decode("utf-8"))
            if _gemini_model != model:
                _gemini_model = model
                print(f"  Gemini: モデル {model} を使用")
            parts = (data.get("candidates") or [{}])[0].get(
                "content", {}).get("parts", [])
            return _parse("".join(p.get("text", "") for p in parts))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            print(f"  Gemini {model}: {e.code} {detail[:120]}")
            last_err = e
            if e.code in (404, 429):
                continue  # このモデルは使えない→次の候補へ
            raise
    raise last_err


def _ask_claude(url_a, url_b, title_a, title_b):
    def block(url):
        b64, media = _fetch_b64(url)
        return {"type": "image",
                "source": {"type": "base64", "media_type": media, "data": b64}}
    payload = {"model": CLAUDE_MODEL, "max_tokens": 10,
               "messages": [{"role": "user", "content": [
                   block(url_a), block(url_b),
                   {"type": "text", "text": _prompt(title_a, title_b)}]}]}
    req = urllib.request.Request(
        CLAUDE_URL, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json",
                 "x-api-key": os.environ["ANTHROPIC_API_KEY"].strip(),
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=60) as res:
        data = json.loads(res.read().decode("utf-8"))
    return _parse("".join(b.get("text", "") for b in data.get("content", [])))


def same_product(url_a, url_b, title_a="", title_b=""):
    """写真2枚が同じ商品かAIに聞く。
    返り値: "same" / "different" / "unsure" / None(エラー・カギ無し)
    """
    try:
        if os.environ.get("GEMINI_API_KEY", "").strip():
            return _ask_gemini(url_a, url_b, title_a, title_b)
        if os.environ.get("ANTHROPIC_API_KEY", "").strip():
            return _ask_claude(url_a, url_b, title_a, title_b)
        return None
    except Exception as e:
        print(f"  AI最終確認に失敗: {e}")
        return None
