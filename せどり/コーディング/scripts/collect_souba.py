# -*- coding: utf-8 -*-
"""
メルカリ相場DB 収集プログラム（2週間に1回だけ動かす）

流れ:
  1. watch_mercari.json のキーワードで、メルカリの「売り切れ」を検索
  2. 商品名・値段・ブランド・サイズ・状態・画像URL を取り出す
  3. 商品写真をダウンロードして「指紋」(CLIPベクトル)にする
  4. まとめて data/souba_db.sqlite（1ファイルのデータベース）に保存

ポイント:
  - すでにDBにある商品はスキップ（2回目からは新しく売れた分だけ＝速い）
  - メルカリには1.5秒間隔でしかアクセスしない（優しく・ブロック回避）
  - 画像そのものは保存しない（指紋だけ保存→リポジトリが太らない）
"""

import os
import json
import time
import sqlite3
import datetime
import urllib.request

import mercari      # メルカリ検索の部品
import fingerprint  # 画像の指紋化の部品

DB_FILE = "せどり/データ/data/souba_db.sqlite"
CONF_FILE = "せどり/データ/watchlists/watch_mercari.json"
REQUEST_WAIT = 1.5   # メルカリ検索の間隔（秒）
IMG_WAIT = 0.1       # 画像ダウンロードの間隔（画像は配信サーバーなので短くてOK）
PAGE_SIZE = 120      # 1ページの件数（メルカリの実質上限）
MAX_PAGES = 25       # 安全のための上限（1キーワード最大 約3000件。暴走防止）

# 状態ランク番号 → 日本語（メルカリの決まり）
CONDITIONS = {1: "新品、未使用", 2: "未使用に近い", 3: "目立った傷や汚れなし",
              4: "やや傷や汚れあり", 5: "傷や汚れあり", 6: "全体的に状態が悪い"}


def open_db():
    """DBを開く（無ければ作る）"""
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    con = sqlite3.connect(DB_FILE)
    con.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,      -- メルカリの商品ID (m1234...)
            name TEXT,                -- 商品名
            price INTEGER,            -- 売れた値段
            brand TEXT,               -- こちらで付けたブランド名(監視と同じ名前)
            size TEXT,                -- サイズ (S/M/L等)
            condition_id INTEGER,     -- 状態ランク(1〜6)
            image_url TEXT,           -- 商品写真のURL
            keyword TEXT,             -- どの検索で見つけたか
            added TEXT,               -- DBに入れた日時
            vec BLOB,                 -- 画像の指紋(float16×512)
            updated INTEGER           -- いつの取引か(相場の鮮度チェックに使う)
        )
    """)
    # 昔に作ったDBに無い列があれば足す（引っ越し処理）
    cols = [r[1] for r in con.execute("PRAGMA table_info(items)")]
    if "updated" not in cols:
        con.execute("ALTER TABLE items ADD COLUMN updated INTEGER")
    if "vec2" not in cols:
        # vec2 = DINOv2の指紋（同一商品の見分け用・CLIPとの二重チェック）
        con.execute("ALTER TABLE items ADD COLUMN vec2 BLOB")
    # 新方式(v2)の指紋。旧方式(vec/vec2)と並べて持ち、精度を実測してから切り替える。
    if "vec3" not in cols:
        # vec3 = SigLIPの指紋（背景切り抜き後・見た目の系統・新世代）
        con.execute("ALTER TABLE items ADD COLUMN vec3 BLOB")
    if "vec4" not in cols:
        # vec4 = DINOv2-largeの指紋（背景切り抜き後・同一商品の見分け・大型）
        con.execute("ALTER TABLE items ADD COLUMN vec4 BLOB")
    return con


def souba_days():
    """相場を何日分まで参照するか（souba.json の設定。既定=半年183日）"""
    try:
        with open("せどり/データ/watchlists/souba.json", "r", encoding="utf-8") as f:
            return int(json.load(f).get("設定", {}).get("相場参照期間_日", 183))
    except Exception:
        return 183


def fetch_sold_until_cutoff(keyword, cutoff):
    """『相場参照期間より古い商品が出てくるまで』ページを取り続ける。
    以前は固定3ページ(最大約360件)で打ち切っていたため、あまり売れない
    (＝人気で無い)キーワードでは参照期間(既定183日)分に達する前に
    打ち切ってしまい、逆によく売れるキーワードは6ヶ月分を取りきれて
    いなかった。キーワードごとの売れ行きに合わせて可変にする。
    """
    out = []
    page_token = ""
    for i in range(MAX_PAGES):
        try:
            items, page_token = mercari.fetch_page(
                keyword, PAGE_SIZE, "STATUS_SOLD_OUT", None, page_token)
        except Exception as e:
            print(f"  メルカリ取得失敗 ({keyword}, {i + 1}ページ目): {e}")
            break
        if not items:
            break
        out.extend(items)
        # このページの中に参照期間より古い物が混ざっていたら、
        # それより後ろのページはもっと古いだけなので打ち切ってよい
        oldest = min((it.get("updated") or 0) for it in items)
        if oldest and oldest < cutoff:
            break
        if not page_token:
            break  # もう続きが無い
        time.sleep(0.5)
    return out


def send_discord(message):
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        return
    data = json.dumps({"content": message[:1900]}).encode()
    req = urllib.request.Request(
        webhook, data=data,
        headers={"Content-Type": "application/json",
                 "User-Agent": "sedori-bot/1.0 (+https://github.com/ryosugihara/sedori)"})
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception as e:
        print(f"Discord送信失敗: {e}")


def main():
    with open(CONF_FILE, "r", encoding="utf-8") as f:
        brands = json.load(f).get("brands", [])

    con = open_db()
    # メルカリショップ（業者の店）が混ざっていたら消す（相場が業者価格でズレるため）。
    # 普通のメルカリの商品IDは「m+数字」。それ以外を削除する。
    removed = con.execute("DELETE FROM items WHERE id NOT GLOB 'm[0-9]*'").rowcount
    if removed:
        con.commit()
        print(f"メルカリショップの混入 {removed} 件をDBから削除しました")
    # 「〇〇様専用」等の予約出品（商品説明が実質なく、相場・画像の参考にならない）を消す。
    # 短いタイトルに「専用」が入っている物だけを対象にする
    # （長いタイトルは商品説明も書かれている実物出品なので残す）。
    reserved = con.execute(
        "DELETE FROM items WHERE name LIKE '%専用%' AND LENGTH(name) <= 15"
    ).rowcount
    if reserved:
        con.commit()
        print(f"予約出品（商品説明なし） {reserved} 件をDBから削除しました")
    # 新品未使用(condition_id=1)は中古せどりの相場として使わない
    # （新品は中古より高く売れがちで、そのまま相場に使うと利益を過大に見積もる）
    brand_new = con.execute("DELETE FROM items WHERE condition_id = 1").rowcount
    if brand_new:
        con.commit()
        print(f"新品未使用 {brand_new} 件をDBから削除しました（中古相場のみ残す）")
    # 相場は変動するので、古い取引（既定：半年より前）はDBから消す
    days = souba_days()
    cutoff = int(time.time()) - days * 86400
    expired = con.execute(
        "DELETE FROM items WHERE updated IS NOT NULL AND updated < ?", (cutoff,)
    ).rowcount
    if expired:
        con.commit()
        print(f"{days}日より古い取引 {expired} 件をDBから削除しました（相場の鮮度維持）")
    known = {r[0] for r in con.execute("SELECT id FROM items")}
    print(f"相場収集を開始（DBには今 {len(known)} 件）")

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    added = 0
    no_image = 0
    per_brand = {}

    for b in brands:
        for kw in b.get("keywords", []):
            items = fetch_sold_until_cutoff(kw, cutoff)
            time.sleep(REQUEST_WAIT)
            # すでにDBにある商品には「いつの取引か」だけ書き込む（まだ無い行だけ）
            for it in items:
                if it.get("updated") and it["id"] in known:
                    con.execute(
                        "UPDATE items SET updated=? WHERE id=? AND updated IS NULL",
                        (it["updated"], it["id"]),
                    )
            # まだDBに無くて、取引が新しく（半年以内）、予約出品でも新品未使用でもない物だけを入れる
            fresh = [it for it in items
                     if it["id"] and it["id"] not in known
                     and not (it.get("updated") and it["updated"] < cutoff)
                     and not ("専用" in it.get("name", "") and len(it.get("name", "")) <= 15)
                     and it.get("condition_id") != 1]
            print(f"  「{kw}」 取得{len(items)}件 / 新規{len(fresh)}件")

            for it in fresh:
                vec, vec2 = None, None
                if it.get("image"):
                    # 1回のダウンロードで2種類の指紋(CLIP+DINO)を作る
                    v, v2 = fingerprint.embed_image_url_both(it["image"])
                    if v is not None:
                        # float16にして半分のサイズで保存（精度はほぼ変わらない）
                        vec = v.astype("float16").tobytes()
                    if v2 is not None:
                        vec2 = v2.astype("float16").tobytes()
                    time.sleep(IMG_WAIT)
                if vec is None:
                    no_image += 1
                con.execute(
                    "INSERT OR IGNORE INTO items "
                    "(id, name, price, brand, size, condition_id, image_url, "
                    " keyword, added, vec, updated, vec2) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (it["id"], it["name"], it["price"], b["name"], it["size"],
                     it.get("condition_id"), it.get("image"), kw, now, vec,
                     it.get("updated"), vec2),
                )
                known.add(it["id"])
                added += 1
                per_brand[b["name"]] = per_brand.get(b["name"], 0) + 1
            con.commit()

    # 取引日時がまだ埋まらなかった行は「DBに入れた日」で代用する
    # （こうすると全部の行が半年たてば自動で消え、古い相場が残らない）
    con.execute("UPDATE items SET updated = CAST(strftime('%s', added) AS INTEGER) "
                "WHERE updated IS NULL AND added IS NOT NULL")
    con.commit()

    # 昔に集めた行でDINO指紋(vec2)が無い物は、写真を取り直して埋める（引っ越し処理）
    todo = con.execute(
        "SELECT id, image_url FROM items "
        "WHERE vec2 IS NULL AND image_url IS NOT NULL AND image_url != ''"
    ).fetchall()
    if todo:
        print(f"DINO指紋の埋め直し: {len(todo)} 件")
        done = 0
        for i, (iid, url) in enumerate(todo, start=1):
            try:
                _, v2 = fingerprint.embed_image_url_both(url)
                if v2 is not None:
                    con.execute("UPDATE items SET vec2=? WHERE id=?",
                                (v2.astype("float16").tobytes(), iid))
                    done += 1
            except Exception as e:
                print(f"  埋め直し失敗 ({iid}): {e}")
            if i % 200 == 0:
                con.commit()
                print(f"  進捗 {i}/{len(todo)}（成功 {done}）")
            time.sleep(IMG_WAIT)
        con.commit()
        print(f"DINO指紋の埋め直し完了: {done}/{len(todo)} 件")

    total = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    with_vec = con.execute("SELECT COUNT(*) FROM items WHERE vec IS NOT NULL").fetchone()[0]
    con.close()

    lines = [f"📚 メルカリ相場DBを更新しました（{now}）",
             f"　新しく追加: {added} 件（うち指紋化できず: {no_image}）",
             f"　DB合計: {total} 件（画像指紋あり {with_vec} 件）", ""]
    for name, n in sorted(per_brand.items(), key=lambda x: -x[1]):
        lines.append(f"　・{name}: +{n}")
    report = "\n".join(lines)
    print(report)
    send_discord(report)


if __name__ == "__main__":
    main()
