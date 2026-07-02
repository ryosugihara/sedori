# -*- coding: utf-8 -*-
"""
画像の「指紋」化 部品（CLIPという無料公開AIを使う）

指紋（ベクトル）とは:
  画像を512個の数字の並びに変えたもの。指紋どうしの近さ＝見た目の近さ。
  これで「新着商品の写真」と「メルカリで売れた商品の写真」を比べられる。

※AI(torch等)が入っていない環境で import しても壊れないよう、
  重い読み込みは実際に使う瞬間まで行わない。
"""

import io
import urllib.request

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
}

_model = None  # 一度読み込んだAIを覚えておく（毎回読むと遅いため）


def get_model():
    """CLIPを読み込んで返す（初回だけ時間がかかる。実測12秒ほど）"""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("clip-ViT-B-32")
    return _model


def embed_image_bytes(raw):
    """画像データ(バイト列)を指紋(float32・長さ1に正規化済み)にする"""
    import numpy as np  # noqa: F401（torchと一緒に入る）
    from PIL import Image
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    vec = get_model().encode([img], normalize_embeddings=True)[0]
    return vec.astype("float32")


def embed_image_url(url, timeout=20):
    """画像URLをダウンロードして指紋にする（失敗したら None）"""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read()
        return embed_image_bytes(raw)
    except Exception as e:
        print(f"  画像の指紋化に失敗 ({str(url)[:60]}): {e}")
        return None


def embed_text(text):
    """文章を指紋にする（画像と同じ土俵で比べられるのがCLIPの強み）"""
    vec = get_model().encode([text], normalize_embeddings=True)[0]
    return vec.astype("float32")


# ===== ここから DINOv2（同一商品の見分けが得意なAI）====================
# CLIPは「同じ系統の服」までしか分からないことが実運用で判明したため、
# 「同じ商品そのものか」を見るのが得意な DINOv2 を二重チェックとして使う。

_dino = None  # (前処理, モデル) を覚えておく


def get_dino():
    """DINOv2 を読み込んで返す（初回だけ時間がかかる）"""
    global _dino
    if _dino is None:
        from transformers import AutoImageProcessor, AutoModel
        proc = AutoImageProcessor.from_pretrained("facebook/dinov2-base")
        model = AutoModel.from_pretrained("facebook/dinov2-base")
        model.eval()
        _dino = (proc, model)
    return _dino


def embed_image_bytes_dino(raw):
    """画像データをDINOv2の指紋(float32・長さ1に正規化済み)にする"""
    import torch
    import numpy as np
    from PIL import Image
    proc, model = get_dino()
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    with torch.no_grad():
        out = model(**proc(images=img, return_tensors="pt"))
    vec = out.last_hidden_state[0, 0].numpy()  # 画像全体を表す部分(CLS)を使う
    vec = vec / (np.linalg.norm(vec) + 1e-9)
    return vec.astype("float32")


def embed_image_url_both(url, timeout=20):
    """画像URLを1回だけダウンロードして、(CLIP指紋, DINOv2指紋) を返す。
    失敗したら (None, None)。
    """
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read()
        return embed_image_bytes(raw), embed_image_bytes_dino(raw)
    except Exception as e:
        print(f"  画像の指紋化に失敗 ({str(url)[:60]}): {e}")
        return None, None
