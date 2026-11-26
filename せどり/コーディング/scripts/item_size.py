# -*- coding: utf-8 -*-
"""
商品のサイズを調べて、メルカリのサイズ区分（XS(SS)/S/M/L/XL(LL)…）にそろえる部品

なぜ必要か（2026-11-26 依頼）:
  同じデザインでも、サイズで売れる値段が大きく変わる（例: 34 のような小さいサイズは安い）。
  サイズを見ずに全サイズの売却例で相場を出すと、小さいサイズの安い商品を
  『利益が出る』と誤って判定してしまう。そこで、お店の商品のサイズを調べ、
  メルカリで『同じサイズ区分』で売れた実例だけで相場を出す（souba_match が使う）。

サイズの取り方:
  1. 商品データに size が入っていればそれを使う（RINKAN は一覧APIにサイズがある）
  2. 無ければ商品ページを1回読んで、サイズの書かれた所を探す
     （重い照合を通った『利益が出そうな候補』だけで行うので、読む回数は少ない）
       トレファク: 「タグ表記サイズ：XS（ 参考サイズ：XS ）」
       RAGTAG    : 「【サイズ】50(XL位)」
       オフモール: 「表記サイズ M」
       その他    : 「サイズ：M」「サイズ M」など
"""

import gzip
import re
import html as _html
import urllib.request

ORDER = ["XXS以下", "XS(SS)", "S", "M", "L", "XL(LL)", "2XL(3L)", "3XL(4L)", "4XL(5L)以上"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.9",
    "Accept-Encoding": "gzip",
}

_page_cache = {}  # url -> サイズの文字（同じ商品を何度も読まない）

# サイズの書かれ方（上から順に試す。参考サイズ・『〇〇位』のように
# お店がS/M/Lに直してくれている物を優先する）
_PATTERNS = [
    r"参考サイズ\s*[：:]\s*([A-Za-z0-9]+)",
    r"【サイズ】\s*([^<【\n]{1,30})",
    r"タグ表記サイズ\s*[：:]?\s*([^\s（(｜|<]{1,15})",
    r"表記サイズ\s*[：:]?\s*([^\s（(｜|<]{1,15})",
    r"サイズ\s*[：:]\s*([^\s（(｜|<、,]{1,15})",
]


def _text(page):
    page = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", " ", page)
    page = re.sub(r"<br\s*/?>", "\n", page)
    return _html.unescape(re.sub(r"<[^>]+>", " ", page))


def extract_size_text(page_html):
    """商品ページのHTMLから、サイズの書かれた文字を取り出す（見つからなければ None）"""
    t = _text(page_html)
    for pat in _PATTERNS:
        m = re.search(pat, t)
        if m:
            v = m.group(1).strip()
            if v and not v.startswith("詳細") and v not in ("表", "について", "感"):
                return v
    return None


def _fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as res:
        raw = res.read()
        if (res.headers.get("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.decompress(raw)
        cs = res.headers.get_content_charset() or "utf-8"
    try:
        return raw.decode(cs, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def size_text_of(item):
    """商品のサイズの文字を返す（データに無ければ商品ページを読む）。分からなければ None"""
    if item.get("size"):
        return str(item["size"])
    url = item.get("url")
    if not url or "mercari.com" in url:
        return None
    if url in _page_cache:
        return _page_cache[url]
    try:
        s = extract_size_text(_fetch(url))
    except Exception as e:
        print(f"  サイズの読み取りに失敗 ({url[:60]}): {e}")
        s = None
    _page_cache[url] = s
    return s


_LETTER = [
    (r"^(XXXS|XXS|2XS|SSS)$", "XXS以下"),
    (r"^(XS|SS)$", "XS(SS)"),
    (r"^S$", "S"),
    (r"^M$", "M"),
    (r"^L$", "L"),
    (r"^(XL|LL)$", "XL(LL)"),
    (r"^(XXL|2XL|3L)$", "2XL(3L)"),
    (r"^(XXXL|3XL|4L)$", "3XL(4L)"),
]


def _letter_bucket(s):
    s = s.upper().replace(" ", "")
    for pat, b in _LETTER:
        if re.match(pat, s):
            return b
    return None


def to_bucket(size_text, item=None):
    """サイズの文字を、メルカリのサイズ区分にそろえる。分からなければ None。
    数字だけのサイズは、ブランド・種類・性別から読み替える:
      ・『〇〇位』『参考サイズ』のようにお店がS/M/Lを書いていればそれを使う
      ・Undercover の 1〜5 は 1=S 2=M 3=L 4=XL 5=XXL
      ・パンツ/デニムの 24〜40 はウエスト(インチ)
      ・シャツの 36〜44 は首回り(cm)
      ・44〜58 は欧州メンズ（44=XS 46=S 48=M 50=L 52=XL）
      ・30〜42 は欧州レディース（34=XS 36=S 38=M 40=L）
    """
    if not size_text:
        return None
    s = str(size_text).strip()
    text = ((item or {}).get("title", "") + " " + (item or {}).get("category", "")).lower()
    brand = ((item or {}).get("brand", "") or "").lower()

    # 「50(XL位)」「XS（参考サイズ：XS）」のように、S/M/L 表記が添えてあればそれを優先
    m = re.search(r"([XSML]{1,4}|LL|3L|4L)\s*位", s, re.I)
    if m and _letter_bucket(m.group(1)):
        return _letter_bucket(m.group(1))
    first = re.split(r"[\s(（/／,、]", s)[0]
    b = _letter_bucket(first)
    if b:
        return b
    if re.search(r"free|フリー|f$", first, re.I):
        return "FREE SIZE"
    m = re.match(r"^[Ww]?(\d{1,2})(?:\.\d)?(インチ|inch|in|号)?$", first, re.I)
    if not m:
        return None
    n = int(m.group(1))
    if "undercover" in brand and 0 <= n <= 6:
        return {0: "XS(SS)", 1: "S", 2: "M", 3: "L", 4: "XL(LL)", 5: "2XL(3L)", 6: "3XL(4L)"}[n]
    pants = any(k in text for k in ["パンツ", "デニム", "ジーンズ", "スラックス", "ジーパン",
                                    "pants", "denim", "jeans", "トラウザー", "ショーツ"])
    if pants and 23 <= n <= 40:
        if n <= 26:
            return "XS(SS)"
        if n <= 28:
            return "S"
        if n <= 30:
            return "M"
        if n <= 32:
            return "L"
        if n <= 34:
            return "XL(LL)"
        return "2XL(3L)"
    shirt = any(k in text for k in ["シャツ", "shirt", "ブラウス"]) and "tシャツ" not in text
    if shirt and 36 <= n <= 44:
        return {36: "XS(SS)", 37: "S", 38: "S", 39: "M", 40: "M", 41: "L", 42: "L",
                43: "XL(LL)", 44: "XL(LL)"}[n]
    if 44 <= n <= 58:
        return {44: "XS(SS)", 45: "XS(SS)", 46: "S", 47: "S", 48: "M", 49: "M", 50: "L",
                51: "L", 52: "XL(LL)", 53: "XL(LL)", 54: "2XL(3L)", 55: "2XL(3L)",
                56: "3XL(4L)", 57: "3XL(4L)", 58: "3XL(4L)"}[n]
    if 30 <= n <= 43:
        if n <= 32:
            return "XXS以下"
        if n <= 34:
            return "XS(SS)"
        if n <= 36:
            return "S"
        if n <= 38:
            return "M"
        if n <= 40:
            return "L"
        return "XL(LL)"
    return None


def neighbors(bucket):
    """となりのサイズ区分（同じ区分の売却例が足りない時に使う）"""
    if bucket not in ORDER:
        return []
    i = ORDER.index(bucket)
    return [ORDER[j] for j in (i - 1, i + 1) if 0 <= j < len(ORDER)]
