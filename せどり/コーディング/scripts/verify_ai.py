# -*- coding: utf-8 -*-
"""
AIによる「同じ商品か」の最終確認 部品（任意機能）

写真2枚をAIに見せて「同じ商品ですか？」と質問し、人の目レベルの判定をもらう。

このプログラムがすること:
  1. 2枚の写真と商品名をAIに渡し、決まった手順（特徴を言葉にする → 比べる →
     違いが写り方の差か本当の違いかを見極める → 点数を付ける）で判定させる
  2. AIの答えは「一致度(0〜100点)・理由・違う点・結論」のJSONで受け取る
     （1語の答えだけだった以前の方式より、判定が安定し、理由が残る）
  3. 点数を『同じ／違う／不明』に変換して返す（合格ラインは souba.json で調整）
  4. 判定の理由を せどり/データ/recon/AI_VERIFY_LOG.txt に残す
     （「なぜ通知に至らなかったか」をあとで見返すため）

使えるAI（どちらかのカギがあれば動く。無ければ静かにスキップ）:
  1. GEMINI_API_KEY    … GoogleのAI。無料枠あり（カード登録不要）← おすすめ
     カギの作り方: https://aistudio.google.com/apikey で「APIキーを作成」
  2. ANTHROPIC_API_KEY … Claude。精度高いが有料（1判定 約0.5〜1円）

AIが読めない情報（内タグの型番・年代・生産国）は評価軸に入れていない。
入れるとAIが想像で埋めてしまい、かえって誤判定が増えるため。
"""

import os
import re
import json
import time
import base64
import datetime
import urllib.request
import urllib.error

_last_call = [0.0]  # 直前にAIを呼んだ時刻（無料枠の回数制限を守るため）
MIN_INTERVAL = 5.0  # 呼び出しの間隔（秒）

# 判定の合格ライン（souba.json の設定で変えられる。無ければこの値）
#   点数 >= SAME_SCORE → 「同じ」 / 点数 <= DIFF_SCORE → 「違う」 / その間 → 「不明」
SAME_SCORE_DEFAULT = 80
DIFF_SCORE_DEFAULT = 40

LOG_FILE = "せどり/データ/recon/AI_VERIFY_LOG.txt"
LOG_MAX_LINES = 1500   # これを超えたら古い行から消す（ファイルが太り続けないように）

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
CLAUDE_MAX_TOKENS = 800  # JSONで理由まで書かせるため、1語方式(10)より長くする
HTTP_TIMEOUT = 45        # 1回の応答待ちの上限(秒)。混雑時に延々待たないため
PER_CALL_BUDGET = 120    # 1判定に使ってよい合計時間(秒)。モデル乗り換えの暴走防止

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


def _thresholds():
    """合格ライン(同じ/違う)を souba.json から読む。読めなければ既定値"""
    try:
        with open("せどり/データ/watchlists/souba.json", "r", encoding="utf-8") as f:
            s = json.load(f).get("設定", {})
        return (int(s.get("AI確認_同じと判定する点数", SAME_SCORE_DEFAULT)),
                int(s.get("AI確認_違うと判定する点数", DIFF_SCORE_DEFAULT)))
    except Exception:
        return SAME_SCORE_DEFAULT, DIFF_SCORE_DEFAULT


def _fetch_b64(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as res:
        raw = res.read()
    media = "image/png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    return base64.b64encode(raw).decode(), media


def _prompt(title_a, title_b):
    """AIへの指示書。評価軸・絶対条件・手順・出力形式を固定する。"""
    return (
        "あなたはブランド古着のプロのバイヤー兼鑑定士です。\n"
        "1枚目の写真＝商品A、2枚目の写真＝商品B です。写真と商品名を見比べて、"
        "両者が『同一の型で、同一のカラー展開の商品』かどうかを判定し、"
        "一致度（0〜100点）と理由をJSONで出力してください。\n\n"
        f"商品Aの商品名: {title_a[:80]}\n"
        f"商品Bの商品名: {title_b[:80]}\n\n"
        "# 判定の手順（必ずこの順で考える）\n"
        "1. 商品Aの特徴を言葉にする: アイテムの種類／メインカラーと配色／"
        "ロゴ・柄・プリントの種類と位置／ポケット・ボタン・ジップ・金具・襟・"
        "ステッチなどの細部／シルエット\n"
        "2. 商品Bについても同じ項目を言葉にする\n"
        "3. 一致している点と違う点を洗い出す\n"
        "4. 違う点が『写真の撮り方（角度・光・トリミング・背景・着用/平置き）』の差なのか、"
        "『物理的に別のデザイン』なのかを見極める\n"
        "5. 一致度を決める\n\n"
        "# 絶対条件（ここが違えば即座に別商品扱い）\n"
        "- アイテムの種類が明らかに違う（例: パーカーとジャケット、バッグと財布）→ 0点\n"
        "- 商品名のブランドが明らかに違う → 0点\n"
        "- カラー展開（色）が違う → 同じ型でも最大50点\n"
        "- ロゴ・柄・プリントの種類や位置が違う → 最大40点\n"
        "- 写真から読み取れない情報（内タグの型番・年代・生産国・サイズ）は推測で埋めず"
        "『不明』とし、加点も減点もしない\n\n"
        "# 点数の目安\n"
        "- 90〜100: 型・色・細部がすべて一致し、同一商品と断定できる\n"
        "- 70〜89: 型と色は一致。細部の一部が写り方の都合で確認できない\n"
        "- 40〜69: 似ているが、色または細部に違いがある可能性が残る\n"
        "- 0〜39: 別の商品\n\n"
        "# 出力形式（JSONのみ。前後の説明文やコードブロック記号は不要）\n"
        "{\"match_score\": 0から100の整数, \"is_same_product\": true または false, "
        "\"analysis\": {\"category\": \"種類の比較\", \"color\": \"色と配色の比較\", "
        "\"details\": \"ロゴ・細部・シルエットの比較\"}, "
        "\"differences\": \"違う点（無ければ「なし」）\", \"conclusion\": \"一文で結論\"}"
    )


def _extract_json(text):
    """AIの返答からJSON部分を取り出して辞書にする。読めなければ None"""
    text = (text or "").strip()
    # ```json ... ``` で囲まれていたら中身だけにする
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return None


def _parse(text):
    """AIの返答を {verdict, score, reason, differences} に変換する。

    verdict: "same" / "different" / "unsure"
    JSONが壊れていても、点数だけ拾えれば判定に使う。それも無理なら『不明』。
    旧方式の1語回答（同じ/違う）が返ってきた場合も読めるようにしてある。
    """
    same_th, diff_th = _thresholds()
    data = _extract_json(text)
    score = None
    reason, diffs = "", ""
    if isinstance(data, dict):
        try:
            score = int(round(float(data.get("match_score"))))
        except Exception:
            score = None
        reason = str(data.get("conclusion") or "")[:200]
        diffs = str(data.get("differences") or "")[:200]
    if score is None:
        m = re.search(r'"?match_score"?\s*[:=]\s*(\d{1,3})', text or "")
        if m:
            score = int(m.group(1))
    if score is None:
        # 旧方式（1語）の答えにも対応
        t = (text or "").strip()
        if t.startswith("同じ"):
            return {"verdict": "same", "score": None, "reason": t[:100], "differences": ""}
        if t.startswith("違う"):
            return {"verdict": "different", "score": None, "reason": t[:100], "differences": ""}
        return {"verdict": "unsure", "score": None, "reason": "返答を読めなかった", "differences": ""}

    score = max(0, min(100, score))
    if score >= same_th:
        verdict = "same"
    elif score <= diff_th:
        verdict = "different"
    else:
        verdict = "unsure"
    return {"verdict": verdict, "score": score, "reason": reason, "differences": diffs}


def _wait_turn():
    """無料枠の「1分あたりの回数制限」を守るため、前の呼び出しから間隔をあける"""
    wait = MIN_INTERVAL - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()


def _ask_gemini(url_a, url_b, title_a, title_b):
    global _gemini_model
    _wait_turn()
    b64a, ma = _fetch_b64(url_a)
    b64b, mb = _fetch_b64(url_b)
    payload = {
        "contents": [{"parts": [
            {"inline_data": {"mime_type": ma, "data": b64a}},
            {"inline_data": {"mime_type": mb, "data": b64b}},
            {"text": _prompt(title_a, title_b)},
        ]}],
        # 低い温度＝毎回ほぼ同じ答えになる（判定のブレを減らす）。JSONで返すよう指定
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
    }
    key = os.environ["GEMINI_API_KEY"].strip()
    body = json.dumps(payload).encode("utf-8")

    # 動くと分かっているモデルを先頭に、他の候補も後ろに並べて順に試す
    # （回数制限(429)はモデルごとに別枠のことがあるため、固定せず他も試す）
    if _gemini_model:
        models = [_gemini_model] + [m for m in GEMINI_MODELS if m != _gemini_model]
    else:
        models = list(GEMINI_MODELS)
    last_err = None
    retried_wait = False  # 「何秒待て」の指示に従った再試行は1回だけ（待ちすぎ防止）
    deadline = time.time() + PER_CALL_BUDGET  # この判定に使ってよい時間の締切
    for model in models:
        if time.time() > deadline:
            break  # 時間切れ。残りのモデルは試さない
        req = urllib.request.Request(
            f"{GEMINI_BASE}/{model}:generateContent?key={key}",
            data=body, method="POST",
            headers={"Content-Type": "application/json"})
        for attempt in (1, 2):
            try:
                with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as res:
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
                    detail = e.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
                print(f"  Gemini {model}: {e.code} {detail[:120]}")
                last_err = e
                if e.code == 429 and attempt == 1 and not retried_wait:
                    # 返答に「retryDelay: Xs（X秒後に再試行して）」が入っていれば従う。
                    # 1分あたりの制限ならこれで通る。1日の上限なら待っても無駄なので
                    # 70秒を超える指示や2回目は諦めて次のモデルへ。
                    m = re.search(r'"retryDelay"\s*:\s*"(\d+)', detail)
                    wait_s = int(m.group(1)) if m else 0
                    if 0 < wait_s <= 70:
                        print(f"  Gemini: {wait_s}秒待って再試行します")
                        time.sleep(wait_s)
                        retried_wait = True
                        continue
                if e.code in (404, 429, 500, 503):
                    # 404=モデル無し / 429=回数制限 / 500・503=サーバー側の混雑・不調。
                    # どれもこのモデル固有のことが多いので、次の候補モデルを試す
                    break
                raise
            except (TimeoutError, urllib.error.URLError, OSError) as e:
                # 応答待ち切れ・接続の不調。混雑が原因のことが多いので次のモデルを試す
                print(f"  Gemini {model}: 接続失敗 {type(e).__name__}: {str(e)[:80]}")
                last_err = e
                break
    raise last_err


def _ask_claude(url_a, url_b, title_a, title_b):
    _wait_turn()

    def block(url):
        b64, media = _fetch_b64(url)
        return {"type": "image",
                "source": {"type": "base64", "media_type": media, "data": b64}}
    payload = {"model": CLAUDE_MODEL, "max_tokens": CLAUDE_MAX_TOKENS,
               "temperature": 0.1,
               "messages": [{"role": "user", "content": [
                   block(url_a), block(url_b),
                   {"type": "text", "text": _prompt(title_a, title_b)}]}]}
    req = urllib.request.Request(
        CLAUDE_URL, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json",
                 "x-api-key": os.environ["ANTHROPIC_API_KEY"].strip(),
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as res:
        data = json.loads(res.read().decode("utf-8"))
    return _parse("".join(b.get("text", "") for b in data.get("content", [])))


def _log(result, url_a, url_b, title_a, title_b):
    """判定の理由をrecon/AI_VERIFY_LOG.txt に残す（失敗しても本体は止めない）。
    商品ページのURLと商品名だけを書き、出品者の情報は一切含めない。
    """
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        score = "--" if result.get("score") is None else f"{result['score']:3d}点"
        jp = {"same": "同じ", "different": "違う", "unsure": "不明"}.get(result["verdict"], "?")
        line = (f"{now} | {score} | {jp} | A: {title_a[:30]} {url_a} | "
                f"B: {title_b[:30]} {url_b} | 結論: {result.get('reason', '')} | "
                f"違い: {result.get('differences', '')}\n")
        lines = []
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
        lines.append(line)
        if len(lines) > LOG_MAX_LINES:
            lines = lines[-LOG_MAX_LINES:]
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except Exception:
        pass


# 直前の呼び出しが失敗した理由（テスト・診断で「なぜ失敗したか」を数えるために使う）
LAST_ERROR = None


def ping():
    """AIが今使える状態かを、画像なしの軽い質問1回で確かめる。
    使えればTrue。テストが本番の測定（メルカリ取得）を始める前の生存確認用。
    """
    global LAST_ERROR, _gemini_model
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        if os.environ.get("ANTHROPIC_API_KEY", "").strip():
            return True  # Claudeは従量課金でほぼ常に使える前提
        LAST_ERROR = "カギ未設定"
        return False
    body = json.dumps({"contents": [{"parts": [{"text": "OK とだけ答えてください"}]}]}).encode("utf-8")
    for model in ([_gemini_model] if _gemini_model else []) + [m for m in GEMINI_MODELS if m != _gemini_model]:
        req = urllib.request.Request(
            f"{GEMINI_BASE}/{model}:generateContent?key={key}",
            data=body, method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                res.read()
            _gemini_model = model
            LAST_ERROR = None
            return True
        except urllib.error.HTTPError as e:
            LAST_ERROR = f"HTTPError: HTTP Error {e.code}"
            continue
        except Exception as e:
            LAST_ERROR = f"{type(e).__name__}: {str(e)[:80]}"
            continue
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return True  # Geminiが全滅でもClaudeに切り替えて判定できる
    return False


def same_product_detail(url_a, url_b, title_a="", title_b=""):
    """写真2枚が同じ商品かAIに聞き、点数と理由つきで返す。
    返り値: {verdict, score, reason, differences} / None(エラー・カギ無し)
      verdict: "same" / "different" / "unsure"
    """
    global LAST_ERROR
    has_gemini = bool(os.environ.get("GEMINI_API_KEY", "").strip())
    has_claude = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    if not has_gemini and not has_claude:
        LAST_ERROR = "カギ未設定"
        return None
    try:
        if has_gemini:
            try:
                result = _ask_gemini(url_a, url_b, title_a, title_b)
            except Exception as e:
                # Gemini(無料)が回数制限などで使えない時、Anthropic(有料)のカギが
                # あればそちらで続ける（1判定 約0.5〜1円）。無ければ諦める
                if not has_claude:
                    raise
                print(f"  Gemini失敗のためClaudeに切り替え: {type(e).__name__}: {str(e)[:80]}")
                result = _ask_claude(url_a, url_b, title_a, title_b)
        else:
            result = _ask_claude(url_a, url_b, title_a, title_b)
        LAST_ERROR = None
        _log(result, url_a, url_b, title_a, title_b)
        return result
    except Exception as e:
        # エラーの種類と中身の先頭だけを覚える（カギの値そのものは含まれない）
        LAST_ERROR = f"{type(e).__name__}: {str(e)[:120]}"
        print(f"  AI最終確認に失敗: {LAST_ERROR}")
        return None


def same_product(url_a, url_b, title_a="", title_b=""):
    """写真2枚が同じ商品かAIに聞く（従来どおりの簡易版）。
    返り値: "same" / "different" / "unsure" / None(エラー・カギ無し)
    """
    r = same_product_detail(url_a, url_b, title_a, title_b)
    return r["verdict"] if r else None
