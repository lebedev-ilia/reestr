#!/usr/bin/env python3
"""Проверки сборщика досье (modules/export/dossier.py).

Главное, что здесь ловится: сборка не имеет права молча отдать документ с
дырой. Факты живут своей жизнью — Илья поправил формулировку, абзац переехал,
маркер исчез — и сборщик обязан упасть с именем пропавшего якоря, а не выдать
наружу половину «кто мы». Отдельно проверяется, что клиентская версия не тащит
внутреннее, что секрет отменяет сборку и что писать можно только в derived/.

Проверки на подделке: ROOT подменяется временным каталогом, живая память не
читается и не пишется (samotest-ne-pishet-v-zhivuyu-pamyat).

Запуск: ./.venv/bin/python -m jarvis.modules.export.selftest
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jarvis.modules.export import dossier as D   # noqa: E402

FACT = """---
id: proba
type: project
---

ЧТО ПРОДАЁМ. Берём один процесс и сдаём работающую систему.

ВНУТРЕННЕЕ. Оценка себестоимости, наружу не идёт.

ХВОСТ. Последний абзац.
"""

MAN = {
    "sections": [
        {"id": "a", "title": "Что продаём", "audience": "any",
         "source": "memory/facts/proba.md", "from": "ЧТО ПРОДАЁМ", "to": "ВНУТРЕННЕЕ"},
        {"id": "b", "title": "Кухня", "audience": "model",
         "source": "memory/facts/proba.md", "from": "ВНУТРЕННЕЕ", "to": "ХВОСТ"},
    ],
    "rules": [],
    "client_sections": [["Клиент", "кто он"], ["Оценка", "по шагам", "model"]],
}


def main() -> int:
    bad = []
    saved = D.ROOT
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "memory" / "facts").mkdir(parents=True)
        (tmp / "memory" / "facts" / "proba.md").write_text(FACT, encoding="utf-8")
        (tmp / "derived").mkdir()
        D.ROOT = tmp
        try:
            # 1. вырезка идёт по якорям и не тащит YAML-шапку
            f = D.fragment("memory/facts/proba.md", "ЧТО ПРОДАЁМ", "ВНУТРЕННЕЕ")
            if "id: proba" in f or "ВНУТРЕННЕЕ" in f:
                bad.append(f"1. вырезка захватила лишнее: {f!r}")
            if "работающую систему" not in f:
                bad.append("1. вырезка потеряла тело абзаца")

            # 2. пропал якорь начала — падение с именем якоря, не тишина
            try:
                D.fragment("memory/facts/proba.md", "ЧЕГО ТАМ НЕТ")
                bad.append("2. пропавшее начало не остановило сборку")
            except D.BuildError as e:
                if "ЧЕГО ТАМ НЕТ" not in str(e):
                    bad.append(f"2. ошибка не назвала якорь: {e}")

            # 3. пропал якорь конца — тоже падение (иначе кусок съест весь файл)
            try:
                D.fragment("memory/facts/proba.md", "ЧТО ПРОДАЁМ", "НЕТ ТАКОГО")
                bad.append("3. пропавший конец не остановил сборку")
            except D.BuildError:
                pass

            # 4. клиентская версия не тащит разделы audience=model
            cl = D.build("проба", "client", today="27.08.2026", man=MAN)
            if "себестоимости" in cl or "Кухня" in cl or "Оценка" in cl:
                bad.append("4. в клиентскую версию попало внутреннее")
            if "работающую систему" not in cl or "## 2. Клиент" not in cl:
                bad.append("4. клиентская версия потеряла общую часть")
            md = D.build("проба", "model", today="27.08.2026", man=MAN)
            if "себестоимости" not in md:
                bad.append("4. версия для своей модели потеряла внутренний раздел")

            # 5. секрет в источнике отменяет сборку целиком
            (tmp / "memory" / "facts" / "proba.md").write_text(
                FACT.replace("работающую систему", "пароль: [СЕКРЕТ_УДАЛЁН_2026-10-18]"), encoding="utf-8")
            try:
                D.build("проба", "model", today="27.08.2026", man=MAN)
                bad.append("5. секрет не остановил сборку")
            except D.BuildError as e:
                if "секрет" not in str(e):
                    bad.append(f"5. неверная причина отказа: {e}")
            (tmp / "memory" / "facts" / "proba.md").write_text(FACT, encoding="utf-8")

            # 6. писать можно только в derived/
            man_file = tmp / "manifest.json"
            man_file.write_text(json.dumps(MAN, ensure_ascii=False), encoding="utf-8")
            saved_man, D.MANIFEST = D.MANIFEST, man_file
            code = D.main(["--topic", "проба", "--out", str(tmp / "memory" / "x.md")])
            if code != 2 or (tmp / "memory" / "x.md").exists():
                bad.append("6. запись мимо derived/ не отбита")
            code = D.main(["--topic", "проба", "--out", "derived/proba.md"])
            if code != 0 or not (tmp / "derived" / "proba.md").exists():
                bad.append("6. запись в derived/ не прошла")
            D.MANIFEST = saved_man
        finally:
            D.ROOT = saved

    # 7. живой манифест: все якоря на месте прямо сейчас
    try:
        code = D.main(["--check"])
        if code != 0:
            bad.append("7. живой манифест разошёлся с памятью")
    except Exception as e:                      # noqa: BLE001
        bad.append(f"7. проверка живого манифеста упала: {e}")

    for b in bad:
        print("ПРОВАЛ:", b)
    print("досье: провалов", len(bad), "из 7 проверок")
    return 1 if bad else 0


run = main

if __name__ == "__main__":
    sys.exit(main())
