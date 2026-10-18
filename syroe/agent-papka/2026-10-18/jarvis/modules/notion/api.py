#!/usr/bin/env python3
"""Тонкий клиент Notion API. Только чтение.

Запись сюда намеренно не добавлена: Notion — рабочая система Ильи, и любая
правка оттуда необратима для него (страница с планом дороже, чем кажется).
Читать можно молча, писать — только с его разрешения и отдельным модулем.

Токен: NOTION_TOKEN в .env корня агента (формат ntn_...). Интеграция видит
ТОЛЬКО те страницы, которые к ней подключены вручную через «Connections»
на самой странице; дочерние наследуют доступ от родителя.

    from jarvis.modules.notion.api import search, blocks_text
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jarvis.core import config as C  # noqa: E402

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"          # закреплена намеренно: плавающая версия ломает разбор
TIMEOUT = 30

# Notion отвечает медленно (замер с этой машины: 1.2 с на запрос) и держит
# лимит ~3 запроса в секунду. Последовательный обход упирается в задержку,
# а не в лимит: 700 запросов = 15 минут простоя в ожидании ответа. Поэтому
# запросы идут из нескольких потоков, а темп держит общий на процесс шлагбаум:
# он пропускает не чаще MIN_INTERVAL, сколько бы потоков ни ломилось.
MIN_INTERVAL = 0.34
_gate_lock = threading.Lock()
_last_call = [0.0]


def _gate() -> None:
    with _gate_lock:
        wait = _last_call[0] + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()


def token() -> str:
    """Токен из окружения либо из .env корня агента."""
    envfile = C.ROOT / ".env"
    if envfile.exists():
        for line in envfile.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    t = os.environ.get("NOTION_TOKEN", "").strip()
    if not t:
        raise SystemExit(
            "Нет NOTION_TOKEN. Илья создаёт токен на notion.so/profile/integrations,\n"
            "кладёт в .env строкой NOTION_TOKEN=ntn_... и подключает нужные\n"
            "страницы к интеграции через «Connections» на самой странице."
        )
    return t


def call(path: str, method: str = "GET", body: dict | None = None) -> dict:
    """Один запрос. Уважает 429 (Notion лимитит ~3 запроса в секунду):
    без этого полный обход воркспейса упирается в отлуп на середине."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API}{path}", data=data, method=method,
        headers={
            "Authorization": f"Bearer {token()}",
            "Notion-Version": VERSION,
            "Content-Type": "application/json",
        },
    )
    for attempt in range(5):
        try:
            _gate()
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            payload = e.read().decode(errors="replace")
            if e.code == 429:
                time.sleep(float(e.headers.get("Retry-After", 1)) + 0.5)
                continue
            if e.code >= 500 and attempt < 4:
                time.sleep(1 + attempt)
                continue
            raise RuntimeError(f"Notion {e.code} на {path}: {payload[:300]}") from None
    raise RuntimeError(f"Notion: 5 попыток подряд отбиты на {path}")


def paged(path: str, method: str = "POST", body: dict | None = None) -> list[dict]:
    """Все страницы курсорной выдачи. Notion отдаёт максимум 100 за раз,
    и молчаливо обрезает — has_more проверять обязательно."""
    out: list[dict] = []
    cursor = None
    while True:
        if method == "GET":
            # У GET курсор идёт в строке запроса. Без этого блоки длинной страницы
            # молча обрезались бы на сотне — самая незаметная из возможных потерь.
            q = f"{path}?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
            d = call(q)
        else:
            b = dict(body or {})
            b["page_size"] = 100
            if cursor:
                b["start_cursor"] = cursor
            d = call(path, method, b)
        out.extend(d.get("results", []))
        if not d.get("has_more"):
            return out
        cursor = d.get("next_cursor")


def whoami() -> dict:
    return call("/users/me")


def search(query: str = "", kind: str | None = None) -> list[dict]:
    """Всё, что видно интеграции. kind: 'page' | 'database' | None (оба).
    ВАЖНО: search не показывает страницы, к которым интеграция не подключена, —
    пустой ответ означает «не дали доступ», а не «воркспейс пуст»."""
    body: dict = {"query": query}
    if kind:
        body["filter"] = {"property": "object", "value": kind}
    return paged("/search", body=body)


def db_rows(db_id: str) -> list[dict]:
    return paged(f"/databases/{db_id}/query")


def db_schema(db_id: str) -> dict:
    return call(f"/databases/{db_id}")


def rich(items: list[dict] | None) -> str:
    return "".join(x.get("plain_text", "") for x in (items or []))


def title_of(obj: dict) -> str:
    """Заголовок страницы или базы. У страницы он спрятан в свойстве типа title,
    имя которого произвольно (Name, Задача, Title...), поэтому ищем по типу."""
    if obj.get("object") == "database":
        return rich(obj.get("title")) or "(без названия)"
    for v in (obj.get("properties") or {}).values():
        if v.get("type") == "title":
            return rich(v.get("title")) or "(без названия)"
    return "(без названия)"


def prop_text(v: dict) -> str:
    """Значение свойства строкой. Ради читаемого дампа, не ради точности типов."""
    t = v.get("type")
    x = v.get(t)
    if x is None:
        return ""
    if t in ("title", "rich_text"):
        return rich(x)
    if t in ("number", "url", "email", "phone_number", "string"):
        return str(x)
    if t == "checkbox":
        return "да" if x else "нет"
    if t == "select":
        return x.get("name", "")
    if t == "status":
        return x.get("name", "")
    if t == "multi_select":
        return ", ".join(i.get("name", "") for i in x)
    if t == "date":
        return " → ".join(filter(None, [x.get("start"), x.get("end")]))
    if t in ("people", "created_by", "last_edited_by"):
        return ", ".join(i.get("name", i.get("id", "")) for i in x)
    if t == "files":
        return ", ".join(i.get("name", "") for i in x)
    if t == "relation":
        return f"{len(x)} связ."
    if t == "formula":
        return prop_text({"type": x.get("type"), x.get("type"): x.get(x.get("type"))})
    if t == "rollup":
        return str(x.get("number", x.get("array", "")))[:80]
    if t in ("created_time", "last_edited_time"):
        return str(x)
    return str(x)[:120]


def blocks_text(block_id: str, depth: int = 0, limit: int = 400) -> str:
    """Содержимое страницы плоским текстом, с вложенностью отступами.
    limit — потолок блоков на страницу: у Ильи есть страницы на сотни блоков,
    а нужен смысл, а не побайтовая копия."""
    lines: list[str] = []
    count = 0
    for b in paged(f"/blocks/{block_id}/children", method="GET"):
        if count >= limit:
            lines.append("  " * depth + "… (обрезано)")
            break
        count += 1
        t = b.get("type", "")
        body = b.get(t, {}) or {}
        text = rich(body.get("rich_text")) if isinstance(body, dict) else ""
        pad = "  " * depth
        if t == "child_page":
            lines.append(f"{pad}[стр.] {body.get('title', '')} ({b['id']})")
            continue
        if t == "child_database":
            lines.append(f"{pad}[база] {body.get('title', '')} ({b['id']})")
            continue
        if t == "to_do":
            mark = "x" if body.get("checked") else " "
            lines.append(f"{pad}- [{mark}] {text}")
        elif t.startswith("heading"):
            lines.append(f"{pad}{'#' * int(t[-1])} {text}")
        elif t in ("bulleted_list_item", "numbered_list_item"):
            lines.append(f"{pad}- {text}")
        elif text:
            lines.append(f"{pad}{text}")
        if b.get("has_children") and t not in ("child_page", "child_database") and depth < 3:
            sub = blocks_text(b["id"], depth + 1, limit)
            if sub:
                lines.append(sub)
    return "\n".join(x for x in lines if x.strip())


if __name__ == "__main__":
    me = whoami()
    print("бот:", me.get("name"), "| воркспейс:", me.get("bot", {}).get("owner", {}))
    found = search()
    print("видно объектов:", len(found))
