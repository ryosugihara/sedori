# -*- coding: utf-8 -*-
"""
調査・通知の優先順位 部品（watchlists/priority.json が設定の正）

このプログラムがすること:
  1. 『調査しない商品』を見分ける。服以外かどうかは次の3段構えで判断する
     （どれも priority.json のスイッチで個別に切り替えられる）:
       ① 名前の言葉 …… 「バッグ」「財布」等がはっきり書いてあれば服以外
       ② サイトのカテゴリ …… 仕入れ先が付けている分類。名前が雑でも分類は正しいことが多い
       ③ 写真そのもの …… CLIPというAIが写真と文章を同じ土俵で比べられる性質を使い、
          「服の写真か・バッグの写真か」を名前に頼らず判定する（安く出ている狙い目の
          商品は名前が雑なことが多いため、これが最後の砦になる）
     加えて、シンプルな服（無地のTシャツ・黒ズボン等）も調査対象から外せる
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


def category_says(category):
    """仕入れ先サイトが付けている分類から判断する。
    返り値: "服以外" / "服" / None（分類が無い・どちらとも言えない）
    名前より信頼できる情報なので、名前の判定より先に使う。
    """
    try:
        c = (category or "").strip().lower()
        if not c:
            return None
        s = _conf()
        if not s.get("カテゴリで判定する", False):
            return None
        if _has(c, s.get("服以外のカテゴリ", [])):
            return "服以外"
        if _has(c, s.get("服のカテゴリ_サイト分類", [])):
            return "服"
        return None
    except Exception:
        return None


def skip_reason(text, category=None):
    """この商品を『調査しない』理由を返す。調査してよければ None。
    判断の順番（確実な情報から順に見る）:
      1. サイトのカテゴリが『服』と言っている → 名前が雑でも調査する（取りこぼし防止）
      2. サイトのカテゴリが『服以外』と言っている → 調査しない
      3. 名前に服以外の言葉がある → 調査しない
      4. （スイッチがオンなら）服の言葉が1つも無い → 調査しない ※既定はオフ
      5. シンプルな服 → 調査しない
    写真での判定は、写真の指紋を計算したあとに skip_by_image で別途行う。
    """
    try:
        s = _conf()
        if not s:
            return None
        says = category_says(category)
        if says == "服":
            # サイトが『服』と分類している物は、名前が雑でも調査する。
            # ただしシンプル服の除外だけは効かせる
            if s.get("シンプル服_調査しない", False) and is_simple(text):
                return "シンプル服"
            return None
        if says == "服以外":
            return "服以外(カテゴリ)"
        if s.get("服以外_調査しない", False) and is_non_clothing(text):
            return "服以外"
        if s.get("服と確認できない商品_調査しない", False) and not is_clothing(text):
            return "服と確認できない"
        if s.get("シンプル服_調査しない", False) and is_simple(text):
            return "シンプル服"
        return None
    except Exception:
        return None  # 判定に失敗したら調査する（通知を止めない）


# --- 写真そのものから「服かどうか」を判定する（名前に頼らない最後の砦）------------
# CLIPは「写真」と「文章」を同じ土俵（同じ形の指紋）で比べられる。
# あらかじめ『a photo of a jacket』等の文章を指紋にしておき、商品写真の指紋と
# どれが一番近いかを見るだけで、名前が無くても種類が分かる。
# 写真の指紋は画像照合ですでに計算しているので、追加の費用はほぼゼロ。
_img_labels = {"mat": None, "groups": None}


def _label_vectors():
    """判定用の文章を指紋にして覚えておく（初回だけ）。使えなければ None"""
    if _img_labels["mat"] is not None:
        return _img_labels["mat"], _img_labels["groups"]
    try:
        import numpy as np
        import fingerprint
        words = _conf().get("写真判定_言葉", {})
        if not words:
            return None, None
        vecs, groups = [], []
        for group, phrases in words.items():
            for phrase in phrases:
                vecs.append(fingerprint.embed_text(phrase))
                groups.append(group)
        _img_labels["mat"] = np.stack(vecs)
        _img_labels["groups"] = groups
        return _img_labels["mat"], groups
    except Exception as e:
        print(f"  写真判定の準備に失敗（写真判定は休止）: {e}")
        _img_labels["mat"], _img_labels["groups"] = None, None
        return None, None


def image_scores(vec_clip):
    """写真の指紋から、グループごとの近さを返す。例 {"服":0.27,"バッグ":0.21,...}
    使えない時は空の辞書。
    """
    try:
        mat, groups = _label_vectors()
        if mat is None or vec_clip is None:
            return {}
        sims = mat @ vec_clip
        best = {}
        for g, sim in zip(groups, sims):
            sim = float(sim)
            if sim > best.get(g, -1):
                best[g] = sim  # そのグループで一番近かった文章の点数
        return best
    except Exception:
        return {}


_pattern_labels = {"mat": None, "groups": None}


def _pattern_vectors():
    """柄判定用の文章を指紋にして覚えておく（初回だけ）"""
    if _pattern_labels["mat"] is not None:
        return _pattern_labels["mat"], _pattern_labels["groups"]
    try:
        import numpy as np
        import fingerprint
        words = _conf().get("写真柄判定_言葉", {})
        if not words:
            return None, None
        vecs, groups = [], []
        for group, phrases in words.items():
            for phrase in phrases:
                vecs.append(fingerprint.embed_text(phrase))
                groups.append(group)
        _pattern_labels["mat"] = np.stack(vecs)
        _pattern_labels["groups"] = groups
        return _pattern_labels["mat"], groups
    except Exception as e:
        print(f"  柄判定の準備に失敗（柄判定は休止）: {e}")
        _pattern_labels["mat"], _pattern_labels["groups"] = None, None
        return None, None


def pattern_scores(vec_clip):
    """写真の指紋から『派手』『単調』それぞれの近さを返す。例 {"派手":0.24,"単調":0.26}"""
    try:
        mat, groups = _pattern_vectors()
        if mat is None or vec_clip is None:
            return {}
        sims = mat @ vec_clip
        best = {}
        for g, sim in zip(groups, sims):
            sim = float(sim)
            if sim > best.get(g, -1):
                best[g] = sim
        return best
    except Exception:
        return {}


def skip_by_pattern(vec_clip):
    """写真を見て『単調なデザインの服』と判断できたら理由を返す。
    柄・装飾がある（または自信が無い）なら None。
    商品名が雑でも見た目で判断できるのが利点（名前だけの判定の穴を埋める）。
    """
    try:
        s = _conf()
        if not s.get("写真で柄を判定する", False):
            return None
        sc = pattern_scores(vec_clip)
        if not sc or "派手" not in sc or "単調" not in sc:
            return None
        margin = float(s.get("写真柄判定_単調と判断する差", 0.03))
        if sc["単調"] - sc["派手"] >= margin:
            return "単調デザイン(写真判定)"
        return None
    except Exception:
        return None


def skip_by_image(vec_clip):
    """写真を見て『服以外』と判断できたら理由を返す。服（または自信が無い）なら None。
    服以外のグループが服を『服以外と判断する差』以上 上回った時だけ弾く
    （差を大きくすると慎重になり、服の取りこぼしが減る）。
    """
    try:
        s = _conf()
        if not s.get("写真で判定する", False):
            return None
        scores = image_scores(vec_clip)
        if not scores or "服" not in scores:
            return None
        clothes = scores["服"]
        others = {g: v for g, v in scores.items() if g != "服"}
        if not others:
            return None
        top_g = max(others, key=others.get)
        margin = float(s.get("写真判定_服以外と判断する差", 0.03))
        if others[top_g] - clothes >= margin:
            return f"服以外(写真判定:{top_g})"
        return None
    except Exception:
        return None


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

        # 2) 派手・個性デザインは加点（見た目の派手さ）
        hade = any(w.lower() in t for w in s.get("派手デザイン", []))
        if hade:
            score += int(s.get("派手デザイン_点数", 2))
            reasons.append("派手デザイン")

        # 2-2) 希少さ・状態の言葉（アーカイブ・限定等）も加点する。
        # ただし見た目の派手さとは別物なので、シンプル判定には使わない
        if any(w.lower() in t for w in s.get("価値ワード", [])):
            score += int(s.get("価値ワード_点数", 3))
            reasons.append("希少価値")

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
