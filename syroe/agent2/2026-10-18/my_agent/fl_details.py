#!/usr/bin/env python3
"""Догрузка деталей заказов FL.ru: полное ТЗ, бюджет из JSON-LD, возраст и отзывы заказчика.

Источник: публичная страница заказа https://www.fl.ru/projects/<id>/<slug>.html
(проверено: отдаётся без авторизации; в HTML есть schema.org Product с description и price).

Запуск:
    python3 my_agent/fl_details.py --in my_agent/fl_shortlist.json --limit 180
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

LD_RE = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S)
REG_RE = re.compile(r'Зарегистрирован:\s*([^<]{0,40})')
REV_RE = re.compile(r'Отзывы\s*·?\s*(\d+)\s*([\d.,]+)?')
RESP_RE = re.compile(r'отклик\w*[^0-9<]{0,30}(\d+)', re.I)
CAT_RE = re.compile(r'Все проекты\s*→\s*([^→]{2,80})→')


def clean(s: str) -> str:
    s = re.sub(r'<[^>]+>', ' ', s or "")
    s = htmllib.unescape(s).replace('\xa0', ' ').replace('\u2009', ' ')
    return re.sub(r'\s+', ' ', s).strip()


def fetch(url: str, tries: int = 3) -> str:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       "Accept-Language": "ru-RU,ru;q=0.9"})
            with urllib.request.urlopen(req, timeout=35) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 + 3 * i)
    raise RuntimeError(f"fetch failed {url}: {last}")


def parse_detail(h: str):
    out = {"full_desc": None, "price": None, "client_reg": None,
           "client_reviews": None, "client_rating": None, "cat_path": None, "responses": None}
    for m in LD_RE.finditer(h):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict) and data.get("@type") == "Product":
            out["full_desc"] = clean(data.get("description"))
            off = data.get("offers") or {}
            if isinstance(off, dict) and off.get("price"):
                try:
                    out["price"] = int(float(off["price"]))
                except Exception:  # noqa: BLE001
                    pass
    txt = clean(h)
    m = REG_RE.search(txt)
    if m:
        out["client_reg"] = re.split(r'Откликнуться|Информация|Отзывы|\d{2}\.\d{2}\.\d{4}',
                                     m.group(1))[0].strip() or None
    m = REV_RE.search(txt)
    if m:
        out["client_reviews"] = int(m.group(1))
        if m.group(2):
            out["client_rating"] = m.group(2)
    m = CAT_RE.search(txt)
    if m:
        out["cat_path"] = clean(m.group(1))
    m = re.search(r'статистика откликов(.{0,400})', txt)
    if m:
        mm = re.search(r'(\d+)\s*отклик', m.group(1)) or re.search(r'отклик\w*[:\s]*(\d+)', m.group(1))
        if mm:
            out["responses"] = int(mm.group(1))
    # в шапке: "...: проект в категории <Категория>, 13.10.2026 в 14:17"
    m = re.search(r'проект в категории\s*([^,]{2,60}),\s*(\d{2}\.\d{2}\.\d{4})\s*в\s*(\d{2}:\d{2})', txt)
    if m:
        out["cat_path"] = out["cat_path"] or clean(m.group(1))
        out["published"] = f"{m.group(2)} {m.group(3)}"
    # имя заказчика: первое осмысленное слово после "Вход" в шапке детальной
    m = re.search(r'\bВход\b\s+([A-Za-zА-Яа-яЁё0-9 _\-\.]{2,40}?)\s+Зарегистрирован', txt)
    if m:
        out["client_name"] = clean(m.group(1))
    m = re.search(r'Заказчик\s+([A-Za-zА-Яа-яЁё0-9 _\-\.]{2,40}?)\s+Зарегистрирован', txt)
    if m and not out.get("client_name"):
        out["client_name"] = clean(m.group(1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="my_agent/fl_shortlist.json")
    ap.add_argument("--out", default="my_agent/fl_details.json")
    ap.add_argument("--limit", type=int, default=180)
    ap.add_argument("--sleep", type=float, default=0.7)
    ap.add_argument("--cache", default="my_agent/fl_cache/detail")
    args = ap.parse_args()

    rows = json.load(open(args.inp, encoding="utf-8"))
    rows = rows[:args.limit]
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    res, errs = [], []
    for n, o in enumerate(rows, 1):
        pid = o["id"]
        cf = cache / f"{pid}.html"
        try:
            if cf.exists() and cf.stat().st_size > 5000:
                h = cf.read_text(encoding="utf-8")
            else:
                h = fetch(o["url"])
                cf.write_text(h, encoding="utf-8")
                time.sleep(args.sleep)
        except Exception as e:  # noqa: BLE001
            errs.append({"id": pid, "url": o["url"], "error": str(e)})
            continue
        d = parse_detail(h)
        merged = dict(o)
        merged.update({k: v for k, v in d.items() if v is not None})
        if d.get("price"):
            merged["budget"] = d["price"]
            merged["budget_raw"] = f"{d['price']} ₽ (из карточки заказа)"
        res.append(merged)
        if n % 20 == 0:
            print(f"  {n}/{len(rows)} обработано")

    Path(args.out).write_text(json.dumps({"orders": res, "errors": errs},
                                         ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nДетали получены: {len(res)}; ошибок: {len(errs)} -> {args.out}")


if __name__ == "__main__":
    main()
