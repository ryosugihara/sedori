# -*- coding: utf-8 -*-
"""
相場DBの「掃除」部品：相場の参考にならない売却実例を見分けて外す

このプログラムがすること:
  1. souba.json の『相場DB_除外キーワード』を読む
     （まとめ売り・ジャンク・箱のみ・新品 など、ふつうの中古1点の値段と違う実例の言葉）
  2. 商品名と状態ランクから「この実例は相場に使わない」理由を返す（exclude_reason）
  3. DBの中にすでに入っている該当行を消す（cleanse_db）。メルカリには一切アクセスしない

使う場所:
  - collect_souba.py … 収集のたびに最初に cleanse_db を実行し、新しく入れる時も
                       exclude_reason で弾く（弾いた物は指紋化しないので画像DLも減る）
  - souba_match.py   … 照合時にも exclude_reason で弾く（DBの掃除が済む前の保険）
  - souba_db_maintenance.py … 収集日を待たずに掃除だけを行うワークフロー用

除外した実例は、検索結果に出てきても二度とDBに入らない（毎回 exclude_reason で
弾かれる）ので、メルカリへのアクセス回数は増えない。
"""

import json

SOUBA_FILE = "せどり/データ/watchlists/souba.json"

# souba.json に設定が無い時に使う既定の言葉
DEFAULT_WORDS = [
    "まとめ売り", "おまとめ", "セット売り", "点セット", "枚セット", "着セット",
    "ジャンク", "部品取り", "訳あり", "難あり",
    "箱のみ", "タグのみ", "袋のみ", "保存袋のみ", "付属品のみ",
    "新品", "未使用", "タグ付", "デッドストック", "DEADSTOCK", "dead stock",
    "サンプル品", "レプリカ", "コピー品", "ノーブランド",
]

_cache = {"words": None}


def exclude_words():
    """除外キーワードの一覧を返す（souba.json の設定。無ければ既定）"""
    if _cache["words"] is not None:
        return _cache["words"]
    words = DEFAULT_WORDS
    try:
        with open(SOUBA_FILE, "r", encoding="utf-8") as f:
            s = json.load(f).get("設定", {})
        w = s.get("相場DB_除外キーワード")
        if isinstance(w, list) and w:
            words = [str(x) for x in w]
    except Exception:
        pass
    _cache["words"] = [w.lower() for w in words if w]
    return _cache["words"]


def exclude_reason(name, condition_id=None):
    """この売却実例を相場に使わない理由を返す。使ってよければ None。

    - 商品名に除外キーワードが含まれる → そのキーワード
    - 状態ランクが 1（新品、未使用） → "新品未使用"
    - 「〇〇様専用」の短い予約出品 → "専用出品"
    """
    text = (name or "").lower()
    if str(condition_id) == "1":
        return "新品未使用"
    if "専用" in text and len(text) <= 15:
        return "専用出品"
    for w in exclude_words():
        if w in text:
            return w
    return None


def cleanse_db(con):
    """DBの中の『相場に使わない実例』を消す。消した件数を理由ごとに返す。
    メルカリには一切アクセスしない（手元のDBの商品名だけを見る）。
    """
    counts = {}
    rows = con.execute("SELECT id, name, condition_id FROM items").fetchall()
    to_delete = []
    for iid, name, cond in rows:
        reason = exclude_reason(name, cond)
        if reason:
            to_delete.append(iid)
            counts[reason] = counts.get(reason, 0) + 1
    if to_delete:
        con.executemany("DELETE FROM items WHERE id = ?", [(i,) for i in to_delete])
        con.commit()
    return counts


def format_counts(counts):
    """消した件数の内訳を、人が読める1行にする"""
    if not counts:
        return "除外対象なし"
    total = sum(counts.values())
    top = sorted(counts.items(), key=lambda x: -x[1])[:8]
    return f"{total}件（" + "、".join(f"{k}:{v}" for k, v in top) + "）"
