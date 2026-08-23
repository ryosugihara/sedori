# -*- coding: utf-8 -*-
"""
画像照合 部品：新着商品の写真を、メルカリ相場DB(souba_db.sqlite)と見比べる

使い方（monitor.py から）:
    import souba_match
    if souba_match.ready():
        m = souba_match.match_item(item, souba)
        # m = {rank, best_sim, estimate, net, profit,
        #      price_safe, price_mid, price_strong, count, confidence,
        #      ref_name, ref_price, ref_url}

rank の意味:
  「同デザイン」… 類似度がとても高い＝ほぼ同じ見た目の商品が売れている
  「似た系統」  … そこそこ似ている＝参考程度の相場

相場の出し方（price_model）:
  似ている売却実例を最大30件集め、外れ値（極端に高い/安い実例）を除いてから
  『安全(下から25%)／標準(中央値)／強気(下から75%)』の3つを出す。
  利益の判定には『標準』を使う（souba.json の『相場_判定に使う値』で変更可）。
  根拠の件数が『相場_最低件数』に満たない時は信頼度『低』として最安値で判定する。

※AIが入っていない環境でも監視が壊れないよう、失敗したら None を返すだけ。
"""

import os
import re
import json
import time
import sqlite3
import statistics

DB_FILE = "せどり/データ/data/souba_db.sqlite"

_cache = {"loaded": False, "brands": {}}


def _souba_days():
    """相場を何日分まで参照するか（souba.json の設定。既定=半年183日）"""
    try:
        with open("せどり/データ/watchlists/souba.json", "r", encoding="utf-8") as f:
            return int(json.load(f).get("設定", {}).get("相場参照期間_日", 183))
    except Exception:
        return 183


def _norm_brand(name):
    """ブランド名を比較しやすい形にする（小文字＋英数字だけ）"""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _percentile(sorted_vals, pct):
    """小さい順に並んだ値の『下から pct %』の位置の値を返す（numpy と同じ線形補間）。
    例: [100, 200, 300, 400] の 25% → 175
    """
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    pos = (n - 1) * pct / 100.0
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def price_model(prices, souba):
    """売れた値段の集まりから、外れ値を除いて『安全／標準／強気』の3つの相場を出す。

    やること:
      1. 値段を並べて、四分位（下から25%=Q1、50%=中央値、75%=Q3）を求める
      2. Q1〜Q3 の幅(IQR)の1.5倍より外にある値段を『外れ値』として捨てる
         （まとめ売りで異常に高い実例や、ジャンク品で異常に安い実例を除くため。
           件数が4件未満の時は外れ値の判断ができないので捨てない）
      3. 残った値段でもう一度 Q1／中央値／Q3 を出し、
           安全 = Q1（控えめに見た相場）
           標準 = 中央値（ふつうに売れる相場）
           強気 = Q3（高めに売れた相場）
         とする
      4. 根拠の件数から信頼度を付ける（最低件数に満たなければ『低』）
      5. 利益の判定に使う値(estimate)を決める。設定『相場_判定に使う値』に従うが、
         信頼度が『低』の時は強制的に一番安い実例の値段を使う（安全側に倒す）

    返り値: {estimate, safe, mid, strong, n_raw, n_used, n_outliers, confidence}
    prices が空なら None。
    """
    raw = sorted(int(p) for p in prices if p is not None and int(p) > 0)
    if not raw:
        return None
    n_raw = len(raw)

    # 外れ値の除去（4件以上ある時だけ）
    kept = raw
    if n_raw >= 4:
        q1, q3 = _percentile(raw, 25), _percentile(raw, 75)
        iqr = q3 - q1
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        kept = [p for p in raw if lo <= p <= hi]
        if not kept:  # 念のため（全部外れ値になることは理屈上ないが）
            kept = raw
    n_used = len(kept)
    n_outliers = n_raw - n_used

    safe = int(_percentile(kept, 25))
    mid = int(_percentile(kept, 50))
    strong = int(_percentile(kept, 75))

    # 信頼度: 根拠の件数で決める（souba.json で変えられる）
    min_n = int(souba.get("price_min_count", 5))
    good_n = int(souba.get("price_good_count", 10))
    if n_used < min_n:
        confidence = "低"
    elif n_used < good_n:
        confidence = "中"
    else:
        confidence = "高"

    # 利益判定に使う値: 設定で 安全/標準/強気 を選べる。信頼度『低』なら最安値。
    which = souba.get("price_basis", "標準")
    if confidence == "低":
        estimate = int(min(kept))
    elif which == "安全":
        estimate = safe
    elif which == "強気":
        estimate = strong
    else:
        estimate = mid

    return {
        "estimate": estimate,
        "safe": safe, "mid": mid, "strong": strong,
        "n_raw": n_raw, "n_used": n_used, "n_outliers": n_outliers,
        "confidence": confidence,
    }


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

    # まとめ売り・ジャンク・新品など「中古1点の相場にならない実例」を外す。
    # DB側の掃除（collect_souba / souba_db_maintenance）が済む前でも効くよう、
    # 読み込み時にも同じ基準で弾く（保険）。
    try:
        import souba_clean
        before = len(rows)
        rows = [r for r in rows if souba_clean.exclude_reason(r[1]) is None]
        if before - len(rows):
            print(f"  相場に使わない実例を除外: {before - len(rows)}件（まとめ売り・ジャンク・新品等）")
    except Exception as e:
        print(f"  除外キーワードの読み込みに失敗（全件を使います）: {e}")

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


def match_item(item, souba, stats=None, min_profit=None):
    """新着商品(item)の写真をDBと照合する。一致が無い/失敗なら None。

    souba には手数料・送料・しきい値が入っている（monitor.load_souba()の返り値）。
    stats に辞書を渡すと、どこで弾かれたか・類似度・利益の分布を集計する
    （诊断レポート用。個数を数えるだけで判定そのものには影響しない）。
    min_profit を渡すと、予想利益がその額未満の商品は重い答え合わせ
    （幾何検証・色比較・AI確認）を行わず None を返す。通知しない商品の照合を
    省いてスキャンを大幅に高速化する（送る商品は変わらない）。
    """
    def _count(key):
        if stats is not None:
            stats[key] = stats.get(key, 0) + 1

    try:
        _count("total")
        if not item.get("image"):
            _count("reason_その他_写真なし")
            return None  # 写真が無い商品は画像では判定できない

        text = (item.get("title", "") + " " + item.get("category", "")).lower()

        # 無地(特徴がない)デニム・パンツ等は、写真では同じ商品か見分けられない
        # （着丈やシルエットの違いが写真に出にくい）ため、画像判定しない。
        plain = any(k.lower() in text for k in souba.get("plain_cats", []))
        has_feature = any(w.lower() in text for w in souba.get("feature_words", []))
        if plain and not has_feature:
            _count("reason_その他_無地カテゴリ")
            return None

        _load()
        bn = _norm_brand(item.get("brand", ""))
        got = _cache["brands"].get(bn)
        if not got:
            _count("reason_相場データ不足")
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
            _count("reason_その他_画像取得失敗")
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
            _count("reason_類似度不足")
            return None
        # 同一商品の判定が得意なDINOの点数が高い順に並べる。
        #  - pool   … 相場(値段)の根拠に使う実例。上限 price_pool 件（既定30件）。
        #             以前は5件固定だったが、件数が少ないと1件の偏った値段に
        #             引っ張られるため、見つかった候補を幅広く使う。
        #  - picked … 答え合わせ（幾何検証・AI確認）を試す実例。上位5件まで
        #             （答え合わせは1件ごとに写真のDLと計算が必要で重いため）。
        idx = np.array(idx)
        ordered = list(idx[np.argsort(-sims_d[idx])])
        pool = ordered[:int(souba.get("price_pool", 30))]
        picked = ordered[:5]
        _count("similar_found")  # 類似商品が見つかった件数

        i0 = picked[0]
        best_c, best_d = float(sims_c[i0]), float(sims_d[i0])
        if best_d >= 0.90:
            _count("sim_90plus")  # 類似度(DINO)90%以上だった件数

        # GG柄・モノグラム等の「どれも同じに見える柄」は、写真では
        # 型番やサイズの違いを見分けられないので、同デザインと断定しない。
        # （繰り返し柄は幾何検証もAIも誤りやすいため、昇格の対象外にする）
        patterns = [p.lower() for p in souba.get("plain_patterns", [])]

        # 予想相場: 根拠の実例(pool)の値段から、外れ値を除いた四分位モデルで
        # 『安全(Q1)／標準(中央値)／強気(Q3)』を出す（price_model 参照）。
        # 以前は「上位5件のうち安い方から2番目」の1つの値段だけを使っていたが、
        # 根拠が少なく、その1件が偏っていると相場を誤るため、分布で見る形に変えた。
        pm = price_model([rs[i][2] for i in pool], souba)
        if pm is None:
            _count("reason_その他_相場計算失敗")
            return None
        estimate = pm["estimate"]
        _count(f"confidence_{pm['confidence']}")  # 相場の信頼度(高/中/低)の内訳

        fee, ship = souba["fee"], souba["shipping"]
        net = int(estimate * (1 - fee) - ship)  # メルカリ手取り
        buy = item.get("price_num")
        profit = (net - buy) if buy else None   # 予想利益（仕入値不明なら None）
        if profit is not None:
            notify_line = souba.get("notify_line", 2000)
            if profit < 0:
                _count("profit_negative")      # 利益がマイナスだった件数
            elif profit < notify_line:
                _count("profit_low")           # 利益0〜通知ライン未満だった件数
            else:
                _count("profit_ok")            # 利益が通知ライン以上だった件数

        # 予想利益が min_profit 未満なら、どのみち通知しないので、ここで打ち切って
        # 重い答え合わせ(参照画像DL・幾何検証・色比較・AI確認)を省く。
        # 実測では照合対象の約9割が利益ライン未満で、その全てに答え合わせしていた
        # ため、スキャンが制限時間内に終わらなくなっていた（送る商品は変わらない）。
        if min_profit is not None and profit is not None and profit < min_profit:
            return None

        # 通知する実例は必ず答え合わせに合格した物だけにする（二重確認）:
        #  1) 幾何検証（無料）… 細部の点が同じ位置関係で一致するか
        #  2) AI最終確認（カギがある時だけ）… AIが写真2枚を見比べて判定
        # 『似た系統』も含め、どちらにも合格できなかった実例は参考表示すら
        # しない（以前は『似た系統』がノーチェックで表示されており、
        # CLIP/DINOの類似度だけで無関係な商品が参考に出ることがあった）。
        #
        # 以前は一番似ていた実例(i0)だけを答え合わせしていたが、写り方
        # (角度・トリミング)次第でi0だけ幾何検証やAIに落ちることがあり、
        # 2番目以降の実例でなら合格したはずの商品まで捨てていた。
        # そこでDINO類似度が高い順に最大5件を順番に試し、最初に合格した
        # 実例を採用する（AI確認は費用・レート制限のため上位2件までに絞る）。
        import geom_verify
        geo_th = int(souba.get("geo_inliers", 15))
        color_hist_th = float(souba.get("color_hist_th", 0.55))
        raw_item = fingerprint.download_bytes(item["image"])
        if raw_item is None:
            _count("reason_その他_画像取得失敗")
            return None

        MAX_AI_TRIES = 2
        ai_tries = 0
        verified = None
        verified_i = None
        verified_rank = None
        saw_pattern_reject = False

        for ci in picked:
            c, d = float(sims_c[ci]), float(sims_d[ci])
            ref_text_c = (rs[ci][1] or "").lower()
            capped_c = any(p in text or p in ref_text_c for p in patterns)
            cand_rank = ("同デザイン" if (c >= strong_c and d >= strong_d and not capped_c)
                         else "似た系統")

            raw_ref = fingerprint.download_bytes(rs[ci][5]) if rs[ci][5] else None
            if raw_ref is None:
                continue

            # 幾何検証(ORB)は白黒画像で処理するため色を一切見ておらず、ステッチや
            # シルエットが似ているだけの色違い商品(黒デニム×紺デニム等)を誤って
            # 合格させることがある。そこで『色の内訳(ヒストグラム)』で色を比べ、
            # 明らかに色が違う実例はここで諦めて次の候補を試す。
            # ※実測(同商品60組/別商品270組)で、しきい値0.55なら色違いの約88%を弾き
            #   つつ同一商品は残せることを確認済み(color_check.py / 色比較テスト)。
            #   平均1色を見る旧color_distanceは差し色(白地に赤/青ロゴ)を見分けられ
            #   なかったため、分布で見るcolor_hist_distanceに置き換えた。
            color_dist = geom_verify.color_hist_distance(raw_item, raw_ref)
            if color_dist is not None and color_dist > color_hist_th:
                continue

            inl = geom_verify.inlier_count(raw_item, raw_ref)
            # GG柄等の繰り返し柄は、幾何検証も似た特徴点だらけで誤って合格しやすい
            # （geom_verify.py自身の注意書き通り）ため、幾何検証の結果は信用しない。
            if inl >= geo_th and not capped_c:
                verified = f"幾何検証OK({inl}点一致)"
                verified_i, verified_rank = ci, cand_rank
                break

            if ai_tries >= MAX_AI_TRIES:
                continue
            ai_tries += 1
            import verify_ai
            # AIは「一致度(0〜100点)＋理由」で答える（verify_ai.py）。
            # 点数の合格ラインは souba.json の『AI確認_同じと判定する点数』。
            # 理由は recon/AI_VERIFY_LOG.txt に残るので、落ちた原因を後で見返せる。
            ai = verify_ai.same_product_detail(
                item["image"], rs[ci][5],
                item.get("title", ""), rs[ci][1] or "") if verify_ai.available() else None
            v = ai["verdict"] if ai else None
            if v == "same":
                score = ai.get("score")
                verified = (f"AIが写真を見比べて確認（一致度{score}点）"
                            if score is not None else "AIが写真を見比べて確認")
                verified_i = ci
                verified_rank = "似た系統" if capped_c else cand_rank
                break
            if v == "different" and capped_c:
                saw_pattern_reject = True  # 柄物で明確に別物と判定された候補があった

        if verified is None:
            if saw_pattern_reject:
                _count("reason_類似度不足_柄物別物判定")
            else:
                _count("reason_類似度不足_未確認")
            return None  # 幾何検証にもAIにも裏付けが取れる実例が1件も無い

        _count("matched")  # 幾何検証かAIで裏付けが取れた（=返り値を返す）件数
        r0 = rs[verified_i]  # 実際に答え合わせに合格した実例（通知で根拠として見せる）
        return {
            "rank": verified_rank,
            "verified": verified,  # 二重確認に合格した方法
            "best_sim": best_d,   # 同一商品らしさ(DINO、一番似ていた候補の値)
            "clip_sim": best_c,   # 見た目の系統の近さ(CLIP、一番似ていた候補の値)
            "estimate": estimate,   # 利益判定に使った相場（設定『相場_判定に使う値』）
            "net": net,
            "profit": profit,
            # 相場の内訳（通知で『安全／標準／強気』として見せる）
            "price_safe": pm["safe"],      # 安全 = 下から25%(Q1)
            "price_mid": pm["mid"],        # 標準 = 中央値
            "price_strong": pm["strong"],  # 強気 = 下から75%(Q3)
            "count": pm["n_used"],         # 相場の根拠にした実例の件数（外れ値を除いた後）
            "count_raw": pm["n_raw"],      # 外れ値を除く前の件数
            "outliers": pm["n_outliers"],  # 外れ値として捨てた件数
            "confidence": pm["confidence"],  # 信頼度(高/中/低)。低なら最安値で判定している
            "ref_name": (r0[1] or "")[:40],
            "ref_price": r0[2],
            "ref_url": f"https://jp.mercari.com/item/{r0[0]}",
            "ref_image": r0[5],  # 実例の写真URL（通知で店の写真と並べて見せる）
        }
    except Exception as e:
        print(f"  画像照合エラー: {e}")
        return None
