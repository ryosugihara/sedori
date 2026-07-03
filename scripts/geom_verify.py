# -*- coding: utf-8 -*-
"""
幾何検証 部品（無料・Google画像検索と同じ原理の答え合わせ）

やること:
  2枚の写真から「特徴点」（プリントの角、金具、ステッチの模様など）を
  たくさん見つけ、『同じ位置関係で一致する点が何個あるか』を数える。

  同じ商品なら、細部が幾何学的にピッタリ対応するので一致点が多くなる。
  似ているだけの別商品は、細部の対応が取れないので一致点が少ない。

※ 無地や、写り方が大きく違う写真では一致点が出にくい（＝安全側に倒れる）。
※ GG柄のような繰り返し柄は誤って一致しやすいので、呼び出し側で除外すること。
"""


def _load_gray(raw, max_side=640):
    """画像データを白黒・ほどよいサイズにする（特徴点検出の前準備）"""
    import numpy as np
    import cv2
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    h, w = img.shape[:2]
    scale = max_side / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)))
    return img


def inlier_count(raw_a, raw_b):
    """2枚の画像の『幾何学的に一致する点の数』を返す（多い=同じ商品の可能性大）"""
    try:
        import numpy as np
        import cv2
        a = _load_gray(raw_a)
        b = _load_gray(raw_b)
        if a is None or b is None:
            return 0

        # 特徴点を最大1500個ずつ探す
        orb = cv2.ORB_create(nfeatures=1500)
        ka, da = orb.detectAndCompute(a, None)
        kb, db = orb.detectAndCompute(b, None)
        if da is None or db is None or len(ka) < 8 or len(kb) < 8:
            return 0

        # 似た特徴点どうしをつなぐ（あいまいなつなぎは捨てる=比率テスト）
        bf = cv2.BFMatcher(cv2.NORM_HAMMING)
        matches = bf.knnMatch(da, db, k=2)
        good = [m[0] for m in matches
                if len(m) == 2 and m[0].distance < 0.75 * m[1].distance]
        if len(good) < 8:
            return 0

        # 「全体として同じ位置関係か」を検証し、矛盾しない一致点だけ数える
        src = np.float32([ka[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kb[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
        if mask is None:
            return 0
        return int(mask.sum())
    except Exception as e:
        print(f"  幾何検証に失敗: {e}")
        return 0
