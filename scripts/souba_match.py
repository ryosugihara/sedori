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
    # 相場は変動するので、新しい取引（既定：半年以内）だけを参照する。
    # 指紋は CLIP と DINO の両方が揃っている行だけ使う（二重チェックのため）。
    cutoff = int(time.time()) - _souba_days() * 86400
    cols = [r[1] for r in con.execute("PRAGMA table_info(items)")]
    if "vec2" not in cols:
        con.close()
        print("  相場DBにDINO指紋がまだ無いため、画像照合は休止します")
        return
    rows = con.execute(
        "SELECT id, name, price, brand, size, image_url, vec, vec2 "
        "FROM items WHERE vec IS NOT NULL AND vec2 IS NOT NULL "
        "AND (updated IS NULL OR updated >= ?)", (cutoff,)
    ).fetchall()
    con.close()

    def to_mat(blobs):
        mat = np.stack([np.frombuffer(b, dtype=np.float16).astype("float32")
                        for b in blobs])
        # 念のため長さを1にそろえる（近さ計算を正確にするため）
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return mat / norms

    groups = {}
    for r in rows:
        groups.setdefault(_norm_brand(r[3]), []).append(r)
    for bn, rs in groups.items():
        mat_c = to_mat([r[6] for r in rs])  # CLIPの指紋たち
        mat_d = to_mat([r[7] for r in rs])  # DINOの指紋たち
        _cache["brands"][bn] = (mat_c, mat_d, rs)
    total = sum(len(g[2]) for g in _cache["brands"].values())
    print(f"  相場DB読み込み: {total}件 / {len(_cache['brands'])}ブランド")


def match_item(item, souba):
    """新着商品(item)の写真をDBと照合する。一致が無い/失敗なら None。

    souba には手数料・送料・しきい値が入っている（monitor.load_souba()の返り値）。
    """
    try:
        if not item.get("image"):
            return None  # 写真が無い商品は画像では判定できない

        text = (item.get("title", "") + " " + item.get("category", "")).lower()

        # 無地(特徴がない)デニム・パンツ等は、写真では同じ商品か見分けられない
        # （着丈やシルエットの違いが写真に出にくい）ため、画像判定しない。
        plain = any(k.lower() in text for k in souba.get("plain_cats", []))
        has_feature = any(w.lower() in text for w in souba.get("feature_words", []))
        if plain and not has_feature:
            return None

        _load()
        bn = _norm_brand(item.get("brand", ""))
        got = _cache["brands"].get(bn)
        if not got:
            return None  # このブランドの売却実例がまだDBに無い

        # 属性キーワード（例:コーティング）は『商品名に書いてあるか』が
        # 新着と実例で一致しないと照合しない。
        # ※コーティングデニムは写真だと普通の黒デニムとそっくりだが相場が別物のため。
        attr_words = [w.lower() for w in souba.get("attr_words", [])]

        def attrs_ok(ref_name):
            rn = (ref_name or "").lower()
            return all((w in text) == (w in rn) for w in attr_words)

        import numpy as np
        import fingerprint
        # 1回のダウンロードで、CLIPとDINOの2種類の指紋を作る
        vec_c, vec_d = fingerprint.embed_image_url_both(item["image"])
        if vec_c is None or vec_d is None:
            return None

        mat_c, mat_d, rs = got
        sims_c = mat_c @ vec_c  # CLIP（見た目の系統）の近さ
        sims_d = mat_d @ vec_d  # DINO（同一商品か）の近さ

        # 合格ライン（精度測定で決めた値。souba.jsonで調整できる）
        strong_c = souba.get("strong_th", 0.92)    # 同デザイン: CLIP
        strong_d = souba.get("strong_dino", 0.90)  # 同デザイン: DINO
        cand_c = souba.get("cand_th", 0.88)        # 似た系統: CLIP
        cand_d = souba.get("cand_dino", 0.80)      # 似た系統: DINO

        # 両方のAIが「似ている」と言った実例だけを候補にする（二重チェック）
        ok = (sims_c >= cand_c) & (sims_d >= cand_d)
        idx = np.where(ok)[0]
        # さらに属性キーワード（コーティング等）の食い違う実例を外す
        idx = [i for i in idx if attrs_ok(rs[i][1])]
        if len(idx) == 0:
            return None
        # 同一商品の判定が得意なDINOの点数が高い順に、最大5件
        idx = np.array(idx)
        picked = list(idx[np.argsort(-sims_d[idx])][:5])

        i0 = picked[0]
        best_c, best_d = float(sims_c[i0]), float(sims_d[i0])
        rank = ("同デザイン" if (best_c >= strong_c and best_d >= strong_d)
                else "似た系統")

        # GG柄・モノグラム等の「どれも同じに見える柄」は、写真では
        # 型番やサイズの違いを見分けられないので、同デザインと断定しない。
        # （繰り返し柄は幾何検証も誤って一致しやすいため、検証より先に除外）
        patterns = [p.lower() for p in souba.get("plain_patterns", [])]
        ref_text = (rs[i0][1] or "").lower()
        if rank == "同デザイン" and any(
                p in text or p in ref_text for p in patterns):
            rank = "似た系統"

        # 『同デザイン』は答え合わせに合格した時だけ名乗れる（二重確認）:
        #  1) 幾何検証（無料）… 細部の点が同じ位置関係で一致するか
        #  2) AI最終確認（カギがある時だけ）… AIが写真2枚を見比べて判定
        verified = None
        if rank == "同デザイン":
            import geom_verify
            import verify_ai
            raw_item = fingerprint.download_bytes(item["image"])
            raw_ref = fingerprint.download_bytes(rs[i0][5]) if rs[i0][5] else None
            geo_th = int(souba.get("geo_inliers", 15))
            inl = (geom_verify.inlier_count(raw_item, raw_ref)
                   if raw_item and raw_ref else 0)
            if inl >= geo_th:
                verified = f"幾何検証OK({inl}点一致)"
            elif verify_ai.available():
                v = verify_ai.same_product(item["image"], rs[i0][5],
                                           item.get("title", ""), rs[i0][1] or "")
                if v == "same":
                    verified = "AIが写真を見比べて確認"
            if not verified:
                rank = "似た系統"  # 答え合わせに落ちたら断定しない

        # 予想相場は『控えめ』に見積もる（高いモデルに引っ張られて
        # 利益を過大に出さないよう、安い方から2番目の売値を使う）
        prices = sorted(rs[i][2] for i in picked)
        estimate = int(prices[1] if len(prices) >= 3 else prices[0])

        fee, ship = souba["fee"], souba["shipping"]
        net = int(estimate * (1 - fee) - ship)  # メルカリ手取り
        buy = item.get("price_num")
        profit = (net - buy) if buy else None   # 予想利益（仕入値不明なら None）

        r0 = rs[i0]  # 一番似ていた実例（通知で根拠として見せる）
        return {
            "rank": rank,
            "verified": verified,  # 二重確認に合格した方法（Noneなら未確認）
            "best_sim": best_d,   # 同一商品らしさ(DINO)
            "clip_sim": best_c,   # 見た目の系統の近さ(CLIP)
            "estimate": estimate,
            "net": net,
            "profit": profit,
            "count": len(picked),
            "ref_name": (r0[1] or "")[:40],
            "ref_price": r0[2],
            "ref_url": f"https://jp.mercari.com/item/{r0[0]}",
            "ref_image": r0[5],  # 実例の写真URL（通知で店の写真と並べて見せる）
        }
    except Exception as e:
        print(f"  画像照合エラー: {e}")
        return None
