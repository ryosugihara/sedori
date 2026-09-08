# -*- coding: utf-8 -*-
"""
調査・通知の優先順位 部品（watchlists/priority.json が設定の正）

このプログラムがすること:
  1. 商品名（とカテゴリ）から優先度スコアを計算する
       - 服以外（バッグ・靴・小物）: 減点 …… 服のせどりを中心にするため
       - 派手・個性の強いデザイン: 加点 …… 高値で売りやすいため
       - 無地・シンプルな服: 減点 …… 画像判別が難しく利益も出にくいため
       - 今の季節（約1ヶ月先取り）の服: 加点 / 前の季節だけの服: 減点
  2. 商品リストやキーワードリストを「スコアが高い順」に並べ替える

使い方（スキャン側から）:
    import priority
    items = priority.sort_by_score(items, key=lambda it: it.get("name", ""))
    targets = priority.sort_keywords(targets)  # (ブランド, キーワード) のリスト

※スコアは『調べる順番』にだけ使う。通知する/しないの条件は変えない。
※設定が読めない・壊れている場合は全部スコア0（＝今まで通りの順番）で動く。
"""

import json
import datetime

CONF_FILE = "せどり/データ/watchlists/priority.json"

_cache = {"conf": None}

# 月 → 季節（日本の目安）
_SEASONS = {3: "春", 4: "春", 5: "春", 6: "夏", 7: "夏", 8: "夏",
            9: "秋", 10: "秋", 11: "秋", 12: "冬", 1: "冬", 2: "冬"}
_PREV = {"春": "冬", "夏": "春", "秋": "夏", "冬": "秋"}


def _conf():
    """設定を読み込む（初回だけ）。読めなければ空＝スコア0で動く"""
    if _cache["conf"] is None:
        try:
            with open(CONF_FILE, "r", encoding="utf-8") as f:
                _cache["conf"] = json.load(f).get("設定", {})
        except Exception:
            _cache["conf"] = {}
    return _cache["conf"]


def current_season(today=None):
    """『今の季節』と『1つ前の季節』を返す。先取り_日数(既定30日)ぶん前倒しする。
    例: 8月下旬は(先取りにより)もう「秋」扱い → 秋物を先に調べる
    """
    s = _conf()
    ahead = int(s.get("先取り_日数", 30))
    d = (today or datetime.date.today()) + datetime.timedelta(days=ahead)
    season = _SEASONS[d.month]
    return season, _PREV[season]


def score_text(text):
    """文章（商品名やキーワード）から優先度スコアを計算して返す。
    返り値: (スコア, 理由のリスト)。設定が無ければ (0, [])
    """
    try:
        s = _conf()
        if not s:
            return 0, []
        t = (text or "").lower()
        score = 0
        reasons = []

        # 1) 服以外（バッグ等）は減点
        if any(w.lower() in t for w in s.get("低優先カテゴリ", [])):
            score += int(s.get("低優先カテゴリ_点数", -2))
            reasons.append("服以外")

        # 2) 派手・個性デザインは加点
        hade = any(w.lower() in t for w in s.get("派手デザイン", []))
        if hade:
            score += int(s.get("派手デザイン_点数", 2))
            reasons.append("派手デザイン")

        # 3) シンプル（無地系の服で、派手の言葉が1つも無い）は減点
        if not hade and any(w.lower() in t for w in s.get("シンプル判定_カテゴリ", [])):
            score += int(s.get("シンプル_点数", -2))
            reasons.append("シンプル")

        # 4) 季節: 今の季節(先取り込み)の服は加点、前の季節だけの服は減点
        seasons = s.get("季節の服", {})
        now, prev = current_season()
        now_words = [w.lower() for w in seasons.get(now, [])]
        prev_words = [w.lower() for w in seasons.get(prev, [])]
        in_now = any(w in t for w in now_words)
        in_prev = any(w in t for w in prev_words)
        if in_now:
            score += int(s.get("季節一致_点数", 2))
            reasons.append(f"{now}物")
        elif in_prev:
            # 前の季節『だけ』に属する服（今の季節のリストには無い）
            score += int(s.get("季節外れ_点数", -2))
            reasons.append(f"季節外れ({prev}物)")

        return score, reasons
    except Exception:
        return 0, []


def sort_by_score(items, key):
    """商品リストをスコアが高い順に並べ替える（同点は元の順番を保つ）。
    key は各商品から『スコア計算に使う文章』を取り出す関数。
    失敗したら元のリストをそのまま返す（監視を止めない）。
    """
    try:
        return sorted(items, key=lambda it: -score_text(key(it))[0])
    except Exception:
        return items


def sort_keywords(targets):
    """(ブランド, キーワード) のリストを、キーワードのスコアが高い順に並べ替える。
    服・季節物のキーワードから先に調べることで、1日の通知上限やAIの回数制限を
    優先度の高い商品に使う。
    """
    try:
        return sorted(targets, key=lambda bk: -score_text(bk[1])[0])
    except Exception:
        return targets
