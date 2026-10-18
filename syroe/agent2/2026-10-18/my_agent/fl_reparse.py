#!/usr/bin/env python3
"""Пересборка кандидатов из кэша детальных страниц (без обращений к сети).

Зачем: правки парсера (дата публикации, возраст заказчика) применяются к уже
скачанному кэшу `my_agent/fl_cache/detail/*.html`.

Запуск: python3 my_agent/fl_reparse.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fl_details import parse_detail  # noqa: E402

MERGED = ["my_agent/fl_details.json", "my_agent/fl_details2.json"]
OUT = "my_agent/fl_candidates.json"


def main():
    det = []
    for f in MERGED:
        p = Path(f)
        if p.exists():
            det += json.load(open(p, encoding="utf-8"))["orders"]
    seen = {o["id"]: o for o in det}
    cache = Path("my_agent/fl_cache/detail")
    ok = miss = 0
    for pid, o in seen.items():
        cf = cache / f"{pid}.html"
        if not cf.exists():
            miss += 1
            continue
        d = parse_detail(cf.read_text(encoding="utf-8"))
        for k, v in d.items():
            if v is not None:
                o[k] = v
        ok += 1
    rows = list(seen.values())
    json.dump({"orders": rows}, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(f"переразобрано {ok}, без кэша {miss}, всего {len(rows)} -> {OUT}")
    print("с датой:", sum(1 for o in rows if o.get("published")),
          "| с возрастом заказчика:", sum(1 for o in rows if o.get("client_reg")),
          "| с отзывами:", sum(1 for o in rows if o.get("client_reviews") is not None))


if __name__ == "__main__":
    main()
