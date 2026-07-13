# -*- coding: utf-8 -*-
"""
新着監視プログラム（本番）／対応サイト: KINDAL・トレファク(TREFAC FASHION)

このプログラムがすること:
  1. 見張りたいブランドの商品一覧を、各サイトから取得する
  2. 前回までに見た商品と比べて「新しく追加された商品」を見つける
  3. 新着があれば Discord に通知する（ブランド名・商品名・値段・リンク・画像つき）
  4. 「見た商品リスト」を更新して保存する（次回の比較に使う）

大事なルール:
  - 一番最初の実行では、今ある商品を全部「見た」と記録するだけで通知しません。
    （過去の在庫が一気に何百件も通知されるのを防ぐためです）
  - 2回目以降の実行で、新しく増えた商品だけを通知します。
"""

import os
import re
import json
import time
import subprocess
import urllib.request
import urllib.error
import urllib.parse

import trefac  # トレファク用の読み取り部品（同じフォルダの trefac.py）
import rinkan  # RINKAN用の読み取り部品（同じフォルダの rinkan.py）
import hardoff  # おふもーる(ハードオフ)用の読み取り部品（同じフォルダの hardoff.py）

# 画像照合（メルカリ相場DBと写真を見比べる部品）。
# AIの道具が入っていない環境でも監視が止まらないよう、失敗したら無しで動く。
try:
    import souba_match
except Exception:
    souba_match = None

# --- 設定（ここの数字や名前を変えれば動きを調整できます）-------------------
SHOP = "https://shop.kind.co.jp"      # KINDAL 通販サイトのアドレス
BRANDS_FILE = "せどり/データ/watchlists/watch_brands.json"     # KINDAL の見張るブランド一覧
STATE_FILE = "せどり/データ/state/seen.json"        # KINDAL の「見た商品」記録

TREFAC_BRANDS_FILE = "せどり/データ/watchlists/watch_trefac.json"     # トレファク の見張るブランド一覧
TREFAC_STATE_FILE = "せどり/データ/state/trefac_seen.json" # トレファク の「見た商品」記録

BRING_SHOP = "https://wastenot-official.com"  # BRING(wastenot) 通販サイト（Shopify）
BRING_BRANDS_FILE = "せどり/データ/watchlists/watch_bring.json"        # BRING の見張るブランド一覧
BRING_STATE_FILE = "せどり/データ/state/bring_seen.json"    # BRING の「見た商品」記録

RINKAN_BRANDS_FILE = "せどり/データ/watchlists/watch_rinkan.json"      # RINKAN の見張るブランド一覧
RINKAN_STATE_FILE = "せどり/データ/state/rinkan_seen.json"  # RINKAN の「見た商品」記録

HARDOFF_BRANDS_FILE = "せどり/データ/watchlists/watch_hardoff.json"      # おふもーる の見張るブランド一覧
HARDOFF_STATE_FILE = "せどり/データ/state/hardoff_seen.json"  # おふもーる の「見た商品」記録

SCAN_PROFIT_SEEN_FILE = "せどり/データ/state/scan_profit_seen.json"  # 在庫スキャンで送信済みの商品記録(重複通知防止)
SCAN_PROFIT_CHECKED_FILE = "せどり/データ/state/scan_profit_checked.json"  # 調べたが利益無しだった商品と、調べた日時
SCAN_PROFIT_REPORT_FILE = "せどり/データ/recon/SCAN_PROFIT.txt"  # 送信した商品名・金額・リンクの記録(あとで見返す用)
SCAN_PROFIT_STATS_FILE = "せどり/データ/recon/SCAN_PROFIT_STATS.txt"  # 「なぜ通知に至らなかったか」の診断レポート
RECHECK_SECONDS = 30 * 86400  # 利益無しだった商品を再チェックするまでの間隔(30日)
SOUBA_FILE = "せどり/データ/watchlists/souba.json"                    # メルカリで売れた値段の記録(利益判定に使う)
EXCLUDE_FILE = "せどり/データ/watchlists/exclude.json"                # 通知から除外する条件
SOLD_DB_FILE = "せどり/データ/data/sold/sold_items.json"   # 売却済み商品DB(画像から抽出した相場・Phase2で予測に使う)
PER_PAGE = 250                        # 1回の取得件数（Shopifyの最大値）
MAX_PAGES = 20                        # 安全のための上限（無限ループ防止）
REQUEST_WAIT = 1.5                    # サイトへの優しさ（アクセスの間に待つ秒数）

# ↓ ループ監視（短い間隔で見張り続ける）用の設定。数字は環境変数で変えられます。
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "30"))  # 何秒ごとにチェックするか
LOOP_MINUTES = int(os.environ.get("LOOP_MINUTES", "27"))  # 1回の見張りを何分続けるか

def image_match_ready():
    """②ブランドの画像判定が使える状態か。
    相場DB(souba_db.sqlite)とAIの道具が揃っている時だけ True。
    揃っていなければ②は通知せずスキップ（①は影響なし）。
    """
    return souba_match is not None and souba_match.ready()

# 本物のブラウザのふりをするための情報
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "ja,en;q=0.9",
}
# -------------------------------------------------------------------------


def http_get_json(url):
    """URL にアクセスして、プログラム用データ(JSON)を辞書として返す"""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def fetch_collection_products(handle, shop_base=SHOP):
    """あるShopify店の、あるブランド(売り場)の全商品をページめくりで取得して返す"""
    all_products = []
    for page in range(1, MAX_PAGES + 1):
        url = f"{shop_base}/collections/{handle}/products.json?limit={PER_PAGE}&page={page}"
        try:
            data = http_get_json(url)
        except Exception as e:
            print(f"  取得失敗 ({handle} page{page}): {e}")
            break
        products = data.get("products", [])
        if not products:
            break  # これ以上商品がなければ終わり
        all_products.extend(products)
        if len(products) < PER_PAGE:
            break  # これが最後のページ
        time.sleep(REQUEST_WAIT)  # サイトに優しく、少し待つ
    return all_products


def yen(price_str):
    """'5060' のような文字列を '¥5,060' の見た目に整える"""
    try:
        return "¥{:,}".format(int(float(price_str)))
    except Exception:
        return f"¥{price_str}"


def build_item(product, brand_name, shop_base=SHOP, shop_label="KINDAL"):
    """Shopify の商品データから、通知に使う情報だけを取り出す"""
    variants = product.get("variants") or [{}]
    images = product.get("images") or []
    raw_price = variants[0].get("price", "")
    try:
        price_num = int(float(raw_price))  # 計算用の数値
    except Exception:
        price_num = None
    return {
        "id": product.get("id"),
        "brand": brand_name,
        "title": product.get("title", "(名前なし)"),
        "price": yen(raw_price),
        "price_num": price_num,
        "url": f"{shop_base}/products/{product.get('handle')}",
        "image": images[0].get("src") if images else None,
        "shop": shop_label,
        "category": product.get("product_type", ""),  # 商品の分類（財布/バッグ等）
    }


def kindal_items(brand):
    """KINDAL の1ブランドの全商品を、通知用の形(idつき)で返す"""
    products = fetch_collection_products(brand["collection"], SHOP)
    return [build_item(p, brand["name"], SHOP, "KINDAL") for p in products]


def bring_items(brand):
    """BRING(wastenot) の1ブランドの全商品を、通知用の形(idつき)で返す"""
    products = fetch_collection_products(brand["collection"], BRING_SHOP)
    return [build_item(p, brand["name"], BRING_SHOP, "BRING") for p in products]


def load_json_file(path, default):
    """ファイルがあれば読み込む。無ければ default を返す"""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json_file(path, data):
    """データをファイルに保存する（フォルダが無ければ作る）"""
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def git_save_state():
    """state(見た記録)をすぐサーバーに保存(commit&push)する。
    こうすると、別の実行が『古い記録』を見て同じ商品を二度通知するのを防げる。
    GitHub上で動いている時だけ実行する（ローカルやデモでは何もしない）。
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    branch = os.environ.get("GITHUB_REF_NAME", "")
    try:
        subprocess.run(["git", "config", "user.name", "github-actions"], check=False)
        subprocess.run(
            ["git", "config", "user.email", "github-actions@users.noreply.github.com"],
            check=False,
        )
        subprocess.run(["git", "add", "state"], check=False)
        r = subprocess.run(
            ["git", "commit", "-m", "新着監視: 状態を更新 [skip ci]"],
            capture_output=True,
        )
        if r.returncode != 0:
            return  # 変更が無ければ何もしない
        if branch:
            subprocess.run(["git", "pull", "--rebase", "origin", branch], check=False)
        subprocess.run(["git", "push"], capture_output=True)
        print("  状態をサーバーに保存しました")
    except Exception as e:
        print(f"  状態の保存(push)に失敗: {e}")


def discord_post(payload):
    """Discord に1メッセージ送る（共通部分）"""
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        print("DISCORD_WEBHOOK_URL が未設定。通知をスキップします。")
        return
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=data,
        headers={
            "Content-Type": "application/json",
            # ↓ これが無いと Discord に 403 で拒否される（名乗りが必要）
            "User-Agent": "sedori-bot/1.0 (+https://github.com/ryosugihara/sedori)",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as e:
        # 失敗の理由（文字数オーバー等）をログに残す
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        print(f"  Discord通知に失敗: {e} {body}")
    except Exception as e:
        print(f"  Discord通知に失敗: {e}")


def send_text(message):
    """ただの文章を Discord に送る"""
    discord_post({"content": message[:1900]})


# ===== ここから「メルカリ相場・利益見込み」関連 =====================

def load_souba():
    """相場メモ(souba.json)を読み込んで、設定と記録をまとめて返す"""
    data = load_json_file(SOUBA_FILE, {})
    s = data.get("設定", {})
    return {
        "fee": s.get("メルカリ手数料率", 0.10),       # メルカリ手数料(10%)
        "shipping": s.get("想定送料", 800),            # 送料の想定
        "high": s.get("利益見込み_高ライン_円", 20000),
        "mid": s.get("利益見込み_中ライン_円", 3000),
        "notify_line": s.get("利益通知ライン_円", 2000),  # ②ブランドはこの利益以上だけ通知
        # 画像判定の合格ライン（精度測定で決めた値：違う商品の誤合格0%のライン）
        "strong_th": s.get("画像一致_同デザイン", 0.92),        # 同デザイン: CLIP
        "strong_dino": s.get("画像一致_同デザイン_DINO", 0.90),  # 同デザイン: DINO
        "cand_th": s.get("画像一致_似た系統", 0.88),            # 似た系統: CLIP
        "cand_dino": s.get("画像一致_似た系統_DINO", 0.80),      # 似た系統: DINO
        # 画像判定の対象外・照合条件（無地は判定しない／属性語は名前の一致が必要）
        "plain_cats": s.get("画像判定_無地カテゴリ", []),
        "feature_words": s.get("画像判定_特徴語", []),
        "attr_words": s.get("画像判定_属性キーワード", []),
        # GG柄等の「どれも同じに見える柄」は同デザイン断定しない
        "plain_patterns": s.get("画像判定_断定しない柄", []),
        # 同デザイン断定に必要な幾何検証の一致点数（答え合わせの合格ライン）
        "geo_inliers": s.get("画像判定_幾何一致点数", 15),
        # 色の違い(Lab色空間の距離)がこれを超えたら別物として除外する
        "color_distance_th": s.get("画像判定_色距離", 30),
        "records": data.get("records", []),
    }


# ===== ここから「売却済みDBによる相場予測(Phase 2)」=================
# メルカリで売れた実例(data/sold/sold_items.json)を使って、
# 新着商品の「予想相場」と「予想利益」をざっくり計算します。

# 商品の種類を見分けるための言葉（上から順に当てはめる＝先にある方が優先）。
# 例:「デニムジャケット」は先に『ジャケット』に当たるので“デニム”ではなく“ジャケット”。
CATEGORY_RULES = [
    ("バッグ", ["バッグ", "バック", "かばん", "カバン", "bag", "ポーチ"]),
    ("帽子", ["ニット帽", "ビーニー", "beanie", "キャップ", "cap", "ハット", "帽子"]),
    ("ジャケット", ["ジャケット", "jacket", "ライダース", "riders", "ブルゾン",
                   "コート", "coat", "アウター", "ダウン", "down", "ダッフル",
                   "n2b", "モッズ", "mods"]),
    ("パーカー", ["パーカー", "hoodie", "フーディ", "スウェット", "sweat"]),
    ("ニット", ["ニット", "knit", "セーター", "sweater", "カーディガン",
               "cardigan", "モヘア"]),
    ("ロングスリーブ", ["ロングスリーブ", "longsleeve", "ロンt", "長袖"]),
    ("Tシャツ", ["tシャツ", "ティーシャツ", "t-shirt", "tee", "カットソー", "半袖"]),
    ("シャツ", ["シャツ", "shirt", "ブラウス", "ポロ", "polo"]),
    ("スカーフ", ["スカーフ", "scarf", "ストール", "マフラー", "muffler"]),
    ("ベルト", ["ベルト", "belt"]),
    ("ブレスレット", ["ブレス", "bracelet", "リストバンド", "wristband",
                     "バングル", "bangle"]),
    ("ネックレス", ["ネックレス", "necklace", "ペンダント"]),
    ("デニム", ["デニム", "denim", "jeans", "ジーンズ", "スキニー", "skinny"]),
    ("パンツ", ["パンツ", "pants", "スラックス", "トラウザー", "チノ",
               "ショーツ", "shorts"]),
]

_sold_cache = None  # 売却済みDBを1度だけ読んで覚えておく入れ物


def load_sold_db():
    """売却済み商品DBを読み込む（2回目以降は覚えた中身を返す）"""
    global _sold_cache
    if _sold_cache is None:
        data = load_json_file(SOLD_DB_FILE, {"items": []})
        _sold_cache = data.get("items", [])
    return _sold_cache


def norm_brand(name):
    """ブランド名を比較しやすい形にする（小文字＋英数字だけ）。
    例: 'NUMBER (N)INE' → 'numbernine' / 'beauty:beast' → 'beautybeast'
    """
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def categorize(text):
    """商品名や種類の文字から、商品の種類(デニム/Tシャツ等)を1つ見つける"""
    t = (text or "").lower()
    for cat, kws in CATEGORY_RULES:
        for kw in kws:
            if kw.lower() in t:
                return cat
    return None


# 「特徴にならない一般的な言葉」。これらは“同じ商品”の手がかりにしない。
# （例:「Tシャツ」「デニム」「graphic」だけ一致しても同じ商品とは言えない）
WEAK_TOKENS = {
    # 英語・ローマ字の一般語（種類・素材・形・状態など）
    "graphic", "message", "jeans", "denim", "leather", "hoodie", "jacket",
    "pants", "shirt", "knit", "sweater", "down", "riders", "polo", "mini",
    "bag", "tee", "tshirt", "band", "belt", "scarf", "beanie", "longsleeve",
    "bracelet", "wristband", "chain", "jean", "size", "made",
    "wool", "cotton", "nylon", "rayon", "mohair", "military", "vintage",
    "shoulder", "tote", "body", "zip", "zipper", "studs", "stud",
    # 日本語の一般語
    "デニム", "ジャケット", "パーカー", "シャツ", "ニット", "セーター",
    "パンツ", "バッグ", "ジーンズ", "スキニー", "ブレスレット", "ベルト",
    "スカーフ", "ダウン", "ライダース", "ニット帽", "フーディー",
    "グラフィック", "レザー", "ショルダー", "トート", "ハンド", "ボディ",
    "ウール", "コットン", "ナイロン", "レーヨン", "モヘア", "ミリタリー",
    "ストール", "マフラー", "ヴィンテージ", "ビンテージ",
}


def signature_tokens(record):
    """1つの売却実例から『その商品ならではの特徴語(=指紋)』を取り出す。
    keywords(英語) と model(型番名) から、一般語・種類の言葉を除いた手がかりだけを集める。
    例: AW07 翼 Tシャツ → {'tsubasa','wing'} / SS06 KILL YOUR IDOLS → {'kill','idols',...}
    """
    def is_generic(tok):
        # 一般語、または種類を表すだけの言葉(Tシャツ/デニム等)は手がかりにしない
        return tok in WEAK_TOKENS or categorize(tok) is not None

    toks = set()
    # keywords（curているローマ字の特徴語）
    for k in record.get("keywords", []):
        k = (k or "").strip().lower()
        if len(k) >= 4 and not is_generic(k):
            toks.add(k)
    # model（型番名）を空白で区切って1語ずつ見る
    for chunk in re.split(r"\s+", record.get("model", "")):
        c = chunk.strip().lower()
        if not c or is_generic(c):
            continue
        # 数字を含む語(季節記号ss2002・年代1990s・サイズ等)は手がかりにしない
        if any(ch.isdigit() for ch in c):
            continue
        if re.fullmatch(r"[a-z][a-z\-]*", c):
            if len(c) >= 4:          # 英単語は4文字以上だけ
                toks.add(c)
        elif len(c) >= 2:            # 日本語の語は2文字以上
            toks.add(c)
    return toks


def predict_profit(item, souba):
    """売却済みDBと照らして『予想相場・予想利益』を計算する。
    送ってもらった写真と“同じ商品”と判断できた時だけ予測する。
    （同じブランドで、その商品ならではの特徴語が題名に入っていること）
    判断できなければ None を返す（＝通知しない／意味のない予測はしない）。
    """
    buy = item.get("price_num")
    if not buy:
        return None  # 仕入値が分からなければ予測しない

    bn = norm_brand(item.get("brand", ""))
    title = (item.get("title", "") + " " + item.get("category", "")).lower()

    db = load_sold_db()
    # 同じブランドの実例ごとに「特徴語が題名にいくつ入っているか」を数える
    scored = []
    for r in db:
        if norm_brand(r.get("brand", "")) != bn:
            continue
        toks = signature_tokens(r)
        hits = [t for t in toks if t in title]
        if hits:
            scored.append((len(hits), r))
    if not scored:
        return None  # 同じ商品が見つからない＝予測しない

    # 一番多く特徴語が一致した実例（＝同じ商品の可能性が高い）に絞る
    best = max(n for n, _ in scored)
    same = [r for n, r in scored if n == best]
    prices = sorted(r.get("sold_price", 0) for r in same)
    sell = prices[len(prices) // 2]            # 中央値（極端な値に振られにくい）
    rep = same[0]                              # 代表（どの商品と判定したか表示用）

    fee, ship = souba["fee"], souba["shipping"]
    net = int(sell * (1 - fee) - ship)  # メルカリ手取り
    profit = net - buy                  # 予想利益
    return {
        "sell": sell,
        "net": net,
        "profit": profit,
        "count": len(same),
        "model": rep.get("model", ""),     # 同型と判定した商品名
        "brand": rep.get("brand", ""),
    }


def prediction_lines(item, souba):
    """予測結果(予想相場・予想利益)を通知カード用の文章にする。予測が無ければ空。"""
    pred = item.get("prediction")
    if not pred:
        return ""
    profit = pred["profit"]
    if profit >= souba["high"]:
        mark = "🟢 高"
    elif profit >= souba["mid"]:
        mark = "🟡 中"
    else:
        mark = "🔴 低"
    return (
        f"🎯 同型と判定: {pred['model']}（売却実例 ¥{pred['sell']:,} / {pred['count']}件）\n"
        f"💰 予想利益 約¥{profit:,}（利益見込み: {mark}）"
    )

# ===== ここまで(Phase 2) ============================================


def extract_item_name(title):
    """商品名から、検索に使いやすい部分を取り出す（「」の中があればそれを使う）"""
    m = re.search(r"「([^」]+)」", title)
    return m.group(1) if m else title


def mercari_search_url(item):
    """その商品の『メルカリ売り切れ相場』を開くリンクを作る"""
    keyword = f"{item.get('brand', '')} {extract_item_name(item.get('title', ''))}".strip()
    return (
        "https://jp.mercari.com/search?keyword="
        + urllib.parse.quote(keyword)
        + "&status=sold_out&order=desc&sort=created_time"
    )


def match_souba(item, records):
    """新着商品が、相場メモのどれかに一致するか探す（keywordsが全部、商品名に入っていれば一致）"""
    title = item.get("title", "")
    brand = (item.get("brand", "") + " " + title).lower()
    best, best_n = None, -1
    for r in records:
        # ブランド指定があれば、それも一致条件にする
        if r.get("brand") and r["brand"].lower() not in brand:
            continue
        kws = r.get("keywords", [])
        if kws and all(k in title for k in kws):
            if len(kws) > best_n:  # より具体的な(言葉数が多い)記録を優先
                best, best_n = r, len(kws)
    return best


def profit_lines(item, souba):
    """通知カードに足す『利益判定』の文章を作って返す"""
    buy = item.get("price_num")
    fee, ship = souba["fee"], souba["shipping"]
    lines = []

    # 1) 損益分岐ライン（相場メモが無くても必ず出せる）
    if buy:
        breakeven = int((buy + ship) / (1 - fee))
        lines.append(
            f"📈 メルカリで ¥{breakeven:,} 以上で利益"
            f"（手数料{int(fee * 100)}%+送料¥{ship}想定）"
        )

    # 2) 画像照合の結果（メルカリ相場DBの写真と見比べた判定）があれば出す
    m = item.get("img_match")
    if m:
        mark = "🟢" if m["rank"] == "同デザイン" else "🟡"
        lines.append(
            f"🖼️ {mark} {m['rank']}の売却実例あり"
            f"（同一度{m['best_sim']:.2f}/系統{m.get('clip_sim', 0):.2f}・{m['count']}件）"
        )
        if m.get("verified"):
            lines.append(f"✅ 二重確認済み: {m['verified']}")
        lines.append(f"🎯 予想相場 ¥{m['estimate']:,} → 手取り ¥{m['net']:,}")
        if m["profit"] is not None:
            lines.append(f"💰 予想利益 約¥{m['profit']:,}")
        lines.append(
            f"⬇️ 下の大きい写真＝メルカリで売れた実例 ¥{m['ref_price']:,}\n"
            f"　[{m['ref_name']}]({m['ref_url']})"
        )

    # 3) 相場メモに一致したら、利益見込み(高/中/低)を出す
    rec = match_souba(item, souba["records"])
    if rec and buy:
        sell = rec["mercari_price"]
        net = int(sell * (1 - fee) - ship)   # メルカリ手取り
        profit = net - buy                   # 利益
        if profit >= souba["high"]:
            mark = "🟢 高"
        elif profit >= souba["mid"]:
            mark = "🟡 中"
        else:
            mark = "🔴 低"
        lines.append(
            f"💹 相場¥{sell:,} → 手取り¥{net:,} / 利益 約¥{profit:,}（利益見込み: {mark}）"
        )

    # 4) ワンタップの相場リンク（常に付ける）
    lines.append(f"🔍 [メルカリ相場を見る]({mercari_search_url(item)})")
    return "\n".join(lines)

# ===== ここまで =====================================================


def send_items(items):
    """新着商品を Discord に通知する（見やすいカード形式・4件ずつ）"""
    souba = load_souba()  # 相場メモを読み込む（利益判定に使う）
    # Discordは1メッセージの文字量に上限(6000字)があるため、
    # カードを詰め込みすぎると丸ごと失敗する。4件ずつなら安全。
    for i in range(0, len(items), 4):
        chunk = items[i:i + 4]
        embeds = []
        for it in chunk:
            # お店の名前があれば一緒に表示する（KINDAL / トレファク）
            shop = it.get("shop", "")
            shop_line = f"🏪 {shop}\n" if shop else ""
            embed = {
                "title": it["title"][:250],
                "url": it["url"],
                "description": (
                    f"{shop_line}🏷️ {it['brand']}\n💴 仕入 {it['price']}\n"
                    + profit_lines(it, souba)
                ),
            }
            if it.get("image"):
                # 右上の小さい写真 ＝ お店の商品
                embed["thumbnail"] = {"url": it["image"]}
            m = it.get("img_match")
            if m and m.get("ref_image"):
                # 下の大きい写真 ＝ メルカリで売れた実例（見比べ用）
                embed["image"] = {"url": m["ref_image"]}
            embeds.append(embed)
        discord_post({"content": f"🆕 新着 {len(chunk)} 件", "embeds": embeds})
        print(f"  Discordに {len(chunk)} 件通知しました")
        time.sleep(1)  # 連続で送りすぎない


def load_excludes():
    """通知から除外する条件(exclude.json)を読み込む"""
    return load_json_file(
        EXCLUDE_FILE, {"ng_keywords": [], "price_rules": []}
    )


def is_excluded(item, excludes, brand=None):
    """この商品が『通知しない』条件に当てはまるか判定する。
    brand を渡すと、ブランド別のNGキーワード/カテゴリ許可リストも見る。
    """
    # 商品名と分類の両方をまとめて、小文字でチェックする
    text = (item.get("title", "") + " " + item.get("category", "")).lower()
    price = item.get("price_num") or 0
    brand = brand or item.get("brand")

    # 1) NGキーワード（含まれていたら除外）
    for kw in excludes.get("ng_keywords", []):
        if kw.lower() in text:
            return True

    # 1b) ブランド別NGキーワード（例: サンローランはテーラードジャケット除外）
    if brand:
        for kw in excludes.get("brand_ng_keywords", {}).get(brand, []):
            if kw.lower() in text:
                return True

    # 2) 価格条件（キーワードに合致 かつ price_min 以上 なら除外）
    for rule in excludes.get("price_rules", []):
        pmin = rule.get("price_min", 0)
        kws = rule.get("keywords", [])
        if price >= pmin and any(k.lower() in text for k in kws):
            return True

    # 3) ブランド別カテゴリ許可リスト（例: サンローランのバッグは
    #    リュック/バックパック以外は通知しない）
    if brand:
        allow_rule = excludes.get("brand_category_allowlist", {}).get(brand)
        if allow_rule:
            is_category = any(k.lower() in text for k in allow_rule.get("category_keywords", []))
            is_allowed = any(k.lower() in text for k in allow_rule.get("allow_keywords", []))
            if is_category and not is_allowed:
                return True

    return False


def matches_only(item, only_keywords):
    """ブランドに『この言葉を含む物だけ通知』指定がある場合の判定。
    指定が無ければ常にTrue（全部通知）。例: Undercoverは『デニム』だけ。
    """
    if not only_keywords:
        return True
    text = (item.get("title", "") + " " + item.get("category", "")).lower()
    return any(k.lower() in text for k in only_keywords)


def check_source(brands, seen, first_run, get_items, do_slow=True):
    """1つのサイトの全ブランドを1回チェックして、新着リストを返す。

    get_items(brand) … そのブランドの商品一覧（id付きの辞書リスト）を返す関数。
    KINDAL でもトレファクでも、この共通処理で扱える。

    do_slow … False のときは「利益が出る時だけ通知」する②ブランド
              (profit_only) をスキップする。①の優先ブランド(サンローラン等)を
              短い間隔で見張りつつ、②は少し長い間隔で見張るための仕組み。
    """
    new_items = []
    excludes = load_excludes()  # 通知しない条件を読み込む
    souba = load_souba()        # 利益予測の設定(手数料・送料・通知ライン)
    for b in brands:
        # ②ブランド(profit_only)は、画像判定の準備(相場DB+AI)が無い時はスキップ。
        # （文字だけだと別デザインを誤通知してしまうため。①ブランドは影響なし）
        if b.get("profit_only") and not image_match_ready():
            continue
        # ②ブランドは、ゆっくり巡回の時だけチェックする（①の速度を守るため）
        if b.get("profit_only") and not do_slow:
            continue
        # 識別子：KINDALは collection、トレファクは keyword を使う
        key = b.get("collection") or b.get("keyword")
        try:
            items = get_items(b)
        except Exception as e:
            print(f"  取得失敗 ({key}): {e}")
            continue

        # このブランドを今までに記録したことがあるか（新規追加ブランドの判定）
        known = key in seen
        # IDは文字列にそろえて比較する（サイトによって数値/文字が混在するため）
        current_ids = [str(it["id"]) for it in items if it.get("id") is not None]
        seen_ids = set(str(x) for x in seen.get(key, []))
        # 「前に見ていない＝新着」の商品を抜き出す
        fresh = [it for it in items if str(it.get("id")) not in seen_ids]

        # 全体の初回、または新しく追加したブランドは、記録だけして通知しない
        if first_run or not known:
            print(f"  初回/新規: {key} を {len(current_ids)} 件記録（通知なし）")
        elif fresh:
            only_kw = b.get("only_keywords")  # 例: Undercoverは「デニム」だけ
            profit_only = b.get("profit_only")  # 例: ②ブランドは利益が出る時だけ
            keep = []
            for it in fresh:
                # まず共通の除外（財布/サングラス等）と only_keywords を確認
                if is_excluded(it, excludes):
                    continue
                if not matches_only(it, only_kw):
                    continue
                # 商品写真をメルカリ相場DBと見比べる（画像の指紋で照合）
                match = None
                if image_match_ready() and it.get("image"):
                    match = souba_match.match_item(it, souba)
                if match:
                    it["img_match"] = match  # 通知カードに表示するため覚えておく
                if profit_only:
                    # ②ブランドは『ほぼ同デザインの売却実例があり、
                    # 予想利益が通知ライン(2000円)以上』の時だけ通知する
                    if (not match
                            or match["rank"] != "同デザイン"
                            or match["profit"] is None
                            or match["profit"] < souba["notify_line"]):
                        continue
                keep.append(it)
            skipped = len(fresh) - len(keep)
            new_items.extend(keep)
            print(f"  新着 {len(fresh)} 件: {key}（通知 {len(keep)} / 対象外 {skipped}）")

        # 見た商品リストを更新（今ある商品IDを全部覚える）
        seen[key] = sorted(seen_ids | set(current_ids))
        time.sleep(REQUEST_WAIT)
    return new_items


def write_scan_diagnostics(path, total_fetched, examined, stats, sent_count):
    """『なぜ通知に至らなかったか』の内訳をファイルに書き出す（診断用）。
    stats は souba_match.match_item(..., stats=stats) で集計した辞書。
    """
    similar_found = stats.get("similar_found", 0)
    sim90 = stats.get("sim_90plus", 0)
    profit_neg = stats.get("profit_negative", 0)
    profit_low = stats.get("profit_low", 0)
    profit_ok = stats.get("profit_ok", 0)

    reason_low_sim = sum(v for k, v in stats.items() if k.startswith("reason_類似度不足"))
    reason_no_data = stats.get("reason_相場データ不足", 0)
    reason_low_profit = profit_neg + profit_low
    reason_other = (sum(v for k, v in stats.items() if k.startswith("reason_その他"))
                     + max(0, total_fetched - examined))

    ranking = sorted(
        [("類似度不足", reason_low_sim), ("相場データ不足", reason_no_data),
         ("利益不足", reason_low_profit), ("その他", reason_other)],
        key=lambda x: -x[1],
    )

    lines = [
        "📊 スキャン診断レポート",
        f"1. 監視した総商品数: {total_fetched} 件（うち画像照合まで進んだ: {examined} 件）",
        f"2. 類似商品が見つかった件数: {similar_found} 件",
        f"3. 類似度(DINO)90%以上だった件数: {sim90} 件",
        f"4. 利益がマイナスだった件数: {profit_neg} 件",
        f"5. 利益0〜通知ライン未満だった件数: {profit_low} 件",
        f"6. 利益が通知ライン以上だった件数: {profit_ok} 件（送信: {sent_count} 件）",
        "7. 通知対象にならなかった理由ランキング:",
    ]
    for i, (name, n) in enumerate(ranking, start=1):
        lines.append(f"   {i}位 {name}: {n} 件")

    report = "\n".join(lines)
    os.makedirs("せどり/データ/recon", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    return report


def scan_profitable():
    """監視中の全ブランドの『今ある在庫』を全部しらべて、利益が出そうな物を通知する。
    ①(優先ブランド)か②(profit_only)かは関係なく、今の出品の中から
    予想利益が通知ライン以上の商品を探す。
    （1回限りの『棚卸しスキャン』。新着監視のstate＝見た記録 はいじらないが、
    このスキャン自身の送信済み記録 SCAN_PROFIT_SEEN_FILE で
    同じ商品を毎回重複して通知しないようにする）
    """
    if not image_match_ready():
        send_text("🔎 在庫スキャン中止：画像判定の準備（相場DBかAI）が揃っていません。")
        return
    souba = load_souba()
    excludes = load_excludes()
    MAX_HITS = int(os.environ.get("SCAN_MAX", "30"))  # 通知しすぎ防止の上限
    notified_before = set(load_json_file(SCAN_PROFIT_SEEN_FILE, []))
    checked_before = load_json_file(SCAN_PROFIT_CHECKED_FILE, {})  # {key: 最後に調べた時刻}
    now_ts = time.time()
    checked_now = {}  # 今回あらたに「利益無し」と分かった物（末尾でchecked_beforeへ合流）
    stats = {}  # 診断レポート用の集計（match_item内部で加算される）
    examined = 0  # is_excluded等を通過し、実際に画像照合まで進んだ件数
    total_fetched = 0  # 各サイトから取得した商品の総数（除外・重複含む）

    # 監視している5サイト・全ブランドを対象にする（①②の区別なし）
    sources = [
        (load_json_file(BRANDS_FILE, {"brands": []}).get("brands", []),
         kindal_items, "KINDAL"),
        (load_json_file(TREFAC_BRANDS_FILE, {"brands": []}).get("brands", []),
         trefac.fetch_brand_items, "トレファク"),
        (load_json_file(BRING_BRANDS_FILE, {"brands": []}).get("brands", []),
         bring_items, "BRING"),
        (load_json_file(RINKAN_BRANDS_FILE, {"brands": []}).get("brands", []),
         rinkan.fetch_brand_items, "RINKAN"),
        (load_json_file(HARDOFF_BRANDS_FILE, {"brands": []}).get("brands", []),
         hardoff.fetch_brand_items, "オフモール"),
    ]

    # 全ブランドを調べ終えてからまとめて送信すると、5サイト×全ブランドの
    # 走査に数十分〜数時間かかるため、見つけた頃には売り切れてしまう。
    # そのため『見つかり次第すぐ送信』する方式にする（利益順には並べられない）。
    sent_count = 0
    report_lines = []
    seen_keys = set()  # 同じ商品を二重に拾わないための目印
    for brands, get_items, site in sources:
        if sent_count >= MAX_HITS:
            break
        for b in brands:
            if sent_count >= MAX_HITS:
                break
            try:
                items = get_items(b)
            except Exception as e:
                print(f"  取得失敗 ({site}/{b.get('name')}): {e}")
                continue
            for it in items:
                total_fetched += 1
                if sent_count >= MAX_HITS:
                    break
                if is_excluded(it, excludes):
                    continue
                dedup = (it.get("shop"), str(it.get("id")))
                if dedup in seen_keys:
                    continue
                seen_keys.add(dedup)
                key = f"{dedup[0]}:{dedup[1]}"
                if key in notified_before:
                    continue  # 前回までのスキャンで既に送信済み
                last_checked = checked_before.get(key)
                if last_checked and now_ts - last_checked < RECHECK_SECONDS:
                    continue  # 30日以内に調べて利益無しだった商品はスキップ
                # 商品写真をメルカリ相場DBと見比べる（画像の指紋で照合）
                examined += 1
                match = None
                if it.get("image"):
                    match = souba_match.match_item(it, souba, stats=stats)
                if (not match or match["rank"] != "同デザイン"
                        or match["profit"] is None
                        or match["profit"] < souba["notify_line"]):
                    checked_now[key] = now_ts  # 今回調べて利益無しだった
                    continue
                # 見つけたその場ですぐ送信し、記録も即保存する
                # （まだ売り切れていないうちに知らせるため。途中で止まっても
                # 　ここまでの送信・チェック記録は残る）
                it["img_match"] = match
                send_text(f"🔎 利益が出そうな商品をみつけました（予想利益 約¥{match['profit']:,}）")
                send_items([it])
                notified_before.add(key)
                save_json_file(SCAN_PROFIT_SEEN_FILE, sorted(notified_before))
                report_lines.append(
                    f"- [{it.get('shop')}] {it['title'][:40]} 仕入{it['price']} "
                    f"利益¥{match['profit']:,} {it['url']}"
                )
                sent_count += 1
            print(f"  {site}/{b.get('name')}: ここまで送信 {sent_count} 件")
            time.sleep(REQUEST_WAIT)

    print(f"利益が出そうで送信した在庫: {sent_count} 件")

    # 「調べたが利益無しだった」記録は、通知の有無に関わらず必ず保存する
    checked_before.update(checked_now)
    save_json_file(SCAN_PROFIT_CHECKED_FILE, checked_before)

    # 商品名・金額・リンクをファイルにも残す（Discordの通知履歴を遡らなくても
    # あとで見返せるように）
    os.makedirs("せどり/データ/recon", exist_ok=True)
    with open(SCAN_PROFIT_REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(f"在庫スキャン結果  {sent_count}件\n" + "\n".join(report_lines))

    diag = write_scan_diagnostics(SCAN_PROFIT_STATS_FILE, total_fetched, examined, stats, sent_count)

    if sent_count == 0:
        send_text("🔎 在庫スキャン完了：今は利益が出そうな商品は見つかりませんでした。")
        send_text(diag)
    else:
        send_text(f"🔎 在庫スキャン完了：利益が出そうな商品を {sent_count} 件みつけて送信しました。")


def main():
    # 環境変数 SCAN_PROFIT=1 のときは「在庫の棚卸しスキャン」を1回だけ実行して終わる
    if os.environ.get("SCAN_PROFIT") == "1":
        print("在庫スキャン（②ブランドの利益候補さがし）を開始します")
        scan_profitable()
        print("在庫スキャン 完了")
        return

    # 見張るブランド一覧を読み込む（KINDAL・トレファク・BRING）
    kindal_brands = load_json_file(BRANDS_FILE, {"brands": []}).get("brands", [])
    trefac_brands = load_json_file(TREFAC_BRANDS_FILE, {"brands": []}).get("brands", [])
    bring_brands = load_json_file(BRING_BRANDS_FILE, {"brands": []}).get("brands", [])
    rinkan_brands = load_json_file(RINKAN_BRANDS_FILE, {"brands": []}).get("brands", [])
    hardoff_brands = load_json_file(HARDOFF_BRANDS_FILE, {"brands": []}).get("brands", [])

    # これまでに「見た商品」の記録を読み込む（サイトごとに別ファイル）
    kindal_seen = load_json_file(STATE_FILE, {})
    trefac_seen = load_json_file(TREFAC_STATE_FILE, {})
    bring_seen = load_json_file(BRING_STATE_FILE, {})
    rinkan_seen = load_json_file(RINKAN_STATE_FILE, {})
    hardoff_seen = load_json_file(HARDOFF_STATE_FILE, {})
    kindal_first = (len(kindal_seen) == 0)  # 記録が空っぽなら初回
    trefac_first = (len(trefac_seen) == 0)
    bring_first = (len(bring_seen) == 0)
    rinkan_first = (len(rinkan_seen) == 0)
    hardoff_first = (len(hardoff_seen) == 0)

    def one_pass(k_first, t_first, b_first, r_first, h_first, do_slow=True):
        """全サイトを1回ずつチェックして、新着をまとめて返す。
        do_slow=False のときは②ブランド(利益が出る時だけ通知)を省いて軽く回す。
        """
        new = []
        new += check_source(kindal_brands, kindal_seen, k_first, kindal_items, do_slow)
        new += check_source(trefac_brands, trefac_seen, t_first, trefac.fetch_brand_items, do_slow)
        new += check_source(bring_brands, bring_seen, b_first, bring_items, do_slow)
        new += check_source(rinkan_brands, rinkan_seen, r_first, rinkan.fetch_brand_items, do_slow)
        new += check_source(hardoff_brands, hardoff_seen, h_first, hardoff.fetch_brand_items, do_slow)
        return new

    def save_all():
        save_json_file(STATE_FILE, kindal_seen)
        save_json_file(TREFAC_STATE_FILE, trefac_seen)
        save_json_file(BRING_STATE_FILE, bring_seen)
        save_json_file(RINKAN_STATE_FILE, rinkan_seen)
        save_json_file(HARDOFF_STATE_FILE, hardoff_seen)

    def start_message():
        all_brands = (
            kindal_brands + trefac_brands + bring_brands + rinkan_brands + hardoff_brands
        )
        names = sorted({b["name"] for b in all_brands})
        send_text(
            "✅ 新着監視を開始/更新しました！\n"
            "監視中の店: KINDAL、トレファク、BRING、RINKAN、オフモール\n"
            f"対象ブランド: {'、'.join(names)}\n"
            f"これから約{POLL_SECONDS}秒ごとに新着をチェックします。"
        )

    # 環境変数 LOOP_MODE=1 のときは「ループ監視」、それ以外は「1回だけ」
    loop_mode = (os.environ.get("LOOP_MODE") == "1")

    # --- 1回だけチェックするモード（手動の動作確認用）---
    if not loop_mode:
        new_items = one_pass(kindal_first, trefac_first, bring_first, rinkan_first, hardoff_first)
        if kindal_first or trefac_first or bring_first or rinkan_first or hardoff_first:
            start_message()
        if new_items:
            send_items(new_items)
        save_all()
        print("完了")
        return

    # --- ループ監視モード（短い間隔で見張り続ける）---
    # ②ブランド(利益が出る時だけ通知)は SLOW_SECONDS ごとにだけチェックする。
    # こうすると①の優先ブランド(サンローラン等)の見張りが遅くならない。
    SLOW_SECONDS = int(os.environ.get("SLOW_SECONDS", "300"))  # ②は何秒ごとに見るか
    print(f"ループ監視開始: {POLL_SECONDS}秒ごと / ②ブランドは{SLOW_SECONDS}秒ごと / 最長 {LOOP_MINUTES}分")
    end_time = time.time() + LOOP_MINUTES * 60

    # まず最初の1回チェック（②ブランドも含めて全部見て記録する）
    new_items = one_pass(kindal_first, trefac_first, bring_first, rinkan_first, hardoff_first, do_slow=True)
    if kindal_first or trefac_first or bring_first or rinkan_first or hardoff_first:
        start_message()
    if new_items:
        send_items(new_items)
    save_all()
    git_save_state()  # 記録をすぐサーバーに保存（重複通知を防ぐ）
    last_slow = time.time()  # ②ブランドを最後に見た時刻

    # 決めた時間内は、くり返しチェックし続ける（2回目以降は初回扱いしない）
    while time.time() < end_time:
        time.sleep(POLL_SECONDS)
        # ②ブランドを見るタイミングか判定（SLOW_SECONDS 経過したら見る）
        do_slow = (time.time() - last_slow) >= SLOW_SECONDS
        if do_slow:
            last_slow = time.time()
        items = one_pass(False, False, False, False, False, do_slow=do_slow)
        if items:
            print(f"新着 {len(items)} 件 → 通知")
            send_items(items)
            save_all()
            git_save_state()  # 通知したら即サーバー保存（重複通知を防ぐ）

    # 最後に記録を保存して終了（次の見張りが続きから始められる）
    save_all()
    git_save_state()
    print("ループ監視 終了")


if __name__ == "__main__":
    main()
