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


def download_bytes(url, timeout=20):
    """画像URLをダウンロードして生データを返す（失敗したら None）"""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.read()
    except Exception as e:
        print(f"  画像の取得に失敗 ({str(url)[:60]}): {e}")
        return None


def embed_image_url_both(url, timeout=20):
    """画像URLを1回だけダウンロードして、(CLIP指紋, DINOv2指紋) を返す。
    失敗したら (None, None)。
    """
    raw = download_bytes(url, timeout)
    if raw is None:
        return None, None
    try:
        return embed_image_bytes(raw), embed_image_bytes_dino(raw)
    except Exception as e:
        print(f"  画像の指紋化に失敗 ({str(url)[:60]}): {e}")
        return None, None


# =====================================================================
# 新方式(v2)：Googleの画像検索に近づけるための強化版
#   ・背景/ハンガー/マネキンを消して「商品だけ」を切り抜く（Google Lensと同じ発想）
#   ・見た目の系統:  CLIP ViT-B/32(2021・粗い) → SigLIP so400m(新世代・細部が見える)
#   ・同一商品判定:  DINOv2-base → DINOv2-large(大型・見分けが強い)
# 旧方式(embed_image_bytes等)はそのまま残し、DBには別の列(vec3/vec4)に保存する。
# 精度を実測して旧方式より良いと確認できてから本番の判定を切り替える。
# =====================================================================

SIGLIP_MODEL = "google/siglip-so400m-patch14-384"  # 見た目の系統(新世代)
DINO_LARGE_MODEL = "facebook/dinov2-large"          # 同一商品の見分け(大型)

_siglip = None   # (プロセッサ, モデル) を覚えておく
_dino_l = None
_rembg_session = None


def get_siglip():
    """SigLIPを読み込んで返す（初回だけ時間がかかる）"""
    global _siglip
    if _siglip is None:
        from transformers import AutoProcessor, AutoModel
        proc = AutoProcessor.from_pretrained(SIGLIP_MODEL)
        model = AutoModel.from_pretrained(SIGLIP_MODEL)
        model.eval()
        _siglip = (proc, model)
    return _siglip


def get_dino_large():
    """DINOv2-largeを読み込んで返す（初回だけ時間がかかる）"""
    global _dino_l
    if _dino_l is None:
        from transformers import AutoImageProcessor, AutoModel
        proc = AutoImageProcessor.from_pretrained(DINO_LARGE_MODEL)
        model = AutoModel.from_pretrained(DINO_LARGE_MODEL)
        model.eval()
        _dino_l = (proc, model)
    return _dino_l


def get_rembg():
    """背景除去(rembg)のセッションを用意する（初回だけモデルを読み込む）"""
    global _rembg_session
    if _rembg_session is None:
        from rembg import new_session
        _rembg_session = new_session("u2net")
    return _rembg_session


def remove_bg_and_crop(raw, margin=0.06):
    """背景を消して『商品だけ』を白背景で切り抜いたPIL画像(RGB)を返す。
    背景・ハンガー・マネキン・余白を除くことで、指紋が商品そのものに集中する
    （Google Lensが内部でやっている前処理と同じ狙い）。
    切り抜きに失敗した時は元画像をそのまま返す（判定を止めないため）。
    """
    import io
    import numpy as np
    from PIL import Image
    try:
        from rembg import remove
        img = Image.open(io.BytesIO(raw)).convert("RGBA")
        cut = remove(img, session=get_rembg())  # 透過付き(RGBA)で前景だけ残る
        alpha = np.array(cut)[:, :, 3]
        ys, xs = np.where(alpha > 20)  # 商品として残った画素の範囲
        if len(xs) == 0 or len(ys) == 0:
            return Image.open(io.BytesIO(raw)).convert("RGB")  # 全部消えたら元画像
        h, w = alpha.shape
        mx, my = int((xs.max() - xs.min()) * margin), int((ys.max() - ys.min()) * margin)
        x0, y0 = max(0, xs.min() - mx), max(0, ys.min() - my)
        x1, y1 = min(w, xs.max() + mx), min(h, ys.max() + my)
        white = Image.new("RGBA", cut.size, (255, 255, 255, 255))
        comp = Image.alpha_composite(white, cut).convert("RGB")  # 背景を白に
        return comp.crop((x0, y0, x1, y1))
    except Exception as e:
        print(f"  背景切り抜きに失敗、元画像を使用: {e}")
        try:
            return Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception:
            return None


def embed_siglip(img):
    """PIL画像(RGB)をSigLIPの指紋(float32・長さ1に正規化済み)にする"""
    import torch
    import numpy as np
    proc, model = get_siglip()
    inputs = proc(images=img, return_tensors="pt")
    with torch.no_grad():
        feats = model.get_image_features(**inputs)
    v = feats[0].numpy()
    return (v / (np.linalg.norm(v) + 1e-9)).astype("float32")


def embed_dino_large(img):
    """PIL画像(RGB)をDINOv2-largeの指紋(float32・長さ1に正規化済み)にする"""
    import torch
    import numpy as np
    proc, model = get_dino_large()
    with torch.no_grad():
        out = model(**proc(images=img, return_tensors="pt"))
    v = out.last_hidden_state[0, 0].numpy()  # 画像全体を表すCLS部分
    return (v / (np.linalg.norm(v) + 1e-9)).astype("float32")


def embed_bytes_v2(raw):
    """画像データ(バイト列)を新方式で (SigLIP指紋, DINOv2-large指紋) にする。
    背景切り抜き→2つのAIで指紋化。失敗したら (None, None)。
    """
    img = remove_bg_and_crop(raw)
    if img is None:
        return None, None
    try:
        return embed_siglip(img), embed_dino_large(img)
    except Exception as e:
        print(f"  新方式の指紋化に失敗: {e}")
        return None, None


def embed_image_url_v2(url, timeout=20):
    """画像URLを1回ダウンロードして、新方式の (SigLIP指紋, DINOv2-large指紋) を返す。
    失敗したら (None, None)。
    """
    raw = download_bytes(url, timeout)
    if raw is None:
        return None, None
    return embed_bytes_v2(raw)
