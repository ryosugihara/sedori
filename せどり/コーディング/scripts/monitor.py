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
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.parse

import trefac  # トレファク用の読み取り部品（同じフォルダの trefac.py）
import rinkan  # RINKAN用の読み取り部品（同じフォルダの rinkan.py）
import hardoff  # おふもーる(ハードオフ)用の読み取り部品（同じフォルダの hardoff.py）
import takayama  # 高山質店オンライン用の読み取り部品（同じフォルダの takayama.py）
import okoku  # 買取王国オンライン用の読み取り部品（同じフォルダの okoku.py）
import allu  # ALLUオンライン用の読み取り部品（同じフォルダの allu.py）
import ragtag  # RAGTAGオンライン(Yahoo!ショッピング公式店)用の読み取り部品（ragtag.py）

# 画像照合（メルカリ相場DBと写真を見比べる部品）。
# AIの道具が入っていない環境でも監視が止まらないよう、失敗したら無しで動く。
try:
    import souba_match
except Exception:
    souba_match = None

# 調査の優先順位（服中心・派手デザイン優先・季節先取り）。
# 無くても監視は動く（その場合は今まで通りの順番）。
try:
    import priority
except Exception:
    priority = None

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

TAKAYAMA_BRANDS_FILE = "せどり/データ/watchlists/watch_takayama.json"  # 高山質店 の見張るブランド一覧
TAKAYAMA_STATE_FILE = "せどり/データ/state/takayama_seen.json"  # 高山質店 の「見た商品」記録

OKOKU_BRANDS_FILE = "せどり/データ/watchlists/watch_okoku.json"  # 買取王国 の見張るブランド一覧
OKOKU_STATE_FILE = "せどり/データ/state/okoku_seen.json"  # 買取王国 の「見た商品」記録

ALLU_BRANDS_FILE = "せどり/データ/watchlists/watch_allu.json"  # ALLU の見張るブランド一覧
ALLU_STATE_FILE = "せどり/データ/state/allu_seen.json"  # ALLU の「見た商品」記録
RAGTAG_BRANDS_FILE = "せどり/データ/watchlists/watch_ragtag.json"  # RAGTAG の見張るブランド一覧
RAGTAG_STATE_FILE = "せどり/データ/state/ragtag_seen.json"  # RAGTAG の「見た商品」記録

# 実際の監視の速さの記録（設定どおりの間隔で見られているかを確かめるため。30分ごとに更新）
SPEED_FILE = "せどり/データ/state/monitor_speed.json"
SCAN_PROFIT_SEEN_FILE = "せどり/データ/state/scan_profit_seen.json"  # 在庫スキャンで送信済みの商品記録(重複通知防止)
SCAN_PROFIT_REPORT_FILE = "せどり/データ/recon/SCAN_PROFIT.txt"  # 送信した商品名・金額・リンクの記録(あとで見返す用)
SCAN_PROFIT_STATS_FILE = "せどり/データ/recon/SCAN_PROFIT_STATS.txt"  # 「なぜ通知に至らなかったか」の診断レポート
SOUBA_FILE = "せどり/データ/watchlists/souba.json"                    # メルカリで売れた値段の記録(利益判定に使う)
EXCLUDE_FILE = "せどり/データ/watchlists/exclude.json"                # 通知から除外する条件
PER_PAGE = 250                        # 1回の取得件数（Shopifyの最大値）
MAX_PAGES = 20                        # 安全のための上限（無限ループ防止）
# KINDALの新着チェックで、各売り場の先頭から何件を見るか（新着は先頭に並ぶ。
# 数秒ごとに見るので少なくて足りる。250件全部を毎回読むより、お店への負担も軽い）
KINDAL_NEWEST_LIMIT = int(os.environ.get("KINDAL_NEWEST_LIMIT", "50"))
# KINDALは専用の見張り役(スレッド)で、他の店と同時に動かす。共有する記録の読み書き・
# 画像照合(AI)がぶつからないようにする鍵
STATE_LOCK = threading.RLock()   # 見た記録の更新・保存・サーバー保存
MATCH_LOCK = threading.Lock()    # 画像照合（AIの部品は1つずつ使う）
_fast_sent_total = [0]           # 速報で送った件数（保存のきっかけに使う）
# KINDALの新着の写真照合をする裏の係（1人）。見張り役は照合を待たずに次のチェックへ戻る
_match_pool = ThreadPoolExecutor(max_workers=1)


def _bg_decide(decide, it):
    """裏の係の仕事: 照合して、速報済みならカードを書き換え、まだなら利益が出る時だけ送る"""
    try:
        if decide(it):
            send_items([it])
    except Exception as e:
        print(f"  照合（裏の係）でエラー: {e}")
REQUEST_WAIT = 1.0                    # サイトへの優しさ（アクセスの間に待つ秒数）

# ↓ ループ監視（短い間隔で見張り続ける）用の設定。数字は環境変数で変えられます。
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "15"))  # 何秒ごとにチェックするか
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
    """あるShopify店の、あるブランド(売り場)の全商品をページめくりで取得して返す。
    途中で読み込みに失敗したら、エラーにする（呼び出し側はそのブランドを今回は飛ばす）。
    以前は失敗しても『読めた所まで(0件のことも)』を返していたが、新しく追加したブランドで
    これが起きると『0件』と記録され、次に読めた時に今ある商品が全部『新着』として
    一度に通知されてしまうため（2026-11-26 修正）。
    """
    all_products = []
    for page in range(1, MAX_PAGES + 1):
        url = f"{shop_base}/collections/{handle}/products.json?limit={PER_PAGE}&page={page}"
        try:
            data = http_get_json(url)
        except Exception as e:
            print(f"  取得失敗 ({handle} page{page}): {e}")
            raise
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
        "vendor": product.get("vendor", ""),  # お店のブランド表記（例: BALMAIN / PIERRE BALMAIN）
    }


def kindal_items(brand):
    """KINDAL の1ブランドの全商品を、通知用の形(idつき)で返す"""
    products = fetch_collection_products(brand["collection"], SHOP)
    return [build_item(p, brand["name"], SHOP, "KINDAL") for p in products]


def kindal_items_newest(brand):
    """KINDAL の1ブランドの『1ページ目(250件)』だけを返す（高速チェック用）。
    KINDALの売り場は新しい商品ほど先頭に並ぶため、新着は1ページ目に入る
    （実測 2026-11-25: 1ページ目に直近1〜1.5か月分）。1回0.3〜0.4秒で取れるので
    10秒ごとに見られる。取りこぼし防止に、全ページの確認も10分ごとに別途行う。
    """
    url = (f"{SHOP}/collections/{brand['collection']}/products.json"
           f"?limit={KINDAL_NEWEST_LIMIT}&page=1")
    data = http_get_json(url)
    return [build_item(p, brand["name"], SHOP, "KINDAL") for p in data.get("products", [])]


def bring_items(brand):
    """BRING(wastenot) の1ブランドの全商品を、通知用の形(idつき)で返す。
    first_page_only=true のブランドは1ページ目(新しい順の250件)だけを読む
    （Dior売り場のように1,800件近くある売り場を30秒ごとに全ページ読むと、お店への負担が大きいため。
      実測 2026-11-25: 1ページ目に直近2週間分が入る）"""
    if brand.get("first_page_only"):
        data = http_get_json(f"{BRING_SHOP}/collections/{brand['collection']}/products.json"
                             f"?limit={PER_PAGE}&page=1")
        products = data.get("products", [])
    else:
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
        # 実行中に書き換わったログ類(recon)は、ここではコミットしない。
        # コミットしない変更が残っていると後の pull --rebase が失敗し、
        # 記録を保存できず「同じ商品を再通知し続ける」原因になるため捨てる
        # （ログは毎日のスキャン系ワークフローが自分の分を保存する）。
        subprocess.run(["git", "checkout", "--", "せどり/データ/recon"],
                       capture_output=True)
        # ※以前はここが「git add state」という存在しないパスで、コミットが
        #   毎回失敗→即時保存が一度も動いていなかった（再通知の主原因）。
        subprocess.run(["git", "add", "せどり/データ/state"], check=False)
        r = subprocess.run(
            ["git", "commit", "-m", "新着監視: 状態を更新 [skip ci]"],
            capture_output=True,
        )
        if r.returncode != 0:
            return  # 変更が無ければ何もしない
        # 他のワークフローが先に保存していても衝突しないよう、取り込んでから押す。
        # 失敗したら少し待ってもう一度（--autostash は未保存の変更があっても動く）
        for attempt in (1, 2, 3):
            if branch:
                subprocess.run(["git", "pull", "--rebase", "--autostash",
                                "origin", branch], capture_output=True)
            p = subprocess.run(["git", "push"], capture_output=True)
            if p.returncode == 0:
                print("  状態をサーバーに保存しました")
                return
            time.sleep(5)
        print("  状態の保存(push)に3回失敗しました（次の保存で再試行されます）")
    except Exception as e:
        print(f"  状態の保存(push)に失敗: {e}")


def discord_post(payload, want_id=False, edit_id=None):
    """Discord に1メッセージ送る（共通部分）。
    want_id=True で、送ったメッセージの番号を返す（あとで書き換えるため）。
    edit_id を渡すと、新しく送らずにそのメッセージを書き換える。
    """
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        print("DISCORD_WEBHOOK_URL が未設定。通知をスキップします。")
        return None
    data = json.dumps(payload).encode("utf-8")
    url, method = webhook, "POST"
    if edit_id:
        url, method = f"{webhook.split('?')[0]}/messages/{edit_id}", "PATCH"
    elif want_id:
        url = webhook + ("&" if "?" in webhook else "?") + "wait=true"
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            # ↓ これが無いと Discord に 403 で拒否される（名乗りが必要）
            "User-Agent": "sedori-bot/1.0 (+https://github.com/ryosugihara/sedori)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            if want_id and not edit_id:
                try:
                    return json.loads(res.read().decode("utf-8")).get("id") or None
                except Exception:
                    return None
            return True
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
        # 新しい目（SigLIP＋DINOv2-large・背景切り抜き）で照合するか、とその合格ライン
        "eye_v2": s.get("画像照合_新しい目を使う", False),
        "v2_strong_s": s.get("新しい目_同デザイン_SigLIP", 0.85),
        "v2_strong_d": s.get("新しい目_同デザイン_DINO", 0.85),
        "v2_cand_s": s.get("新しい目_似た系統_SigLIP", 0.85),
        "v2_cand_d": s.get("新しい目_似た系統_DINO", 0.75),
        # サイズを考えて相場を出すか（同じサイズ区分で売れた実例だけで相場を出す）
        "size_aware": s.get("相場_サイズを考える", True),
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
        # 相場モデル（souba_match.price_model）の設定
        "price_pool": s.get("相場_参照する実例の上限件数", 30),  # 値段の根拠に使う実例の最大数
        "price_min_count": s.get("相場_最低件数", 5),          # これ未満なら信頼度『低』＝最安値で判定
        "price_good_count": s.get("相場_十分件数", 10),        # これ以上なら信頼度『高』
        "price_basis": s.get("相場_判定に使う値", "標準"),     # 利益判定に使う値: 安全/標準/強気
        "records": data.get("records", []),
    }


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
        # 相場の内訳（安全/標準/強気）と根拠の件数・信頼度。
        # 出品価格を決めるのは人なので、控えめな値だけでなく強気の値も見せる。
        if m.get("price_mid") is not None:
            conf = m.get("confidence", "")
            conf_mark = {"高": "🟢", "中": "🟡", "低": "🔴"}.get(conf, "")
            outl = f"・外れ値{m['outliers']}件除外" if m.get("outliers") else ""
            lines.append(
                f"📊 相場 安全¥{m['price_safe']:,} / 標準¥{m['price_mid']:,} / "
                f"強気¥{m['price_strong']:,}"
                f"（根拠{m['count']}件{outl}・信頼度{conf_mark}{conf}）"
            )
        if m.get("size_note"):
            st = f"サイズ {m['size']}・" if m.get("size") else ""
            lines.append(f"📏 {st}{m['size_note']}")
        lines.append(f"🎯 判定相場 ¥{m['estimate']:,} → 手取り ¥{m['net']:,}")
        if m["profit"] is not None:
            lines.append(f"💰 予想利益 約¥{m['profit']:,}")
            # 安全側で見た時の利益も添える（強気でしか利益が出ない商品を見分けるため）
            if m.get("price_safe") is not None and item.get("price_num"):
                fee, ship = souba["fee"], souba["shipping"]
                safe_profit = int(m["price_safe"] * (1 - fee) - ship) - item["price_num"]
                if safe_profit < m["profit"]:
                    lines.append(f"　（安全相場で見ると 約¥{safe_profit:,}）")
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
        embeds = [build_embed(it, souba) for it in chunk]
        discord_post({"content": f"🆕 新着 {len(chunk)} 件", "embeds": embeds})
        print(f"  Discordに {len(chunk)} 件通知しました")
        time.sleep(1)  # 連続で送りすぎない


def build_embed(it, souba, pending=False):
    """通知カード1枚分を作る。pending=True は『速報（相場はこれから調べる）』"""
    # お店の名前があれば一緒に表示する（KINDAL / トレファク）
    shop = it.get("shop", "")
    shop_line = f"🏪 {shop}\n" if shop else ""
    tail = ("⏳ 相場を照合中…（数十秒でこのカードに追記されます）" if pending
            else profit_lines(it, souba))
    if it.get("_note"):
        tail = it["_note"] + "\n" + tail
    embed = {
        "title": it["title"][:250],
        "url": it["url"],
        "description": f"{shop_line}🏷️ {it['brand']}\n💴 仕入 {it['price']}\n" + tail,
    }
    if it.get("image"):
        # 右上の小さい写真 ＝ お店の商品
        embed["thumbnail"] = {"url": it["image"]}
    m = it.get("img_match")
    if m and m.get("ref_image"):
        # 下の大きい写真 ＝ メルカリで売れた実例（見比べ用）
        embed["image"] = {"url": m["ref_image"]}
    return embed


def send_now(it, souba):
    """見つけた瞬間に1件だけ速報で送る（相場の照合を待たない）。メッセージ番号を返す"""
    mid = discord_post({"content": "🆕 新着（速報）", "embeds": [build_embed(it, souba, pending=True)]},
                       want_id=True)
    if mid:
        print(f"  速報: {it.get('shop')} {it.get('title', '')[:40]}")
    return mid


def update_sent(mid, it, souba):
    """速報で送ったカードを、相場の照合結果つきに書き換える"""
    if mid is True:  # 番号が取れなかった時は書き換えられない
        return
    discord_post({"content": "🆕 新着", "embeds": [build_embed(it, souba)]}, edit_id=mid)


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

    # 0) 永久ブロックリスト（誤送信された特定の商品を二度と送らない）。
    #    URL完全一致、または末尾のメルカリ商品ID(mXXXX)一致で止める。
    url = (item.get("url") or "")
    item_id = str(item.get("id") or "")
    for b in excludes.get("block_urls", []):
        b = (b or "").strip()
        if not b:
            continue
        if b == url or (item_id and b.rstrip("/").endswith(item_id)) or (url and b in url):
            return True

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


def matches_skip(item, skip_keywords):
    """ブランドに『この言葉を含む物は通知しない』指定がある場合の判定。
    例: Balmain は別ブランドの PIERRE BALMAIN（ピエールバルマン）を除く。
    商品名・分類・お店のブランド表記(vendor) のどれかに含まれていたら True。
    """
    if not skip_keywords:
        return False
    text = " ".join([item.get("title", ""), item.get("category", ""),
                     item.get("vendor", "")]).lower()
    return any(k.lower() in text for k in skip_keywords)


def check_source(brands, seen, first_run, get_items, do_slow=True, wait=None, fast_notify=False):
    """1つのサイトの全ブランドを1回チェックして、新着リストを返す。

    wait … ブランドを1つ見るごとに待つ秒数（同じサイトへの連続アクセスの間隔）。
            省略時は REQUEST_WAIT。ループ監視で『1ブランドずつの小さな仕事』として
            呼ぶ時は、仕事の間隔そのものがサイトへの間隔になるので短くしてよい。

    get_items(brand) … そのブランドの商品一覧（id付きの辞書リスト）を返す関数。
    KINDAL でもトレファクでも、この共通処理で扱える。

    fast_notify … True（KINDAL）のときは、文字の条件を通った新着を『相場の照合より先に』
              すぐ速報で送り、照合が終わったらそのカードに利益の見込みを書き足す
              （画像照合に数秒〜数十秒かかり、その間に売り切れるのを防ぐため。2026-11-28 依頼）。
              単調な服（派手な言葉が無い物）は、利益が出るか見てから送る（今まで通り）。

    do_slow … False のときは「利益が出る時だけ通知」する②ブランド
              (profit_only) をスキップする。①の優先ブランド(サンローラン等)を
              短い間隔で見張りつつ、②は少し長い間隔で見張るための仕組み。
    """
    new_items = []
    skipped_by_priority = {}  # 服以外・シンプル服として調査しなかった件数（理由ごと）
    excludes = load_excludes()  # 通知しない条件を読み込む
    souba = load_souba()        # 利益予測の設定(手数料・送料・通知ライン)
    for b in brands:
        # ②ブランド(profit_only)は、画像判定の準備(相場DB+AI)が無い時はスキップ。
        # （文字だけだと別デザインを誤通知してしまうため。①ブランドは影響なし）
        if b.get("profit_only") and not image_match_ready():
            continue
        # ②ブランドは、ゆっくり巡回の時だけチェックする（①の速度を守るため）。
        # ただし fast=true のブランド(バレンシアガ等、売れるのが速い高額品)は、
        # 利益判定は行いつつ毎回チェックする（5分待つと売り切れて間に合わないため）。
        if b.get("profit_only") and not do_slow and not b.get("fast"):
            continue
        # 識別子：KINDALは collection、トレファクは keyword を使う
        # seen_key があればそれを『見た記録』の名前に使う。長く止めていたブランドを戻す時に
        # 新しい名前にすると『新規ブランド』扱いになり、初回は記録だけで通知しない
        # （古い記録のまま戻すと、止めていた間の入荷が全部『新着』として一度に届いてしまう）
        key = b.get("seen_key") or b.get("collection") or b.get("keyword")
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
            # 速報：文字の条件だけで通知してよい物は、照合の前に全部すぐ送る
            fast_sent = {}  # 商品ID → Discordのメッセージ番号
            if fast_notify and not profit_only:
                for it in fresh:
                    if (is_excluded(it, excludes) or not matches_only(it, only_kw)
                            or matches_skip(it, b.get("skip_keywords"))):
                        continue
                    if priority is not None:
                        why = priority.skip_reason(it.get("title", ""), it.get("category", ""))
                        if why == "シンプル服" and priority.notify_simple_new():
                            why = None
                        if why:
                            continue
                        if priority.only_distinctive() and not priority.is_flashy(
                                f"{it.get('title', '')} {it.get('category', '')}"):
                            continue  # 単調な服は、利益が出るか見てから（下の通常の流れ）
                    mid = send_now(it, souba)
                    if mid:
                        fast_sent[str(it.get("id"))] = mid
                        _fast_sent_total[0] += 1
            def decide(it, b=b, profit_only=profit_only, fast_sent=fast_sent,
                       skipped_by_priority=skipped_by_priority):
                """写真の照合をして、通知するか(True)を決める。速報済みの物はカードを書き換える"""
                # 商品写真をメルカリ相場DBと見比べる（画像の指紋で照合）。
                # ②ブランドは利益ライン以上の時だけ通知するので、利益ライン未満は
                # 重い答え合わせを省いて高速化する（①ブランドは表示用に常に照合）。
                match = None
                if image_match_ready() and it.get("image"):
                    mp = souba["notify_line"] if profit_only else None
                    with MATCH_LOCK:
                        match = souba_match.match_item(it, souba, min_profit=mp)
                fid = fast_sent.get(str(it.get("id")))
                if fid:
                    # 速報で送り済み → 照合の結果をそのカードに書き足す
                    if match:
                        it["img_match"] = match
                    if it.get("_priority_skip"):
                        it["_note"] = f"⚠️ 写真では『{it['_priority_skip']}』の可能性"
                    update_sent(fid, it, souba)
                    return False
                # 写真を見て『服以外』と判定された商品は、相場照合の結果に関わらず
                # 通知しない（①優先ブランドは相場が見つからなくても通知するため、
                # ここで止めないとバッグ等がすり抜ける）
                if it.get("_priority_skip"):
                    why = it["_priority_skip"]
                    skipped_by_priority[why] = skipped_by_priority.get(why, 0) + 1
                    return False
                if match:
                    it["img_match"] = match  # 通知カードに表示するため覚えておく
                if profit_only:
                    # ②ブランドは『ほぼ同デザインの売却実例があり、
                    # 予想利益が通知ライン(2000円)以上』の時だけ通知する
                    if (not match
                            or match["rank"] != "同デザイン"
                            or match["profit"] is None
                            or match["profit"] < souba["notify_line"]):
                        return False
                elif priority is not None and priority.only_distinctive():
                    # ①優先ブランドは今まで新着を全部通知していたが、単調な服が
                    # 多く混ざるため『派手な言葉があるか、利益が出る物』だけに絞る。
                    # 商品名が雑でも利益が出るなら通知されるので、狙い目は逃さない。
                    # （priority.json の『優先ブランド_派手か利益が出る物だけ通知する』
                    #   を false にすると、今まで通り全部通知する）
                    flashy = priority.is_flashy(
                        f"{it.get('title', '')} {it.get('category', '')}")
                    profitable = (match and match.get("profit") is not None
                                  and match["profit"] >= souba["notify_line"])
                    if not flashy and not profitable:
                        skipped_by_priority["単調(優先ブランド)"] = (
                            skipped_by_priority.get("単調(優先ブランド)", 0) + 1)
                        return False
                return True

            queued = 0
            for it in fresh:
                # まず共通の除外（財布/サングラス等）と only_keywords を確認
                if is_excluded(it, excludes):
                    continue
                if not matches_only(it, only_kw):
                    continue
                if matches_skip(it, b.get("skip_keywords")):
                    continue
                # 服以外（バッグ・靴・小物）・シンプル服は通知しない。
                # ここは①優先ブランドの新着も通る道なので、この絞り込みが
                # 効いていないと服以外の通知が出続ける（watchlists/priority.json）。
                if priority is not None:
                    why = priority.skip_reason(it.get("title", ""),
                                               it.get("category", ""))
                    # 新着通知ではシンプル服も通す設定なら、シンプル服は止めない
                    # （2026-11 依頼: 全部の店でシンプルな服も通知する。服以外は止めたまま）
                    if why == "シンプル服" and priority.notify_simple_new():
                        why = None
                    if why:
                        skipped_by_priority[why] = skipped_by_priority.get(why, 0) + 1
                        continue
                if fast_notify:
                    # 照合（数秒〜数十秒）は裏の係に任せ、KINDALの見張りはすぐ次のチェックへ戻る
                    _match_pool.submit(_bg_decide, decide, it)
                    queued += 1
                    continue
                if decide(it):
                    keep.append(it)
            skipped = len(fresh) - len(keep) - queued
            new_items.extend(keep)
            pr = ("・服以外/シンプル " + "、".join(f"{k}{v}" for k, v in skipped_by_priority.items())
                  if skipped_by_priority else "")
            fs = f"・速報 {len(fast_sent)}・照合へ {queued}" if fast_notify else ""
            print(f"  新着 {len(fresh)} 件: {key}（通知 {len(keep)}{fs} / 対象外 {skipped}{pr}）")

        # 見た商品リストを更新（今ある商品IDを全部覚える）
        with STATE_LOCK:
            seen[key] = sorted(seen_ids | set(current_ids))
        time.sleep(REQUEST_WAIT if wait is None else wait)
    return new_items


def write_scan_diagnostics(path, total_fetched, examined, stats, sent_count):
    """『なぜ通知に至らなかったか』の内訳をファイルに書き出す（診断用）。
    stats は souba_match.match_item(..., stats=stats) で集計した辞書。
    """
    # 利益ライン以上なのに最終確認で落とした候補を、写真URL・理由つきで残す（人が見直す用）
    try:
        dropped_path = path.replace("_STATS.txt", "_DROPPED.json")
        with open(dropped_path, "w", encoding="utf-8") as f:
            json.dump(souba_match.DROPPED, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"  落とした候補の記録に失敗: {e}")
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
    # 相場の根拠件数が足りていたか（信頼度の内訳）。『低』が多いなら相場DBの
    # 収集キーワードを増やすか、相場_最低件数を見直す目安になる
    conf_h = stats.get("confidence_高", 0)
    conf_m = stats.get("confidence_中", 0)
    conf_l = stats.get("confidence_低", 0)
    if conf_h + conf_m + conf_l:
        lines.append(
            f"8. 相場の信頼度（根拠の実例件数）: 高 {conf_h} 件 / 中 {conf_m} 件 / "
            f"低 {conf_l} 件（低＝最安値で控えめに判定）"
        )
    # どのブランドの売却実例が足りないか（相場DBのキーワードを足す目安になる）
    low_brands = sorted(
        [(k.replace("低信頼ブランド_", ""), v) for k, v in stats.items()
         if k.startswith("低信頼ブランド_")],
        key=lambda x: -x[1],
    )[:10]
    if low_brands:
        lines.append("9. 根拠不足（信頼度低）が多いブランド（watch_mercari.json のキーワード追加の目安）:")
        for name, n in low_brands:
            lines.append(f"   ・{name}: {n} 件")
    # 「似た実例は見つかったのに、答え合わせ（幾何検証・色比較・AI確認）で
    # 裏付けが取れず落ちた」件数。利益ライン以上なのに送信されない主因を追う用
    unverified = stats.get("reason_類似度不足_未確認", 0)
    pattern_rej = stats.get("reason_類似度不足_柄物別物判定", 0)
    if unverified or pattern_rej:
        lines.append(
            f"10. 答え合わせで裏付けが取れず落ちた件数: {unverified} 件"
            f"（ほかに柄物でAIが別物と判定: {pattern_rej} 件）"
            "→ 内訳は recon/AI_VERIFY_LOG.txt の判定理由を参照"
        )
    # 最終確認（色比較・幾何検証・AI）で何が起きたかの内訳。
    # 「利益ライン以上なのに送信0件」の原因が、色違いで捨てたのか・AIが失敗して
    # いたのか（回数制限など）・AIが別物と判定したのかを切り分けるための項目
    finals = sorted(
        [(k.replace("final_", ""), v) for k, v in stats.items()
         if k.startswith("final_")],
        key=lambda x: -x[1],
    )
    if finals:
        lines.append("11. 最終確認（色比較・幾何検証・AI）の内訳（候補ごとの回数）:")
        for name, n in finals:
            lines.append(f"   ・{name}: {n} 回")
    if any(k.startswith("size_") for k in stats):
        lines.append(
            "12. サイズを考えた相場（利益が出そうな候補のみ）: "
            f"同じサイズの実例で計算 {stats.get('size_同サイズで計算', 0)} 件 / "
            f"同じサイズの実例なし(最安値で計算) {stats.get('size_同サイズの実例なし', 0)} 件 / "
            f"サイズ不明 {stats.get('size_不明', 0)} 件 → "
            f"サイズを考えたら利益不足で落ちた {stats.get('reason_利益不足_サイズ考慮で下がった', 0)} 件"
        )

    report ="\n".join(lines)
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
    if priority is not None:
        season, prev_season = priority.current_season()
        print(f"優先順位: {season}物を優先（{prev_season}物は後回し）・服中心・派手デザイン優先")
    MAX_HITS = int(os.environ.get("SCAN_MAX", "30"))  # 通知しすぎ防止の上限
    notified_before = set(load_json_file(SCAN_PROFIT_SEEN_FILE, []))
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
        (load_json_file(TAKAYAMA_BRANDS_FILE, {"brands": []}).get("brands", []),
         takayama.fetch_brand_items, "高山質店"),
        (load_json_file(OKOKU_BRANDS_FILE, {"brands": []}).get("brands", []),
         okoku.fetch_brand_items, "買取王国"),
        (load_json_file(ALLU_BRANDS_FILE, {"brands": []}).get("brands", []),
         allu.fetch_brand_items, "ALLU"),
        (load_json_file(RAGTAG_BRANDS_FILE, {"brands": []}).get("brands", []),
         ragtag.fetch_brand_items, "RAGTAG"),
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
            # 優先度の高い商品（服・派手デザイン・今の季節）から先に照合する。
            # 1回の通知上限(MAX_HITS)やAIの回数制限を優先度の高い商品に使うため。
            # 通知する条件そのものは変えない（watchlists/priority.json で調整）。
            if priority is not None:
                items = priority.sort_by_score(
                    items, key=lambda x: f"{x.get('title', '')} {x.get('category', '')}")
            for it in items:
                total_fetched += 1
                if sent_count >= MAX_HITS:
                    break
                if is_excluded(it, excludes):
                    continue
                # 服以外・シンプル服は調査しない（服のせどりに集中するため。
                # 3つのスイッチは watchlists/priority.json で切り替えられる）
                why = (priority.skip_reason(it.get("title", ""), it.get("category", ""))
                       if priority is not None else None)
                if why:
                    stats[f"reason_その他_{why}"] = stats.get(f"reason_その他_{why}", 0) + 1
                    continue
                dedup = (it.get("shop"), str(it.get("id")))
                if dedup in seen_keys:
                    continue
                seen_keys.add(dedup)
                key = f"{dedup[0]}:{dedup[1]}"
                if key in notified_before:
                    continue  # 前回までのスキャンで既に送信済み
                # 商品写真をメルカリ相場DBと見比べる（画像の指紋で照合）
                examined += 1
                match = None
                if it.get("image"):
                    match = souba_match.match_item(it, souba, stats=stats,
                                                   min_profit=souba["notify_line"])
                if (not match or match["rank"] != "同デザイン"
                        or match["profit"] is None
                        or match["profit"] < souba["notify_line"]):
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
    takayama_brands = load_json_file(TAKAYAMA_BRANDS_FILE, {"brands": []}).get("brands", [])
    okoku_brands = load_json_file(OKOKU_BRANDS_FILE, {"brands": []}).get("brands", [])
    allu_brands = load_json_file(ALLU_BRANDS_FILE, {"brands": []}).get("brands", [])
    ragtag_brands = load_json_file(RAGTAG_BRANDS_FILE, {"brands": []}).get("brands", [])

    # これまでに「見た商品」の記録を読み込む（サイトごとに別ファイル）
    kindal_seen = load_json_file(STATE_FILE, {})
    trefac_seen = load_json_file(TREFAC_STATE_FILE, {})
    bring_seen = load_json_file(BRING_STATE_FILE, {})
    rinkan_seen = load_json_file(RINKAN_STATE_FILE, {})
    hardoff_seen = load_json_file(HARDOFF_STATE_FILE, {})
    takayama_seen = load_json_file(TAKAYAMA_STATE_FILE, {})
    okoku_seen = load_json_file(OKOKU_STATE_FILE, {})
    allu_seen = load_json_file(ALLU_STATE_FILE, {})
    ragtag_seen = load_json_file(RAGTAG_STATE_FILE, {})
    kindal_first = (len(kindal_seen) == 0)  # 記録が空っぽなら初回
    trefac_first = (len(trefac_seen) == 0)
    bring_first = (len(bring_seen) == 0)
    rinkan_first = (len(rinkan_seen) == 0)
    hardoff_first = (len(hardoff_seen) == 0)
    takayama_first = (len(takayama_seen) == 0)
    okoku_first = (len(okoku_seen) == 0)
    allu_first = (len(allu_seen) == 0)
    ragtag_first = (len(ragtag_seen) == 0)

    def one_pass(k_first, t_first, b_first, r_first, h_first, do_slow=True, ta_first=False,
                 ok_first=False, al_first=False, rt_first=False):
        """全サイトを1回ずつチェックして、新着をまとめて返す。
        do_slow=False のときは②ブランド(利益が出る時だけ通知)を省いて軽く回す。
        """
        new = []
        new += check_source(kindal_brands, kindal_seen, k_first, kindal_items, do_slow)
        new += check_source(trefac_brands, trefac_seen, t_first, trefac.fetch_brand_items, do_slow)
        new += check_source(bring_brands, bring_seen, b_first, bring_items, do_slow)
        new += check_source(rinkan_brands, rinkan_seen, r_first, rinkan.fetch_brand_items, do_slow)
        new += check_source(hardoff_brands, hardoff_seen, h_first, hardoff.fetch_brand_items, do_slow)
        new += check_source(takayama_brands, takayama_seen, ta_first, takayama.fetch_brand_items, do_slow)
        new += check_source(okoku_brands, okoku_seen, ok_first, okoku.fetch_brand_items, do_slow)
        new += check_source(allu_brands, allu_seen, al_first, allu.fetch_brand_items, do_slow)
        new += check_source(ragtag_brands, ragtag_seen, rt_first, ragtag.fetch_brand_items, do_slow)
        return new

    def save_all():
        save_json_file(STATE_FILE, kindal_seen)
        save_json_file(TREFAC_STATE_FILE, trefac_seen)
        save_json_file(BRING_STATE_FILE, bring_seen)
        save_json_file(RINKAN_STATE_FILE, rinkan_seen)
        save_json_file(HARDOFF_STATE_FILE, hardoff_seen)
        save_json_file(TAKAYAMA_STATE_FILE, takayama_seen)
        save_json_file(OKOKU_STATE_FILE, okoku_seen)
        save_json_file(ALLU_STATE_FILE, allu_seen)
        save_json_file(RAGTAG_STATE_FILE, ragtag_seen)

    def start_message():
        all_brands = (
            kindal_brands + trefac_brands + bring_brands + rinkan_brands + hardoff_brands
            + takayama_brands + okoku_brands + allu_brands + ragtag_brands
        )
        names = sorted({b["name"] for b in all_brands})
        send_text(
            "✅ 新着監視を開始/更新しました！\n"
            "監視中の店: KINDAL、トレファク、BRING、RINKAN、オフモール、高山質店、買取王国、ALLU、RAGTAG\n"
            f"対象ブランド: {'、'.join(names)}\n"
            f"これから約{POLL_SECONDS}秒ごとに新着をチェックします。"
        )

    # 環境変数 LOOP_MODE=1 のときは「ループ監視」、それ以外は「1回だけ」
    loop_mode = (os.environ.get("LOOP_MODE") == "1")

    # --- 1回だけチェックするモード（手動の動作確認用）---
    if not loop_mode:
        new_items = one_pass(kindal_first, trefac_first, bring_first, rinkan_first, hardoff_first,
                             ta_first=takayama_first, ok_first=okoku_first, al_first=allu_first,
                             rt_first=ragtag_first)
        if (kindal_first or trefac_first or bring_first or rinkan_first or hardoff_first
                or takayama_first or okoku_first or allu_first or ragtag_first):
            start_message()
        if new_items:
            send_items(new_items)
        save_all()
        print("完了")
        return

    # --- ループ監視モード（短い間隔で見張り続ける）---
    # ②ブランド(利益が出る時だけ通知)は SLOW_SECONDS ごとにだけチェックする。
    # こうすると①の優先ブランド(サンローラン等)の見張りが遅くならない。
    # ②(profit_only)の重い全ブランド巡回は、走ると数分かかり その間 ①や fast の
    # 高速チェックを止めてしまう。頻度を下げて(10分ごと)高速ブランドの足を引っ張らない
    # ようにする。全ブランドの棚卸しは毎日の在庫スキャンが別途担当する。
    SLOW_SECONDS = int(os.environ.get("SLOW_SECONDS", "600"))  # ②・KINDAL全ページは何秒ごとに見るか
    # KINDALは新着が出るとすぐ売れるため、1ページ目だけを短い間隔で見る（2026-11 依頼: 10秒ごと）
    KINDAL_SECONDS = int(os.environ.get("KINDAL_POLL_SECONDS", "10"))
    # RINKANは10〜12時に出品が多いので、その時間帯だけ短い間隔で見る（2026-11 依頼）
    RINKAN_WINDOW = os.environ.get("RINKAN_WINDOW", "10:00-12:00")          # 日本時間
    RINKAN_WINDOW_SECONDS = int(os.environ.get("RINKAN_WINDOW_SECONDS", "10"))
    # この時間帯には監視の交代（次の回の準備に5〜10分かかる）を入れない。
    # 終わりの時刻がこの時間帯（の少し前〜終わり）に重なる時は、時間帯の前に交代を済ませる。
    PROTECT_WINDOWS = os.environ.get("PROTECT_WINDOWS", RINKAN_WINDOW)
    HANDOFF_LEAD_MIN = int(os.environ.get("HANDOFF_LEAD_MIN", "20"))         # 何分前までに交代するか

    def parse_windows(spec):
        """'10:00-12:00,20:00-21:00' → [(600, 720), (1200, 1260)]（日本時間の0時からの分）"""
        out = []
        for part in (spec or "").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                a, b = part.split("-")
                ha, ma = map(int, a.split(":"))
                hb, mb = map(int, b.split(":"))
                out.append((ha * 60 + ma, hb * 60 + mb))
            except Exception:
                print(f"  時間帯の書き方が読めません: {part}")
        return out

    def jst_min(ts):
        t = time.gmtime(ts + 9 * 3600)
        return t.tm_hour * 60 + t.tm_min

    def in_windows(ts, wins):
        m = jst_min(ts)
        return any(s <= m < e for s, e in wins)

    def adjust_end(end_ts, wins):
        """終わりの時刻が守りたい時間帯に重なるなら、時間帯の開始 HANDOFF_LEAD_MIN 分前に早める"""
        for s, e in wins:
            m = jst_min(end_ts)
            if s - HANDOFF_LEAD_MIN <= m <= e:
                earlier = end_ts - (m - (s - HANDOFF_LEAD_MIN)) * 60
                if earlier > time.time() + 10 * 60:  # 早めた結果が今すぐ過ぎなら、そのまま
                    print(f"  交代が守りたい時間帯に重なるため、終了を {s // 60:02d}:{s % 60:02d} の"
                          f"{HANDOFF_LEAD_MIN}分前に早めます")
                    return earlier
        return end_ts

    rinkan_wins = parse_windows(RINKAN_WINDOW)
    end_time = adjust_end(time.time() + LOOP_MINUTES * 60, parse_windows(PROTECT_WINDOWS))
    print(f"ループ監視開始: KINDALは専用の見張り役で{KINDAL_SECONDS}秒ごと(各売り場の先頭{KINDAL_NEWEST_LIMIT}件・速報)"
          f"・全ページは{SLOW_SECONDS}秒で一巡 / "
          f"他の店は{POLL_SECONDS}秒ごと（RINKANは{RINKAN_WINDOW}の間{RINKAN_WINDOW_SECONDS}秒ごと） / "
          f"②ブランドは{SLOW_SECONDS}秒ごと / 終了 {time.strftime('%H:%M', time.gmtime(end_time + 9 * 3600))}(日本時間)")

    # まず最初の1回チェック（②ブランドも含めて全部見て記録する）
    new_items = one_pass(kindal_first, trefac_first, bring_first, rinkan_first, hardoff_first,
                         do_slow=True, ta_first=takayama_first, ok_first=okoku_first,
                         al_first=allu_first, rt_first=ragtag_first)
    if (kindal_first or trefac_first or bring_first or rinkan_first or hardoff_first
            or takayama_first or okoku_first or allu_first or ragtag_first):
        start_message()
    if new_items:
        send_items(new_items)
    save_all()
    git_save_state()  # 記録をすぐサーバーに保存（重複通知を防ぐ）

    def handle(items, force_save=False):
        if items:
            print(f"新着 {len(items)} 件 → 通知")
            send_items(items)
        if items or force_save:
            with STATE_LOCK:
                save_all()
                git_save_state()  # 通知したら即サーバー保存（重複通知を防ぐ）

    # 仕事の一覧。他の店は『1ブランドずつ』の小さな仕事に分ける。1つの仕事が数秒で終わるので、
    # その合間にKINDALの10秒チェックを差し込める（大きな巡回でKINDALが待たされない）。
    other_tasks = []  # (目印, 店, ブランド設定, 見た記録, 取得関数)
    for site, brands_, seen_, getter in [
        ("トレファク", trefac_brands, trefac_seen, trefac.fetch_brand_items),
        ("BRING", bring_brands, bring_seen, bring_items),
        ("RINKAN", rinkan_brands, rinkan_seen, rinkan.fetch_brand_items),
        ("オフモール", hardoff_brands, hardoff_seen, hardoff.fetch_brand_items),
        # 高山質店は takayama.py の中で『同じ検索は10分に1回』に抑えている。ここで30秒ごとに
        # 呼んでも、10分たつまでは前回の結果を返すだけでお店には読みに行かない
        ("高山質店", takayama_brands, takayama_seen, takayama.fetch_brand_items),
        # 買取王国は全ブランドで『お店全体の新着ページ』1枚を使い回す（okoku.py）。
        # 3ブランド分の仕事があっても、お店への読み込みは30秒に1回程度
        ("買取王国", okoku_brands, okoku_seen, okoku.fetch_brand_items),
        ("ALLU", allu_brands, allu_seen, allu.fetch_brand_items),
        # RAGTAG は自社サイトがボット対策で読めないため、Yahoo!ショッピングの公式店を見る（ragtag.py）
        ("RAGTAG", ragtag_brands, ragtag_seen, ragtag.fetch_brand_items),
    ]:
        for i, b in enumerate(brands_):
            other_tasks.append((f"{site}:{i}", site, b, seen_, getter))

    def task_interval(site, b, now):
        if b.get("profit_only") and not b.get("fast"):
            return SLOW_SECONDS  # ②ブランドはゆっくり巡回（従来通り）
        if site == "RINKAN" and in_windows(now, rinkan_wins):
            return RINKAN_WINDOW_SECONDS
        return POLL_SECONDS

    # ---- 実際の速さの記録（2026-11-28 依頼「本当にその速度で監視していますか」）----
    # 店ごとに『同じ仕事を前回見てから何秒たったか（実際の間隔）』と『1回の所要時間』を測り、
    # 30分ごとに state/monitor_speed.json に書いてサーバーに保存する。
    # 設定の間隔より実際の間隔が長ければ、どこかで待たされている（新着の画像照合など）。
    SPEED_SAVE_SECONDS = int(os.environ.get("SPEED_SAVE_SECONDS", "1800"))
    speed = {"started": time.time(), "last_start": {}, "gaps": {}, "durs": {}, "target": {},
             "slowest": [], "saved": time.time()}
    speed_lock = threading.Lock()  # KINDALの見張り役と同時に書くため

    def timed(site, key, target, fn):
        t0 = time.time()
        with speed_lock:
            prev = speed["last_start"].get(key)
            if prev is not None:
                speed["gaps"].setdefault(site, []).append(t0 - prev)
            speed["last_start"][key] = t0
            speed["target"][site] = target
        try:
            return fn()
        finally:
            d = time.time() - t0
            with speed_lock:
                speed["durs"].setdefault(site, []).append(d)
                if d >= 15:
                    speed["slowest"].append((round(d, 1), site, time.strftime(
                        "%m/%d %H:%M", time.gmtime(t0 + 9 * 3600))))
                    speed["slowest"] = sorted(speed["slowest"], reverse=True)[:10]

    def save_speed(final=False):
        def q(xs, p):
            xs = sorted(xs)
            return round(xs[min(len(xs) - 1, int(len(xs) * p))], 1) if xs else None
        with speed_lock:
            snap = {k: (dict((kk, list(vv)) for kk, vv in speed[k].items())
                        if k in ("gaps", "durs") else speed[k]) for k in speed}
        sites = {}
        for site in sorted(set(snap["gaps"]) | set(snap["durs"])):
            g, d = snap["gaps"].get(site, []), snap["durs"].get(site, [])
            sites[site] = {
                "設定の間隔(秒)": snap["target"].get(site),
                "実際の間隔_ふつう(秒)": q(g, 0.5),
                "実際の間隔_遅い時(上位10%)(秒)": q(g, 0.9),
                "実際の間隔_最長(秒)": round(max(g), 1) if g else None,
                "1回の所要_ふつう(秒)": q(d, 0.5),
                "1回の所要_最長(秒)": round(max(d), 1) if d else None,
                "チェック回数": len(d),
            }
        out = {
            "更新": time.strftime("%Y-%m-%d %H:%M", time.gmtime(time.time() + 9 * 3600)) + " (日本時間)",
            "この回の開始": time.strftime("%m/%d %H:%M", time.gmtime(speed["started"] + 9 * 3600)),
            "この回の終了済み": final,
            "説明": "『実際の間隔』は、同じ店・同じブランドを前回見てから次に見るまでの秒数。"
                    "高山質店はお店の許可どおり10分に1回だけ読み、間は前回の結果を使う。"
                    "『15秒以上かかったチェック』は、新着の画像照合などで監視が止まっていた時間。",
            "店ごと": sites,
            "15秒以上かかったチェック": [
                {"秒": s, "店": site, "時刻": t} for s, site, t in snap["slowest"]],
        }
        try:
            with open(SPEED_FILE, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=1)
        except Exception as e:
            print(f"  速さの記録の保存に失敗: {e}")
        speed["saved"] = time.time()

    # ---- KINDAL専用の見張り役（2026-11-28 依頼: KINDALはもっと早く・必ずすぐ通知）----
    # 他の店の読み込みや画像照合を待たないよう、KINDALだけ別の見張り役で同時に動かす。
    #  ・新着チェック: 全売り場の先頭ページを『同時に』読む（1回 約1秒）→ KINDAL_SECONDS ごと
    #  ・全ページの確認（取りこぼしの保険）: 1売り場ずつ順番に。SLOW_SECONDS で全売り場が一巡
    #  ・お店から『読みすぎ』(429/403)と言われたら、間隔を自動で広げて様子を見る
    kindal_pool = ThreadPoolExecutor(max_workers=max(1, len(kindal_brands)))

    def kindal_fast_check():
        futs = [(b["collection"], kindal_pool.submit(kindal_items_newest, b)) for b in kindal_brands]
        got, limited = {}, False
        for col, f in futs:
            try:
                got[col] = f.result()
            except Exception as e:
                got[col] = e
                if isinstance(e, urllib.error.HTTPError) and e.code in (403, 429):
                    limited = True

        def getter(b):
            r = got[b["collection"]]
            if isinstance(r, Exception):
                raise r
            return r
        before = _fast_sent_total[0]
        items = check_source(kindal_brands, kindal_seen, False, getter, True, wait=0,
                             fast_notify=True)
        return items, limited, _fast_sent_total[0] != before

    def kindal_worker():
        fails = 0
        n = max(1, len(kindal_brands))
        next_fast, next_full, full_i = time.time(), time.time() + SLOW_SECONDS / n, 0
        while time.time() < end_time:
            now = time.time()
            try:
                if now >= next_fast:
                    items, limited, sent = timed("KINDAL(新着)", "kindal", KINDAL_SECONDS,
                                                 kindal_fast_check)
                    fails = fails + 1 if limited else 0
                    iv = KINDAL_SECONDS if not fails else min(300, KINDAL_SECONDS * (2 ** fails))
                    if fails:
                        print(f"  KINDALから読みすぎの合図 → {iv}秒あけます（{fails}回連続）")
                    next_fast = now + iv
                    handle(items, force_save=sent)
                    continue
                if now >= next_full and kindal_brands:
                    b = kindal_brands[full_i % n]
                    full_i += 1
                    next_full = now + SLOW_SECONDS / n
                    before = _fast_sent_total[0]
                    items = timed("KINDAL(全ページ・1売り場ずつ)", "kindal_full", SLOW_SECONDS / n,
                                  lambda: check_source([b], kindal_seen, False, kindal_items, True,
                                                       wait=0, fast_notify=True))
                    handle(items, force_save=_fast_sent_total[0] != before)
                    continue
            except Exception as e:
                print(f"  KINDALチェック中のエラー（続行）: {e}")
            time.sleep(max(0.05, min(0.5, min(next_fast, next_full) - time.time())))

    kindal_thread = threading.Thread(target=kindal_worker, name="kindal", daemon=True)
    kindal_thread.start()

    now0 = time.time()
    next_run = {}
    for j, t in enumerate(other_tasks):
        # 開始直後に全部の仕事が一度に来ないよう、少しずつずらす
        next_run[t[0]] = now0 + POLL_SECONDS * (j + 1) / max(1, len(other_tasks))

    # 決めた時間内は、くり返しチェックし続ける（2回目以降は初回扱いしない）
    while time.time() < end_time:
        now = time.time()
        # 30分ごとに実際の速さを記録してサーバーに保存（忙しくて休みが無い時も必ず通る場所）
        if now - speed["saved"] >= SPEED_SAVE_SECONDS:
            with STATE_LOCK:
                save_speed()
                git_save_state()
        try:
            # KINDALは専用の見張り役（kindal_worker）が同時に見ている
            # 他の店：期限が来た仕事のうち、一番待っているもの1つだけ
            due = [t for t in other_tasks if now >= next_run[t[0]]]
            if due:
                key, site, b, seen_, getter = min(due, key=lambda t: next_run[t[0]])
                # RINKANの時間帯に入った瞬間から短い間隔に切り替わるよう、次回を先に決める
                iv = task_interval(site, b, now)
                next_run[key] = now + iv
                # 1ブランドだけの仕事なので、ブランド後の待ちは不要（仕事の間隔がサイトへの間隔）
                label = site + ("(RINKAN時間帯)" if site == "RINKAN" and iv == RINKAN_WINDOW_SECONDS
                                else "(②ゆっくり)" if iv == SLOW_SECONDS else "")
                handle(timed(label, key, iv,
                             lambda: check_source([b], seen_, False, getter, True, wait=0)))
                continue
        except Exception as e:
            # 1つの仕事の失敗で監視全体を止めない
            print(f"  チェック中のエラー（続行）: {e}")
        # 次の仕事までの待ち時間（最大1秒ずつ待って様子を見る）
        wait = (min(next_run.values()) if next_run else time.time() + 1) - time.time()
        time.sleep(max(0.2, min(1.0, wait)))

    # KINDALの見張り役が最後の1回を終えるのを待つ
    kindal_thread.join(timeout=180)
    kindal_pool.shutdown(wait=False)
    _match_pool.shutdown(wait=True)  # 照合待ちの新着を最後まで片づける
    # 最後に記録を保存して終了（次の見張りが続きから始められる）
    with STATE_LOCK:
        save_all()
        save_speed(final=True)
        git_save_state()
    print("ループ監視 終了")


if __name__ == "__main__":
    main()
