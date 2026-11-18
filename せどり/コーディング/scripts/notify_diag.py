# -*- coding: utf-8 -*-
"""
KINDAL新着通知の診断（Discordには送らない）

過去 DIAG_DAYS 日に出た KINDAL の新着を、本番の新着監視(monitor.check_source)と
同じ判定に1件ずつ最後まで通し、「どこで落ちたか／通知されるはずか」を記録する。
Discordの宛先(webhook)は、送信せずに有効かどうかだけ確認する。
結果は せどり/データ/diag/kindal_notify_diag.txt に保存する。
"""

import os
import sys
import json
import time
import collections
import urllib.request
import urllib.parse
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import monitor  # noqa: E402
import priority  # noqa: E402
import souba_match  # noqa: E402

DAYS = int(os.environ.get("DIAG_DAYS", "7"))
OUT = "せどり/データ/diag/kindal_notify_diag.txt"
REPO = os.environ.get("GITHUB_REPOSITORY", "ryosugihara/sedori")
BRANCH = os.environ.get("GITHUB_REF_NAME", "claude/adoring-bell-7rqznj")


def get(url):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "sedori-diag"})
    tok = os.environ.get("GH_TOKEN")
    if tok and "api.github.com" in url:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read()


def seen_days_ago(path, days):
    """DAYS日前の時点の『見た商品』記録を、gitの履歴から取り出す"""
    until = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days * 86400))
    q = urllib.parse.quote(path)
    commits = json.loads(get(f"https://api.github.com/repos/{REPO}/commits"
                             f"?sha={BRANCH}&path={q}&until={until}&per_page=1"))
    if not commits:
        return {}
    qq = "/".join(urllib.parse.quote(p) for p in path.split("/"))
    return json.loads(get(f"https://raw.githubusercontent.com/{REPO}/{commits[0]['sha']}/{qq}"))


def webhook_status():
    """Discordの宛先が生きているか（送信はしない。GETで情報を見るだけ）"""
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        return "未設定"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "sedori-bot/1.0 (+https://github.com/ryosugihara/sedori)"})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read())
        return f"有効（名前: {d.get('name')}）"
    except urllib.error.HTTPError as e:
        return f"エラー HTTP {e.code}"
    except Exception as e:
        return f"確認失敗: {e}"


def main():
    L = [f"KINDAL新着通知の診断（過去{DAYS}日の新着を本番と同じ判定に通した結果）"]
    L.append(f"Discordの宛先(webhook): {webhook_status()}")
    ready = monitor.image_match_ready()
    L.append(f"画像判定の準備(相場DB+AI): {'OK' if ready else 'なし'}")
    L.append(f"派手か利益だけ通知: {priority.only_distinctive()}")
    excludes = monitor.load_excludes()
    souba = monitor.load_souba()
    old = seen_days_ago(monitor.STATE_FILE, DAYS)
    brands = monitor.load_json_file(monitor.BRANDS_FILE, {"brands": []})["brands"]

    funnel = collections.Counter()
    rows = []
    for b in brands:
        key = b.get("collection") or b.get("keyword")
        try:
            items = monitor.kindal_items(b)
        except Exception as e:
            L.append(f"取得失敗 {key}: {e}")
            continue
        olds = set(str(x) for x in old.get(key, []))
        fresh = [it for it in items if str(it.get("id")) not in olds]
        L.append(f"{key}: 出品{len(items)}件 / うち新着{len(fresh)}件")
        for it in fresh:
            t, c = it.get("title", ""), it.get("category", "")
            reason, extra = None, ""
            # monitor.check_source と同じ順番で判定する
            if monitor.is_excluded(it, excludes):
                reason = "除外リスト"
            elif not monitor.matches_only(it, b.get("only_keywords")):
                reason = "only_keywords(デニムのみ等)"
            else:
                why = priority.skip_reason(t, c)
                if why:
                    reason = why
            if reason is None:
                match = None
                if ready and it.get("image"):
                    try:
                        mp = souba["notify_line"] if b.get("profit_only") else None
                        match = souba_match.match_item(it, souba, min_profit=mp)
                    except Exception as e:
                        extra = f"照合エラー:{e} "
                if it.get("_priority_skip"):
                    reason = it["_priority_skip"]
                else:
                    flashy = priority.is_flashy(f"{t} {c}")
                    profit = match.get("profit") if match else None
                    profitable = profit is not None and profit >= souba["notify_line"]
                    extra += (f"派手語={'あり' if flashy else 'なし'} "
                              f"照合={'あり' if match else 'なし'} 利益={profit}")
                    if b.get("profit_only") and not profitable:
                        reason = "利益不足(profit_only)"
                    elif priority.only_distinctive() and not flashy and not profitable:
                        reason = "派手語なし・利益不足"
                    else:
                        reason = "★通知されるはず"
            funnel[reason] += 1
            rows.append(f"[{reason}] {t[:50]} [{c}] {it.get('price')} {extra} {it.get('url')}")

    L += ["", "=== 行き先の内訳 ==="]
    L += [f"{n:4d} {r}" for r, n in funnel.most_common()]
    L += ["", "=== 1件ずつ ==="] + rows
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
