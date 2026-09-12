# -*- coding: utf-8 -*-
"""
調査・通知の優先順位 部品（watchlists/priority.json が設定の正）

このプログラムがすること:
  1. 『調査しない商品』を見分ける（skip_reason）。次の3つのどれかに当てはまる商品は、
     画像判定も通知も一切行わない（服のせどりに集中するため）:
       ① 服以外の言葉がある（バッグ・靴・財布・帽子など）
       ② 服の言葉が1つも無い（例:「ミューズトゥ」のようなバッグの型名だけの商品名）
       ③ シンプルな服（無地のTシャツ・黒ズボン等。派手さを示す言葉が1つも無い）
     ①②③はそれぞれ priority.json のスイッチで個別に切り替えられる
  2. 商品名（とカテゴリ）から優先度スコアを計算する
       - 派手・個性の強いデザイン: 加点 …… 高値で売りやすいため
       - 無地・シンプルな服: 減点 …… 画像判別が難しく利益も出にくいため
       - 今の季節（設定した日数ぶん先取り）の服: 加点 / 前の季節だけの服: 減点
  3. 商品リストやキーワードリストを「スコアが高い順」に並べ替える

使い方（スキャン側から）:
    import priority
    why = priority.skip_reason(商品名 + " " + カテゴリ)
    if why:                           # 服以外・シンプル服なら調査しない
        continue                      # why には理由（"服以外" 等）が入る
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
    """『今の季節』と『1つ前の季節』を返す。先取り_日数(既定15日)ぶん前倒しする。
    例: 先取り15日なら8月17日ごろから「秋」扱い → 秋物を先に調べる
    """
    s = _conf()
    ahead = int(s.get("先取り_日数", 15))
    d = (today or datetime.date.today()) + datetime.timedelta(days=ahead)
    season = _SEASONS[d.month]
    return season, _PREV[season]


def _has(text, words):
    """文章に、言葉のリストのどれかが含まれるか"""
    t = (text or "").lower()
    return any(w.lower() in t for w in words if w)


def is_non_clothing(text):
    """服以外（バッグ・靴・小物）の言葉が入っているか（設定の『低優先カテゴリ』）"""
    try:
        return _has(text, _conf().get("低優先カテゴリ", []))
    except Exception:
        return False


def is_clothing(text):
    """服だと分かる言葉（ジャケット・シャツ・パンツ等）が入っているか。
    バッグの型名だけの商品名（例:「ミューズトゥ」）は、ここで False になる。
    """
    try:
        return _has(text, _conf().get("服のカテゴリ", []))
    except Exception:
        return True  # 設定が読めない時は「服」とみなす（通知を止めないため）


def is_simple(text):
    """シンプルな服か（無地系のカテゴリで、派手さを示す言葉が1つも無い）"""
    try:
        s = _conf()
        if _has(text, s.get("派手デザイン", [])):
            return False
        return _has(text, s.get("シンプル判定_カテゴリ", []))
    except Exception:
        return False


def skip_reason(text):
    """この商品を『調査しない』理由を返す。調査してよければ None。
    3つのスイッチ（priority.json）で個別に切り替えられる:
      服以外_調査しない / 服と確認できない商品_調査しない / シンプル服_調査しない
    """
    try:
        s = _conf()
        if not s:
            return None
        if s.get("服以外_調査しない", False) and is_non_clothing(text):
            return "服以外"
        if s.get("服と確認できない商品_調査しない", False) and not is_clothing(text):
            return "服と確認できない"
        if s.get("シンプル服_調査しない", False) and is_simple(text):
            return "シンプル服"
        return None
    except Exception:
        return None  # 判定に失敗したら調査する（通知を止めない）


def skip_item(text):
    """この商品の調査を『行わない』か（True/False）。理由が要る時は skip_reason を使う"""
    return skip_reason(text) is not None


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

        # 1) 服以外（バッグ等）は大きく減点（『服以外_調査しない』が false の時の保険）
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
