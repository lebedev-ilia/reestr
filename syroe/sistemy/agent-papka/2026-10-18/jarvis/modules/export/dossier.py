#!/usr/bin/env python3
"""Сборка досье «кто мы и что продаём» из памяти, а не из головы (bl-090).

Зачем. 25.08 документ для передачи другой нейросети (derived/exports/
nologo-polnyy-kontekst-2026-08-25.md) собирался руками из четырёх фактов —
полтора часа и риск разойтись с памятью. При следующем клиенте это повторилось
бы слово в слово. Здесь общая часть («кто мы», «что продаём», «чем доказываем»,
«кому не пишем», общие правила) вырезается из memory/facts и memory/rules по
манифесту, а руками остаётся только клиентская половина — она у каждого своя.

Текст в этом модуле не хранится: манифест держит АДРЕСА кусков (файл + якоря
начала и конца). Факт переписали и якорь пропал — сборка падает с кодом 2 и
называет пропавший якорь. Молча отдать документ с дырой она не может.

Запуск:
  python3 -m modules.export.dossier --check                 проверить якоря
  python3 -m modules.export.dossier --passport              общая часть в stdout
  python3 -m modules.export.dossier --topic "No Logo Studio" --audience model
  ... --out derived/exports/nologo-kontekst-2026-08-27.md   записать файл

--audience model — для своей нейросети (внутренние разделы включены).
--audience client — наружу клиенту: разделы audience=model выкидываются, и
документ дополнительно проверяется на стоп-слова (внутренний ML-проект и т.п.).
"""
import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).resolve().parent / "manifest.json"

# Наружу такое не уходит ни при какой формулировке просьбы.
SECRETS = [
    re.compile(r"vk1\.[a-z0-9]", re.I),
    re.compile(r"\b(пароль|password|api[_-]?key|secret|Bearer\s)", re.I),
    re.compile(r"\b[0-9a-f]{32,}\b", re.I),
]
# Чего клиент видеть не должен (запреты Ильи и внутренняя кухня).
STOP_CLIENT = ["TrendFlow", "75 компонентов", "1010 задач", "16 агентск",
               "себестоим", "внутренн"]


class BuildError(Exception):
    pass


def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _body(path: Path) -> list:
    """Тело файла памяти без YAML-шапки."""
    if not path.exists():
        raise BuildError(f"нет источника: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return lines[i + 1:]
        raise BuildError(f"шапка не закрыта: {path}")
    return lines


def fragment(rel: str, start: str, stop=None) -> str:
    """Кусок файла памяти: от строки, начинающейся с start, до строки,
    начинающейся с stop (исключительно). Якорь не найден — это ошибка."""
    lines = _body(ROOT / rel)

    def hit(ln: str, anchor: str) -> bool:
        # якорь ищется и по сырой строке, и по очищенной от разметки:
        # «**Правило:**» в правилах и «* modules/…» в фактах — оба случая
        return ln.startswith(anchor) or ln.lstrip("*# ").startswith(anchor)

    begin = None
    for i, ln in enumerate(lines):
        if hit(ln, start):
            begin = i
            break
    if begin is None:
        raise BuildError(f"{rel}: не найдено начало «{start}» — источник переписали")
    end = len(lines)
    if stop:
        for i in range(begin + 1, len(lines)):
            if hit(lines[i], stop):
                end = i
                break
        else:
            raise BuildError(f"{rel}: не найден конец «{stop}» — источник переписали")
    text = "\n".join(lines[begin:end]).strip()
    if not text:
        raise BuildError(f"{rel}: кусок «{start}» пуст")
    return text


def rule_text(rid: str) -> str:
    return fragment(f"memory/rules/{rid}.md", "**Правило:**", "**Почему:**")


def check_secrets(text: str) -> list:
    return [p.pattern for p in SECRETS if p.search(text)]


def check_client(text: str) -> list:
    return [w for w in STOP_CLIENT if w.lower() in text.lower()]


def passport(audience: str = "model", man: dict = None) -> str:
    """Общая часть: одинаковая для любого клиента."""
    man = man or manifest()
    out = []
    for s in man["sections"]:
        if audience == "client" and s.get("audience") == "model":
            continue
        out.append(f"### {s['title']}\n\n{fragment(s['source'], s['from'], s.get('to'))}"
                   f"\n\n_источник: {s['source']}_")
    rules = [f"— {rule_text(r)}\n  _(правило {r})_" for r in man.get("rules", [])]
    if rules:
        out.append("### Правила, общие для любой сделки\n\n" + "\n".join(rules))
    return "\n\n---\n\n".join(out)


def build(topic: str, audience: str = "model", today: str = None,
          man: dict = None) -> str:
    man = man or manifest()
    today = today or dt.date.today().strftime("%d.%m.%Y")
    who = ("языковой модели, которая о нас ничего не знает"
           if audience == "model" else "человеку на стороне клиента")
    head = (f"# Полный контекст: кто мы, что делаем и что происходит по теме «{topic}»\n\n"
            f"Собрано {today} сборщиком modules/export/dossier.py. Рассчитан на передачу {who}.\n"
            f"Общая часть вырезана из памяти дословно; клиентская заполняется руками.\n"
            f"Всё, что здесь написано, — либо слова участников дословно, либо проверенный\n"
            f"факт. Домыслов нет; там, где мы чего-то не знаем, это сказано прямо.\n\n"
            f"---\n\n## 0. Зачем этот документ\n\n"
            f"ЗАПОЛНИТЬ: кому уходит, зачем, что читатель должен уметь после прочтения.\n\n"
            f"---\n\n## 1. Кто мы\n\n")
    body = [head + passport(audience, man)]
    n = 1
    for row in man["client_sections"]:
        title, hint = row[0], row[1]
        only = row[2] if len(row) > 2 else "any"
        if audience == "client" and only == "model":
            continue
        n += 1
        body.append(f"## {n}. {title}\n\nЗАПОЛНИТЬ: {hint}.")
    doc = "\n\n---\n\n".join(body) + "\n"
    bad = check_secrets(doc)
    if bad:
        raise BuildError(f"в документ попал секрет ({', '.join(bad)}) — сборка отменена")
    if audience == "client":
        stop = check_client(doc)
        if stop:
            raise BuildError(f"для клиента запрещены слова: {', '.join(stop)}")
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Досье «кто мы и что продаём»")
    ap.add_argument("--topic", default="", help="тема/клиент для каркаса")
    ap.add_argument("--audience", choices=["model", "client"], default="model")
    ap.add_argument("--passport", action="store_true", help="только общая часть")
    ap.add_argument("--check", action="store_true", help="проверить якоря манифеста")
    ap.add_argument("--out", default="", help="куда записать (только derived/)")
    a = ap.parse_args(argv)
    try:
        man = manifest()
        if a.check:
            for s in man["sections"]:
                fragment(s["source"], s["from"], s.get("to"))
            for r in man.get("rules", []):
                rule_text(r)
            print(f"якоря на месте: {len(man['sections'])} кусков, "
                  f"{len(man.get('rules', []))} правил")
            return 0
        text = (passport(a.audience, man) if a.passport
                else build(a.topic or "без темы", a.audience, man=man))
    except BuildError as e:
        print(f"сборка не вышла: {e}", file=sys.stderr)
        return 2
    if a.out:
        dest = Path(a.out)
        if not dest.is_absolute():
            dest = ROOT / dest
        # Документ восстановим из памяти — живёт только в derived
        # (правило v-derived-tolko-vosstanovimoe).
        if ROOT / "derived" not in dest.parents:
            print("писать можно только в derived/", file=sys.stderr)
            return 2
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        print(f"записано: {dest.relative_to(ROOT)} ({len(text)} знаков)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
