# -*- coding: utf-8 -*-
"""
相場DBの大掃除（服以外・単色すぎる服・対象外ブランドを消す）

なぜ必要か:
  相場DBは『売れた実例の見本帳』。服のせどりに集中すると決めたのに、中身は
  バッグ・小物や、無地で見分けのつかない服、もう見張っていないブランドが大半を
  占めている。見本帳を今の方針に合わせて作り直すための道具。

このプログラムがすること:
  1. 相場DBの全商品を、次の4つの物差しで仕分ける
       ① 服以外（名前）… 商品名に「バッグ」「財布」等がある
       ② 服以外（写真）… 保存済みの写真の指紋で判定（実測: 服以外の90%を弾ける）
       ③ 単色すぎる服（写真）… 写真の色の内訳を見て、1色がほとんどを占める物
       ④ 対象外ブランド … 今の監視リスト(watch_mercari.json)に無いブランド
  2. 『下見モード』では何がどれだけ消えるかを数えて報告するだけ（DBは触らない）
  3. 『実行モード』では、指定された物差しに当たった商品を実際に消して詰め直す

安全のしくみ:
  - 既定は下見モード。消すには CLEANUP_MODE=delete を明示する必要がある
  - どの物差しを使うかも1つずつスイッチで指定する（既定は全部オフ）
  - 消す前に必ず件数と例を表示・記録する

メルカリには一切アクセスしない。写真の色を調べる時だけ、写真の配信サーバー(CDN)から
画像を取る（③を使う時のみ。取得は控えめな同時数で行う）。
"""

import os
import json
import sqlite3
import urllib.request

import priority
import geom_verify

DB_FILE = "せどり/データ/data/souba_db.sqlite"
WATCH_FILE = "せどり/データ/watchlists/watch_mercari.json"
REPORT_FILE = "せどり/データ/recon/SOUBA_DB_CLEANUP.txt"
CHANGED_FLAG = "/tmp/souba_db_changed"  # DBを書き換えた時だけ作る目印

MODE = os.environ.get("CLEANUP_MODE", "dry")            # dry=下見 / delete=実行
DEL_NON_CLOTHING = os.environ.get("DEL_NON_CLOTHING") == "1"  # ①②を消す
DEL_PLAIN = os.environ.get("DEL_PLAIN") == "1"                # ③を消す
DEL_OTHER_BRANDS = os.environ.get("DEL_OTHER_BRANDS") == "1"  # ④を消す
# 単色と判断する『1色が占める割合』。大きいほど厳しい（＝消す数が減る）
PLAIN_RATIO = float(os.environ.get("PLAIN_RATIO", "0.5"))
# 色を調べる件数の上限（0=色は調べない）。下見では標本だけ見て、実行時は対象全部を見る
COLOR_LIMIT = int(os.environ.get("COLOR_LIMIT", "0"))
DOWNLOAD_WORKERS = 4  # 写真の同時ダウンロード数（配信サーバーに優しい範囲）


def send_discord(message):
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        print(message)
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


def focus_brands():
    """今の監視リストに入っているブランド名（＝残したいブランド）"""
    try:
        with open(WATCH_FILE, "r", encoding="utf-8") as f:
            return {b.get("name", "") for b in json.load(f).get("brands", [])}
    except Exception:
        return set()


def single_color_ratio(raw):
    """写真の『一番多い色が占める割合』を返す（1.0に近いほど単色）。測れなければ None。

    geom_verify.color_hist は写真の中心70%だけを見て、色あい・鮮やかさ・明るさの
    マス目ごとに画素を数えた物。その中で一番大きいマスの割合を見れば、
    『ほぼ1色の服』か『いろいろな色が入った服』かが分かる。
    （柄の有無をAIに当てさせる方法は精度不足で不採用にしたが、
      こちらは色を直接数えるので判断がぶれにくい）
    """
    try:
        h = geom_verify.color_hist(raw)
        if h is None:
            return None
        total = float(h.sum())
        if total <= 0:
            return None
        return float(h.max()) / total
    except Exception:
        return None


def fetch_bytes(url):
    try:
        import fingerprint
        return fingerprint.download_bytes(url)
    except Exception:
        return None


def measure_colors(targets):
    """[(id, 写真URL), ...] について単色度を測り、{id: 割合} を返す"""
    from concurrent.futures import ThreadPoolExecutor
    out = {}
    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        raws = pool.map(fetch_bytes, [u for _, u in targets])
        for i, ((iid, _url), raw) in enumerate(zip(targets, raws), start=1):
            if raw is not None:
                r = single_color_ratio(raw)
                if r is not None:
                    out[iid] = r
            if i % 500 == 0:
                print(f"  色を調査中 {i}/{len(targets)}")
    return out


def main():
    if not os.path.exists(DB_FILE):
        send_discord("🧹 相場DBの大掃除 中止：相場DBが見つかりません")
        return

    import numpy as np
    keep_brands = focus_brands()
    con = sqlite3.connect(DB_FILE)
    rows = con.execute(
        "SELECT id, name, brand, image_url, vec FROM items"
    ).fetchall()
    total = len(rows)
    print(f"相場DB: 全{total}件 / 残したいブランド: {sorted(keep_brands)}")

    # --- 仕分け ---------------------------------------------------------
    tag = {}        # id → 消す理由（最初に当たった物差し）
    brand_all = {}  # ブランド → 件数
    brand_keep = {}  # ブランド → 残る件数
    examples = {}   # 理由 → 商品名の例
    counts = {"服以外(名前)": 0, "服以外(写真)": 0, "対象外ブランド": 0}
    plain_candidates = []  # 単色を調べる対象 (id, 名前, 写真URL)

    for iid, name, brand, url, vec in rows:
        brand = brand or "(不明)"
        brand_all[brand] = brand_all.get(brand, 0) + 1
        # 下見でも全部の物差しで仕分ける（実際に消すかどうかは最後のスイッチで決める）
        why = None
        if keep_brands and brand not in keep_brands:
            why = "対象外ブランド"
        elif priority.is_non_clothing(name or ""):
            why = "服以外(名前)"
        elif vec is not None:
            v = np.frombuffer(vec, dtype=np.float16).astype("float32")
            n = np.linalg.norm(v)
            if n > 0 and priority.skip_by_image(v / n):
                why = "服以外(写真)"
        if why:
            tag[iid] = why
            counts[why] = counts.get(why, 0) + 1
            examples.setdefault(why, []).append(f"{brand} / {name}")
        else:
            brand_keep[brand] = brand_keep.get(brand, 0) + 1
            if url:
                plain_candidates.append((iid, name or "", url))

    # --- 単色すぎる服を調べる（写真の色を実際に見る）--------------------
    color_ratios = {}
    if COLOR_LIMIT != 0:
        targets = plain_candidates if COLOR_LIMIT < 0 else plain_candidates[:COLOR_LIMIT]
        print(f"単色度を調べる対象: {len(targets)}件（写真を取得して色の内訳を数える）")
        color_ratios = measure_colors([(i, u) for i, _n, u in targets])
        kept_by_name = 0
        for iid, name, url in targets:
            r = color_ratios.get(iid)
            if r is None or r < PLAIN_RATIO:
                continue
            # 色が単色寄りでも、商品名に『柄・装飾』や『希少さ』を示す言葉があれば残す。
            # 縞模様や希少なヴィンテージは、写真の色だけ見ると1色に寄って見えるため
            # （下見で「ストライプ」「BORDER POLO」「Levi's 503B-XX」が単色と判定された）
            if priority.is_flashy(name) or priority._has(
                    name, priority._conf().get("価値ワード", [])):
                kept_by_name += 1
                examples.setdefault("単色だが名前で残した", []).append(f"{r:.2f} / {name}")
                continue
            tag[iid] = "単色すぎる服"
            counts["単色すぎる服"] = counts.get("単色すぎる服", 0) + 1
            examples.setdefault("単色すぎる服", []).append(f"{r:.2f} / {name}")
        if kept_by_name:
            print(f"  単色寄りだが柄・希少さの言葉があるため残した: {kept_by_name}件")

    # --- 報告 -----------------------------------------------------------
    lines = [f"🧹 相場DBの大掃除（{'下見（消しません）' if MODE != 'delete' else '実行'}）",
             f"  全{total}件", ""]
    lines.append("【物差しごとの件数】")
    for why in ("対象外ブランド", "服以外(名前)", "服以外(写真)", "単色すぎる服"):
        n = counts.get(why, 0)
        if why == "単色すぎる服" and COLOR_LIMIT == 0:
            lines.append(f"  {why}: 未調査（写真の色を見る設定が無効）")
            continue
        lines.append(f"  {why}: {n}件")
    lines.append("")
    # 残る件数を数え直す（単色の判定は上のループの後に付くため）
    brand_keep = {}
    for iid, name, brand, url, vec in rows:
        if iid not in tag:
            b = brand or "(不明)"
            brand_keep[b] = brand_keep.get(b, 0) + 1
    lines.append("【ブランド別】残る件数 / 全件数（上位20）")
    for brand, n in sorted(brand_all.items(), key=lambda x: -x[1])[:20]:
        mark = "★" if brand in keep_brands else "  "
        lines.append(f"  {mark} {brand}: {brand_keep.get(brand, 0)} / {n}")
    lines.append("")
    for why, ex in examples.items():
        lines.append(f"【{why} の例】")
        for e in ex[:8]:
            lines.append(f"  ・{e[:70]}")
        lines.append("")
    if color_ratios:
        vals = sorted(color_ratios.values())
        lines.append("【単色度の分布（1.0に近いほど1色）】")
        for p in (10, 25, 50, 75, 90):
            lines.append(f"  下から{p}%: {vals[int(len(vals) * p / 100)]:.2f}")
        for th in (0.3, 0.4, 0.5, 0.6, 0.7):
            n = sum(1 for v in vals if v >= th)
            lines.append(f"  しきい値{th:.1f} なら {n}件 ({n / len(vals) * 100:.0f}%) が単色扱い")
        lines.append("")

    # --- 実行モードなら実際に消す ---------------------------------------
    if MODE == "delete":
        doomed = [iid for iid, why in tag.items()
                  if (why == "対象外ブランド" and DEL_OTHER_BRANDS)
                  or (why.startswith("服以外") and DEL_NON_CLOTHING)
                  or (why == "単色すぎる服" and DEL_PLAIN)]
        if doomed:
            con.executemany("DELETE FROM items WHERE id = ?", [(i,) for i in doomed])
            con.commit()
            con.execute("VACUUM")
            lines.append(f"🗑️ {len(doomed)}件を削除しました（残り {total - len(doomed)}件）")
            try:
                open(CHANGED_FLAG, "w").close()
            except Exception:
                pass
        else:
            lines.append("削除対象がありませんでした（スイッチが全てオフの可能性）")
    else:
        lines.append("※下見モードのため、DBは一切変更していません")
    con.close()

    report = "\n".join(lines)
    print(report)
    os.makedirs(os.path.dirname(REPORT_FILE), exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report)
    send_discord("\n".join(lines[:22]))


if __name__ == "__main__":
    main()
