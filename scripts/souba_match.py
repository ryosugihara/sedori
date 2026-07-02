# -*- coding: utf-8 -*-
"""
画像照合 部品：新着商品の写真を、メルカリ相場DB(souba_db.sqlite)と見比べる

使い方（monitor.py から）:
    import souba_match
    if souba_match.ready():
        m = souba_match.match_item(item, souba)
        # m = {rank, best_sim, estimate, net, profit, count, ref_name, ref_price, ref_url}

rank の意味:
  「同デザイン」… 類似度がとても高い＝ほぼ同じ見た目の商品が売れている
  「似た系統」  … そこそこ似ている＝参考程度の相場

※AIが入っていない環境でも監視が壊れないよう、失敗したら None を返すだけ。
"""

import os
import re
import json
import time
import sqlite3
import statistics

DB_FILE = "data/souba_db.sqlite"

_cache = {"loaded": False, "brands": {}}


def _souba_days():
    """相場を何日分まで参照するか（souba.json の設定。既定=半年183日）"""
    try:
        with open("souba.json", "r", encoding="utf-8") as f:
            return int(json.load(f).get("設定", {}).get("相場参照期間_日", 183))
    except Exception:
        return 183


def _norm_brand(name):
    """ブランド名を比較しやすい形にする（小文字＋英数字だけ）"""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def ready():
    """画像照合が使える状態か（DBがあり、AIの道具も入っているか）"""
    if not os.path.exists(DB_FILE):
        return False
    try:
        import numpy  # noqa: F401
        import sentence_transformers  # noqa: F401
    except Exception:
        return False
    return True


def _load():
    """DBを読み込んで、ブランドごとに指紋の一覧を用意する（初回だけ）"""
    if _cache["loaded"]:
        return
    _cache["loaded"] = True
    import numpy as np
    con = sqlite3.connect(DB_FILE)
    # 相場は変動するので、新しい取引（既定：半年以内）だけを参照する
    cutoff = int(time.time()) - _souba_days() * 86400
    cols = [r[1] for r in con.execute("PRAGMA table_info(items)")]
    if "updated" in cols:
        rows = con.execute(
            "SELECT id, name, price, brand, size, image_url, vec "
            "FROM items WHERE vec IS NOT NULL "
            "AND (updated IS NULL OR updated >= ?)", (cutoff,)
        ).fetchall()
    else:  # 古い形のDBでも動くように
        rows = con.execute(
            "SELECT id, name, price, brand, size, image_url, vec "
            "FROM items WHERE vec IS NOT NULL"
        ).fetchall()
    con.close()

    groups = {}
    for r in rows:
        groups.setdefault(_norm_brand(r[3]), []).append(r)
    for bn, rs in groups.items():
        mat = np.stack([
            np.frombuffer(r[6], dtype=np.float16).astype("float32") for r in rs
        ])
        # 念のため長さを1にそろえる（近さ計算を正確にするため）
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1
        mat = mat / norms
        _cache["brands"][bn] = (mat, rs)
    total = sum(len(rs) for _, rs in _cache["brands"].values())
    print(f"  相場DB読み込み: {total}件 / {len(_cache['brands'])}ブランド")


def match_item(item, souba):
    """新着商品(item)の写真をDBと照合する。一致が無い/失敗なら None。

    souba には手数料・送料・しきい値が入っている（monitor.load_souba()の返り値）。
    """
    try:
        if not item.get("image"):
            return None  # 写真が無い商品は画像では判定できない
        _load()
        bn = _norm_brand(item.get("brand", ""))
        got = _cache["brands"].get(bn)
        if not got:
            return None  # このブランドの売却実例がまだDBに無い

        import numpy as np
        import fingerprint
        vec = fingerprint.embed_image_url(item["image"])
        if vec is None:
            return None

        mat, rs = got
        sims = mat @ vec  # 全実例との近さを一気に計算

        strong = souba.get("strong_th", 0.92)  # 「同デザイン」のライン
        cand = souba.get("cand_th", 0.86)      # 「似た系統」のライン
        order = np.argsort(-sims)
        picked = [i for i in order if sims[i] >= cand][:5]  # 近い順に最大5件
        if not picked:
            return None

        best = float(sims[picked[0]])
        rank = "同デザイン" if best >= strong else "似た系統"

        # 近い実例たちの「真ん中の値段」を予想相場にする（外れ値に強い）
        prices = [rs[i][2] for i in picked]
        estimate = int(statistics.median(prices))

        fee, ship = souba["fee"], souba["shipping"]
        net = int(estimate * (1 - fee) - ship)  # メルカリ手取り
        buy = item.get("price_num")
        profit = (net - buy) if buy else None   # 予想利益（仕入値不明なら None）

        r0 = rs[picked[0]]  # 一番似ていた実例（通知で根拠として見せる）
        return {
            "rank": rank,
            "best_sim": best,
            "estimate": estimate,
            "net": net,
            "profit": profit,
            "count": len(picked),
            "ref_name": (r0[1] or "")[:40],
            "ref_price": r0[2],
            "ref_url": f"https://jp.mercari.com/item/{r0[0]}",
        }
    except Exception as e:
        print(f"  画像照合エラー: {e}")
        return None
