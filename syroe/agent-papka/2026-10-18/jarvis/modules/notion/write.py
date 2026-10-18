#!/usr/bin/env python3
"""Запись в Notion Ильи. Отдельно от api.py — намеренно.

Правило модуля: НИЧЕГО не удаляется навсегда и ничего не делается молча.
Всё, что меняется, сначала снимается «как было» в memory/notion_writes.jsonl,
и на каждую операцию есть обратная. Notion архивирует в корзину, откуда
страница восстанавливается — поэтому архив здесь единственный способ «убрать».

    from jarvis.modules.notion import write as W
    W.archive("3c1015...")            # в корзину, с записью в журнал
    W.unarchive("3c1015...")          # обратно
    W.replace_in_page(page_id, "мусор", "")   # правка текста блоков
    W.rollback()                      # откатить всё из журнала, снизу вверх
"""
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jarvis.core import config as C  # noqa: E402
from jarvis.modules.notion import api  # noqa: E402

JOURNAL = C.NOTION_WRITES


def _note(op: str, target: str, before: dict | None = None, **extra) -> None:
    """Журнал правок. Пишется ДО действия: если действие упадёт на середине,
    лучше лишняя строка в журнале, чем правка без следа."""
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": datetime.now().isoformat(timespec="seconds"),
           "op": op, "target": target, "before": before, **extra}
    with JOURNAL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _kind(obj_id: str) -> str:
    """Страница или база: у них разные ручки в API."""
    try:
        api.call(f"/pages/{obj_id}")
        return "pages"
    except RuntimeError:
        api.call(f"/databases/{obj_id}")
        return "databases"


def archive(obj_id: str, why: str = "") -> dict:
    kind = _kind(obj_id)
    obj = api.call(f"/{kind}/{obj_id}")
    _note("archive", obj_id, before={"archived": obj.get("archived", False)},
          title=api.title_of(obj), kind=kind, why=why)
    return api.call(f"/{kind}/{obj_id}", "PATCH", {"archived": True})


def unarchive(obj_id: str) -> dict:
    kind = _kind(obj_id)
    _note("unarchive", obj_id, before={"archived": True}, kind=kind)
    return api.call(f"/{kind}/{obj_id}", "PATCH", {"archived": False})


def create_page(parent_page_id: str, title: str, paragraphs: list[str] | None = None) -> dict:
    body = {
        "parent": {"page_id": parent_page_id},
        "properties": {"title": {"title": [{"text": {"content": title}}]}},
        "children": [
            {"object": "block", "type": "paragraph",
             "paragraph": {"rich_text": [{"text": {"content": p}}]}}
            for p in (paragraphs or [])
        ],
    }
    page = api.call("/pages", "POST", body)
    _note("create_page", page["id"], before=None, title=title, parent=parent_page_id)
    return page


def create_row(db_id: str, props: dict) -> dict:
    """Создать новую строку (страницу) в базе Notion — в отличие от create_page,
    которая делает дочернюю страницу у обычной страницы, а не запись базы.

    props — уже в форме тела Notion, включая заголовок:
    {"Item": {"title": [{"text": {"content": "..."}}]}, "Amount": {"number": 1}, ...}.
    Откатывается так же, как create_page — архивированием.
    """
    body = {"parent": {"database_id": db_id}, "properties": props}
    page = api.call("/pages", "POST", body)
    _note("create_row", page["id"], before=None, db=db_id, title=api.title_of(page))
    return page


def set_title(page_id: str, title: str) -> dict:
    """Проставить имя записи. Имя свойства-заголовка у баз произвольное,
    поэтому ищем его по типу, а не по названию."""
    page = api.call(f"/pages/{page_id}")
    name = next((k for k, v in page["properties"].items() if v.get("type") == "title"), None)
    if not name:
        raise RuntimeError(f"{page_id}: нет свойства-заголовка")
    _note("set_title", page_id, before={"title": api.title_of(page), "prop": name}, after=title)
    return api.call(f"/pages/{page_id}", "PATCH",
                    {"properties": {name: {"title": [{"text": {"content": title}}]}}})


_WRITABLE = ("date", "rich_text", "select", "multi_select", "number",
             "checkbox", "url", "email", "phone_number", "status", "people",
             "relation", "files")


def set_props(page_id: str, props: dict) -> dict:
    """Поменять свойства записи базы, сохранив прежние значения в журнал.

    props — уже в форме тела Notion: {"Дата": {"date": {"start": "2026-08-27"}}}.
    Заголовок сюда не подавать: для него есть set_title, который сам находит
    свойство-title по типу, а не по названию.
    """
    page = api.call(f"/pages/{page_id}")
    before = {}
    for name in props:
        p = page["properties"].get(name)
        if p is None:
            raise RuntimeError(f"{page_id}: нет свойства {name!r}")
        t = p["type"]
        if t not in _WRITABLE:
            raise RuntimeError(f"{name!r}: тип {t} через API не пишется")
        before[name] = {t: p[t]}
    _note("set_props", page_id, before=before, title=api.title_of(page),
          after=props)
    return api.call(f"/pages/{page_id}", "PATCH", {"properties": props})


def _walk(block_id: str, depth: int = 0) -> list[dict]:
    """Все блоки страницы вглубь. Без этого правка задевает только верхний
    уровень, а мусор обычно лежит в списках внутри разделов."""
    out = []
    for b in api.paged(f"/blocks/{block_id}/children", method="GET"):
        out.append(b)
        if b.get("has_children") and b["type"] not in ("child_page", "child_database") and depth < 4:
            out.extend(_walk(b["id"], depth + 1))
    return out


def replace_in_page(page_id: str, pattern: str, repl: str = "") -> int:
    """Замена по регулярному выражению в тексте блоков страницы.

    Правится содержимое КАЖДОГО фрагмента rich_text по отдельности, а не блок
    целиком: так переживают ссылки, жирный шрифт и прочее оформление, которое
    иначе схлопнулось бы в плоский текст.
    """
    rx = re.compile(pattern)
    changed = 0
    for b in _walk(page_id):
        t = b.get("type", "")
        body = b.get(t)
        if not isinstance(body, dict) or "rich_text" not in body:
            continue
        items = body["rich_text"]
        new_items, hit = [], False
        for it in items:
            if it.get("type") != "text":
                new_items.append(it)
                continue
            old = it["text"]["content"]
            new = rx.sub(repl, old)
            if new != old:
                hit = True
            item = {"type": "text", "text": {"content": new}}
            if it["text"].get("link"):
                item["text"]["link"] = it["text"]["link"]
            if it.get("annotations"):
                item["annotations"] = it["annotations"]
            new_items.append(item)
        if not hit:
            continue
        new_items = [i for i in new_items
                     if i.get("type") != "text" or i["text"]["content"] != ""]
        _note("edit_block", b["id"], before={"rich_text": items}, page=page_id,
              block_type=t, pattern=pattern)
        api.call(f"/blocks/{b['id']}", "PATCH", {t: {"rich_text": new_items}})
        changed += 1
        time.sleep(0.05)
    return changed


def rollback(n: int | None = None) -> int:
    """Откат последних n записей журнала (по умолчанию всех), снизу вверх."""
    if not JOURNAL.exists():
        return 0
    recs = [json.loads(x) for x in JOURNAL.read_text(encoding="utf-8").splitlines() if x.strip()]
    recs = recs[-n:] if n else recs
    done = 0
    for r in reversed(recs):
        op, tid = r["op"], r["target"]
        # Журнал пишется ДО действия, поэтому в нём есть строки о правках,
        # которые не состоялись (Notion, например, не даёт архивировать
        # страницу верхнего уровня). Такая строка не должна рвать откат
        # остальных: одна неудача — предупреждение, а не остановка.
        try:
            if op == "archive" and not (r.get("before") or {}).get("archived"):
                api.call(f"/{r.get('kind', 'pages')}/{tid}", "PATCH", {"archived": False})
            elif op in ("create_page", "create_row"):
                api.call(f"/pages/{tid}", "PATCH", {"archived": True})
            elif op == "set_title":
                b = r["before"]
                api.call(f"/pages/{tid}", "PATCH", {"properties": {
                    b["prop"]: {"title": [{"text": {"content": b["title"]}}]}}})
            elif op == "set_props":
                api.call(f"/pages/{tid}", "PATCH", {"properties": r["before"]})
            elif op == "edit_block":
                t = r["block_type"]
                api.call(f"/blocks/{tid}", "PATCH", {t: {"rich_text": r["before"]["rich_text"]}})
            else:
                continue
        except Exception as e:
            print(f"откат пропущен: {op} {tid[:8]} — {e}")
            continue
        done += 1
    return done


if __name__ == "__main__":
    # Проверка прав одним безобидным действием: создать черновик и убрать его.
    life_os = "3be01540-446f-80be-9500-cfac7da371fe"
    p = create_page(life_os, "· проверка доступа агента (удалить)",
                    ["Служебная страница. Создана, чтобы проверить право записи. "
                     "Сразу уходит в корзину."])
    print("создана:", p["id"])
    archive(p["id"], why="самопроверка права записи")
    print("заархивирована — запись работает")
