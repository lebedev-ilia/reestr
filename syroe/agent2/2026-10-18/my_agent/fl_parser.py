#!/usr/bin/env python3
"""Сбор заказов с FL.ru (публичные страницы категорий) в JSON.

Зачем: подобрать заказы под выполнение агентом + пользователем.
Источник данных: публичный HTML https://www.fl.ru/projects/category/<slug>/page-N/
(проверено вручную: список отдаётся сервером, без авторизации).

Запуск:
    python3 my_agent/fl_parser.py --out my_agent/fl_orders.json
"""
import argparse
import html as htmllib
import json
import re
import time
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

CATEGORIES = {
    "ai": "ai-iskusstvenniy-intellekt",
    "avtomatizaciya": "avtomatizaciya-biznesa",
    "programmirovanie": "programmirovanie",
    "messengers": "messengers",
    "audio-video-photo": "audio-video-photo",
    "konsalting": "konsalting",
    "mobile": "mobile",
    "games": "games",
    "brauzery": "brauzery",
    "socialnye-seti": "socialnye-seti",
    "inzhiniring": "inzhiniring",
    "crypto": "crypto-i-blockchain",
    "saity": "saity",
    "internet-magaziny": "internet-magaziny",
    "marketplace": "marketplace-management",
}

CARD_SPLIT = '<div class="b-post__grid">'
TITLE_RE = re.compile(
    r'<a[^>]*data-disposable-project-id="(\d+)"[^>]*href="(/projects/\d+/[^"]+)"[^>]*>(.*?)</a>',
    re.S)
PRICE_RE = re.compile(r'b-post__price[^>]*>(.*?)</div>', re.S)
DESC_RE = re.compile(r'b-post__txt[^>]*>(.*?)</div>', re.S)
COUNT_RE = re.compile(r'<span[^>]*data-id="fl-view-count"[^>]*>(\d+)')


def clean(s: str) -> str:
    s = re.sub(r'<[^>]+>', ' ', s)
    s = htmllib.unescape(s)
    s = s.replace('\xa0', ' ').replace('\u2009', ' ')
    return re.sub(r'\s+', ' ', s).strip()


def parse_price(raw: str):
    """'10 000 ₽' -> (10000, '10 000 ₽'); 'По договорённости' -> (None, ...)."""
    t = clean(raw)
    m = re.search(r'([\d\s]{3,})\s*₽', t)
    if m:
        digits = re.sub(r'\D', '', m.group(1))
        if digits:
            return int(digits), t
    return None, t


def parse_page(html_text: str, category: str):
    out = []
    parts = html_text.split(CARD_SPLIT)
    for p in parts[1:]:
        m = TITLE_RE.search(p)
        if not m:
            continue
        pid, url, title = m.group(1), m.group(2), clean(m.group(3))
        pm = PRICE_RE.search(p)
        budget, budget_raw = parse_price(pm.group(1)) if pm else (None, "")
        dm = DESC_RE.search(p)
        desc = clean(dm.group(1)) if dm else ""
        cm = COUNT_RE.search(p)
        responses = int(cm.group(1)) if cm else None
        out.append({
            "id": pid,
            "url": "https://www.fl.ru" + url,
            "title": title,
            "budget": budget,
            "budget_raw": budget_raw,
            "desc": desc,
            "responses": responses,
            "category": category,
        })
    return out


def fetch(url: str, tries: int = 3) -> str:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept-Language": "ru-RU,ru;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 + 3 * i)
    raise RuntimeError(f"fetch failed {url}: {last}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="my_agent/fl_orders.json")
    ap.add_argument("--pages", type=int, default=6)
    ap.add_argument("--sleep", type=float, default=1.2)
    ap.add_argument("--cache", default="my_agent/fl_cache")
    args = ap.parse_args()

    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    seen, orders, errors = set(), [], []

    for cat, slug in CATEGORIES.items():
        for page in range(1, args.pages + 1):
            url = (f"https://www.fl.ru/projects/category/{slug}/" if page == 1
                   else f"https://www.fl.ru/projects/category/{slug}/page-{page}/")
            cf = cache / f"{cat}_p{page}.html"
            try:
                if cf.exists() and cf.stat().st_size > 5000:
                    txt = cf.read_text(encoding="utf-8")
                else:
                    txt = fetch(url)
                    cf.write_text(txt, encoding="utf-8")
                    time.sleep(args.sleep)
            except Exception as e:  # noqa: BLE001
                errors.append({"url": url, "error": str(e)})
                break
            items = parse_page(txt, cat)
            if not items:
                break
            new = 0
            for it in items:
                if it["id"] in seen:
                    continue
                seen.add(it["id"])
                orders.append(it)
                new += 1
            print(f"{cat:20s} p{page}: {len(items):3d} cards, {new:3d} new, total {len(orders)}")
            if new == 0:
                break

    Path(args.out).write_text(
        json.dumps({"orders": orders, "errors": errors}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"\nSaved {len(orders)} orders -> {args.out}; errors: {len(errors)}")


if __name__ == "__main__":
    main()
