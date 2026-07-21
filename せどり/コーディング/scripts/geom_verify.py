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


def dominant_color(raw, size=32):
    """画像の代表色をLab色空間で返す（中心60%だけを見て、背景の影響を減らす）"""
    import numpy as np
    import cv2
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    img = cv2.resize(img, (size, size))
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype("float32")
    m = int(size * 0.2)
    center = lab[m:size - m, m:size - m]
    return center.reshape(-1, 3).mean(axis=0)


def color_distance(raw_a, raw_b):
    """2枚の画像の代表色の違い(Lab色空間の距離。大きいほど色が違う＝None=判定不能)。
    幾何検証(ORB)は白黒画像で特徴点を探すため色の違いを一切見ておらず、
    ステッチ等の模様が似ているだけの色違い商品を誤って合格させることがある。
    そのため色だけは別途ここで比較する。
    """
    try:
        import numpy as np
        ca = dominant_color(raw_a)
        cb = dominant_color(raw_b)
        if ca is None or cb is None:
            return None
        return float(np.linalg.norm(ca - cb))
    except Exception as e:
        print(f"  色比較に失敗: {e}")
        return None


def color_hist(raw, size=96, hbins=12, sbins=3, vbins=3, center=0.7):
    """画像を『色の内訳ヒストグラム』にする（平均1色ではなく“分布”で色を捉える）。
    HSVで 色相(H)×鮮やかさ(S)×明るさ(V) を粗いマスに分けて数える。
      ・H があるので 赤/青/緑… の違いを捉える
      ・V があるので 黒/白/グレー の違いも捉える（無彩色対策）
      ・マスを粗くしてあるので、照明や影の多少の違いには鈍感（同じ商品は同じ色と見なす）
      ・中心70%だけ見て背景の影響を減らす
    """
    import numpy as np  # noqa: F401
    import cv2
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    img = cv2.resize(img, (size, size))
    m = int(size * (1 - center) / 2)
    img = img[m:size - m, m:size - m]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, [hbins, sbins, vbins],
                        [0, 180, 0, 256, 0, 256])
    cv2.normalize(hist, hist)
    return hist.flatten().astype("float32")


def color_hist_distance(raw_a, raw_b):
    """2枚の画像の『色の内訳』の違い(0=同じ〜1=全く違う)。None=判定不能。
    平均1色を比べる color_distance より、差し色(白地に赤ロゴ/白地に青ロゴ)や
    黒/紺の違いを捉えられる。色違いの別商品を弾くための本命の指標。
    """
    try:
        import cv2
        ha = color_hist(raw_a)
        hb = color_hist(raw_b)
        if ha is None or hb is None:
            return None
        return float(cv2.compareHist(ha, hb, cv2.HISTCMP_BHATTACHARYYA))
    except Exception as e:
        print(f"  色ヒストグラム比較に失敗: {e}")
        return None


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
