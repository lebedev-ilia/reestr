"""Самотест Notion: чтение (api.py) и правка с журналом отката (write.py).

Запуск:

    cd "…/Agent" && jarvis/.venv/bin/python -m jarvis.modules.notion.selftest

Notion — рабочая система Ильи. Правка оттуда для него необратима (страница с
планом дороже, чем кажется), и единственной защитой служит журнал «как было»
в memory/notion_writes.jsonl плюс rollback(). До 26.08 ни журнал, ни откат не
проверялись ничем: ошибка обнаружилась бы уже на его страницах.

Каждый предохранитель проверяется С ОБЕИХ сторон: и что он отбивает, и что он
пропускает то, что пропускать обязан.

Живых путей здесь нет ни одного:
  * urllib.request.urlopen подменяется на весь прогон заглушкой, которая по
    умолчанию РУГАЕТСЯ: забытая подмена = провал проверки, а не поход в сеть;
  * api.call подменяется игрушечным воркспейсом в памяти;
  * write.JOURNAL уводится во временный каталог, живой
    memory/notion_writes.jsonl сверяется побайтово до и после прогона
    (правило samotest-ne-pishet-v-zhivuyu-pamyat);
  * api.token() подменяется — живой .env не читается.
"""

from __future__ import annotations

import io
import json
import tempfile
import time
import types
import urllib.error
import urllib.request
from pathlib import Path

from jarvis.core import config as C
from jarvis.modules.notion import api as A
from jarvis.modules.notion import write as W

fails: list[str] = []

# Снимается при импорте — до того, как прогон подменит темп на нулевой.
LIVE_INTERVAL = A.MIN_INTERVAL


def check(name: str, ok: bool) -> None:
    print(("  ок   " if ok else "  ПЛОХО") + f"  {name}")
    if not ok:
        fails.append(name)


def section(fn, *a) -> None:
    """Набор проверок целиком. Исключение внутри — это провал набора, а не
    конец прогона: иначе одна сломанная проверка прячет все следующие."""
    try:
        fn(*a)
    except Exception as e:  # noqa: BLE001
        check(f"набор {fn.__name__} упал — {type(e).__name__}: {e}", False)


def boom(fn, *a, **kw) -> str:
    """Вызвать и вернуть текст исключения; пустая строка — прошло без ошибки."""
    try:
        fn(*a, **kw)
        return ""
    except Exception as e:  # noqa: BLE001 — тут важен сам факт отказа
        return f"{type(e).__name__}: {e}"


# --- фикстуры ---------------------------------------------------------------

def page(pid: str, title: str = "Стр", prop: str = "Задача", **props) -> dict:
    p = {"object": "page", "id": pid, "archived": False,
         "properties": {prop: {"type": "title",
                               "title": [{"plain_text": title}]}}}
    p["properties"].update(props)
    return p


def txt(s: str, link: str | None = None, ann: dict | None = None) -> dict:
    it = {"type": "text", "plain_text": s, "text": {"content": s}}
    if link:
        it["text"]["link"] = {"url": link}
    if ann:
        it["annotations"] = ann
    return it


def block(bid: str, t: str = "paragraph", items=None, children=False) -> dict:
    return {"object": "block", "id": bid, "type": t,
            t: {"rich_text": list(items or [])}, "has_children": children}


class Ws:
    """Игрушечный воркспейс: страницы, базы, блоки — в памяти процесса.
    Подставляется вместо api.call, поэтому через него проходят и paged, и
    _walk, и весь write.py целиком."""

    def __init__(self) -> None:
        self.pages: dict[str, dict] = {}
        self.dbs: dict[str, dict] = {}
        self.blocks: dict[str, dict] = {}
        self.kids: dict[str, list[str]] = {}
        self.log: list[tuple[str, str, dict | None]] = []
        self.boom = None      # f(method, path) -> True, чтобы уронить запрос
        self.seq = 0

    # --- служебное ---
    def add_page(self, p: dict) -> dict:
        self.pages[p["id"]] = p
        return p

    def add_block(self, parent: str, b: dict) -> dict:
        self.blocks[b["id"]] = b
        self.kids.setdefault(parent, []).append(b["id"])
        return b

    def patches(self, path_part: str = "") -> list:
        return [x for x in self.log if x[0] == "PATCH" and path_part in x[1]]

    # --- сам обработчик ---
    def call(self, path: str, method: str = "GET", body: dict | None = None) -> dict:
        self.log.append((method, path, body))
        if self.boom and self.boom(method, path):
            raise RuntimeError(f"Notion 400 на {path}: тест уронил запрос")
        clean, _, query = path.partition("?")
        q = dict(x.split("=", 1) for x in query.split("&") if "=" in x)
        parts = clean.strip("/").split("/")

        if parts[:1] == ["pages"] and len(parts) == 2:
            pid = parts[1]
            if pid not in self.pages:
                raise RuntimeError(f"Notion 404 на {path}")
            p = self.pages[pid]
            if method == "PATCH":
                if "archived" in (body or {}):
                    p["archived"] = body["archived"]
                for k, v in (body or {}).get("properties", {}).items():
                    t = next(iter(v))
                    val = v[t]
                    if t == "title":
                        p["properties"][k] = {
                            "type": "title",
                            "title": [{"plain_text": i["text"]["content"]} for i in val]}
                    else:
                        p["properties"][k] = {"type": t, t: val}
            return p

        if parts == ["pages"] and method == "POST":
            self.seq += 1
            pid = f"new-{self.seq}"
            props = {}
            for k, v in (body or {}).get("properties", {}).items():
                t = next(iter(v))
                props[k] = ({"type": "title",
                             "title": [{"plain_text": i["text"]["content"]}
                                       for i in v[t]]}
                            if t == "title" else {"type": t, t: v[t]})
            return self.add_page({"object": "page", "id": pid, "archived": False,
                                  "properties": props, "parent": (body or {}).get("parent")})

        if parts[:1] == ["databases"] and len(parts) == 2:
            did = parts[1]
            if did not in self.dbs:
                raise RuntimeError(f"Notion 404 на {path}")
            d = self.dbs[did]
            if method == "PATCH" and "archived" in (body or {}):
                d["archived"] = body["archived"]
            return d

        if parts[:1] == ["blocks"] and len(parts) == 2 and method == "PATCH":
            b = self.blocks[parts[1]]
            for k, v in (body or {}).items():
                b[k] = v
            return b

        if parts[:1] == ["blocks"] and parts[-1:] == ["children"]:
            ids = self.kids.get(parts[1], [])
            size = int(q.get("page_size", 100))
            start = int(q.get("start_cursor", 0))
            chunk = ids[start:start + size]
            nxt = start + size
            return {"results": [self.blocks[i] for i in chunk],
                    "has_more": nxt < len(ids), "next_cursor": str(nxt)}

        raise RuntimeError(f"тест: неизвестная ручка {method} {path}")


# --- А. транспорт: повторы, заголовки, шлагбаум -----------------------------

class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code: int, payload: str = "{}", retry_after: str = "0"):
    return urllib.error.HTTPError("https://api.notion.com/v1/x", code, "err",
                                  {"Retry-After": retry_after},
                                  io.BytesIO(payload.encode()))


def test_transport(net) -> None:
    print("\nТРАНСПОРТ: повторы и заголовки")
    seen: list = []

    def scen(*answers):
        it = iter(answers)

        def opener(req, timeout=None):
            seen.append(req)
            x = next(it)
            if isinstance(x, Exception):
                raise x
            return Resp(json.dumps(x).encode())
        net["open"] = opener

    scen({"ok": 1})
    check("успешный GET возвращает разобранный ответ", A.call("/users/me") == {"ok": 1})
    # urllib приводит имена заголовков к виду "Notion-version" — сверяем в нижнем
    hdrs = {k.lower(): v for k, v in seen[-1].header_items()}
    check("версия API закреплена в заголовке",
          hdrs.get("notion-version") == "2022-06-28")
    check("токен уходит в Authorization", hdrs.get("authorization") == "Bearer ntn_test")

    seen.clear()
    scen({"ok": 2})
    A.call("/pages", "POST", {"a": 1})
    check("POST шлёт тело json", json.loads(seen[-1].data) == {"a": 1}
          and seen[-1].get_method() == "POST")

    seen.clear()
    scen(http_error(429), {"ok": 3})
    check("429 — повтор, а не падение", A.call("/x") == {"ok": 3} and len(seen) == 2)

    seen.clear()
    scen(http_error(500), http_error(502), {"ok": 4})
    check("5xx — повтор, а не падение", A.call("/x") == {"ok": 4} and len(seen) == 3)

    seen.clear()
    scen(http_error(400, '{"message":"bad"}'), {"ok": 5})
    err = boom(A.call, "/x")
    check("4xx — сразу отказ, без повторов", "400" in err and len(seen) == 1)
    check("текст отказа Notion попадает в ошибку", "bad" in err)

    seen.clear()
    scen(*[http_error(429)] * 5)
    check("пять отлупов подряд — отказ, а не вечный цикл",
          "5 попыток" in boom(A.call, "/x") and len(seen) == 5)

    # Шлагбаум темпа: общий на процесс, иначе обход воркспейса ловит 429.
    # Спать по-настоящему тут незачем — сверяем, СКОЛЬКО сна он запросил.
    seen.clear()
    scen({"ok": 1}, {"ok": 1})
    old, A.MIN_INTERVAL = A.MIN_INTERVAL, 0.5
    A._last_call[0] = 0.0
    slept: list[float] = []
    A.time.sleep = slept.append
    A.call("/x")
    A.call("/x")
    A.MIN_INTERVAL = old
    A.time.sleep = lambda x: None
    check("шлагбаум придерживает второй запрос",
          len(slept) == 1 and 0.3 < slept[0] <= 0.5)
    check("живой темп не быстрее трёх запросов в секунду", LIVE_INTERVAL >= 0.33)

    # Повтор после 429 ждёт столько, сколько просит Notion
    seen.clear()
    slept.clear()
    A.time.sleep = slept.append
    scen(http_error(429, retry_after="2"), {"ok": 9})
    A.call("/x")
    A.time.sleep = lambda x: None
    check("после 429 ждём Retry-After с запасом", 2.0 in slept or 2.5 in slept)


# --- Б. разбор ответов ------------------------------------------------------

def test_razbor() -> None:
    print("\nРАЗБОР ОТВЕТОВ")
    p = page("p1", "Купить хлеб", prop="Название задачи")
    check("заголовок ищется по типу, а не по имени свойства",
          A.title_of(p) == "Купить хлеб")
    empty = page("p2", "", prop="Zzz")
    empty["properties"]["Zzz"]["title"] = []
    check("пустой заголовок не рушит разбор", A.title_of(empty) == "(без названия)")
    check("заголовок базы берётся из title базы",
          A.title_of({"object": "database", "title": [{"plain_text": "Finance"}]}) == "Finance")
    check("страница без свойства-заголовка не рушит разбор",
          A.title_of({"object": "page", "properties": {}}) == "(без названия)")

    pt = A.prop_text
    check("дата с концом читается диапазоном",
          pt({"type": "date", "date": {"start": "2026-08-26", "end": "2026-08-27"}})
          == "2026-08-26 → 2026-08-27")
    check("пустая дата — пустая строка", pt({"type": "date", "date": None}) == "")
    check("галочка читается словом", pt({"type": "checkbox", "checkbox": True}) == "да"
          and pt({"type": "checkbox", "checkbox": False}) == "нет")
    check("мультивыбор склеивается через запятую",
          pt({"type": "multi_select", "multi_select": [{"name": "a"}, {"name": "b"}]}) == "a, b")
    check("формула разворачивается до значения",
          pt({"type": "formula", "formula": {"type": "number", "number": 12}}) == "12")
    check("связи считаются штуками",
          pt({"type": "relation", "relation": [{}, {}, {}]}) == "3 связ.")
    check("число не теряется", pt({"type": "number", "number": 0}) == "0")


# --- В. курсорная выдача ----------------------------------------------------

def test_paged() -> None:
    print("\nКУРСОРНАЯ ВЫДАЧА")
    ws = Ws()
    for i in range(250):
        ws.add_block("root", block(f"b{i}"))
    old, A.call = A.call, ws.call
    try:
        got = A.paged("/blocks/root/children", method="GET")
        check("GET-выдача собирается целиком, а не первой сотней", len(got) == 250)
        check("курсор GET уходит в строку запроса",
              any("start_cursor=" in x[1] for x in ws.log))
        check("размер страницы просится максимальный",
              all("page_size=100" in x[1] for x in ws.log))

        calls: list = []

        def post(path, method="GET", body=None):
            calls.append(body)
            n = len(calls)
            return {"results": [{"i": n}], "has_more": n < 3, "next_cursor": f"c{n}"}

        A.call = post
        got = A.paged("/search", body={"query": "x"})
        check("POST-выдача собирается целиком", len(got) == 3)
        check("курсор POST уходит в тело", calls[1].get("start_cursor") == "c1")
        check("исходное тело запроса не портится", calls[0].get("query") == "x")
        check("has_more=false останавливает обход", len(calls) == 3)
    finally:
        A.call = old


def test_blocks_text() -> None:
    print("\nТЕКСТ СТРАНИЦЫ")
    ws = Ws()
    ws.add_block("pg", block("h", "heading_2", [txt("Раздел")]))
    ws.add_block("pg", block("t1", "to_do", [txt("сделано")]))
    ws.blocks["t1"]["to_do"]["checked"] = True
    ws.add_block("pg", block("t2", "to_do", [txt("не сделано")]))
    ws.add_block("pg", block("p", "paragraph", [txt("текст")], children=True))
    ws.add_block("p", block("in", "bulleted_list_item", [txt("вложенный")]))
    ws.add_block("pg", {"object": "block", "id": "cp", "type": "child_page",
                        "child_page": {"title": "Дочерняя"}, "has_children": True})
    ws.add_block("cp", block("secret", "paragraph", [txt("НЕ ДОЛЖНО ВИДЕТЬСЯ")]))
    old, A.call = A.call, ws.call
    try:
        out = A.blocks_text("pg")
        check("заголовок помечен решётками", "## Раздел" in out)
        check("отметка to_do видна", "- [x] сделано" in out and "- [ ] не сделано" in out)
        check("вложенный блок читается с отступом", "  - вложенный" in out)
        check("дочерняя страница показана ссылкой, а не развёрнута",
              "[стр.] Дочерняя" in out and "НЕ ДОЛЖНО ВИДЕТЬСЯ" not in out)
        ws2 = Ws()
        for i in range(10):
            ws2.add_block("pg", block(f"x{i}", "paragraph", [txt(f"строка {i}")]))
        A.call = ws2.call
        out2 = A.blocks_text("pg", limit=3)
        check("потолок блоков соблюдается", out2.count("строка") == 3
              and "(обрезано)" in out2)
    finally:
        A.call = old


# --- Г. запись и журнал -----------------------------------------------------

def fresh(tmp: Path, name: str) -> Ws:
    """Чистый воркспейс + пустой журнал под одну проверку."""
    W.JOURNAL = tmp / f"{name}.jsonl"
    if W.JOURNAL.exists():
        W.JOURNAL.unlink()
    ws = Ws()
    A.call = ws.call
    return ws


def journal(n: int | None = None) -> list[dict]:
    if not W.JOURNAL.exists():
        return []
    recs = [json.loads(x) for x in W.JOURNAL.read_text(encoding="utf-8").splitlines() if x.strip()]
    return recs[-n:] if n else recs


def test_archive(tmp: Path) -> None:
    print("\nАРХИВ И РАЗАРХИВАЦИЯ")
    ws = fresh(tmp, "arch")
    ws.add_page(page("p1", "Дубль"))
    W.archive("p1", why="дубль")
    rec = journal()[-1]
    check("архив записан в журнал", rec["op"] == "archive" and rec["target"] == "p1")
    check("в журнале лежит «как было»", rec["before"] == {"archived": False})
    check("в журнале лежат заголовок и причина",
          rec["title"] == "Дубль" and rec["why"] == "дубль")
    check("объект действительно ушёл в архив", ws.pages["p1"]["archived"] is True)

    ws = fresh(tmp, "arch2")
    ws.dbs["d1"] = {"object": "database", "id": "d1", "archived": False,
                    "title": [{"plain_text": "База"}]}
    W.archive("d1")
    check("база опознаётся, когда страницы с таким id нет",
          journal()[-1]["kind"] == "databases" and ws.dbs["d1"]["archived"] is True)


def test_zhurnal_do_deystviya(tmp: Path) -> None:
    print("\nЖУРНАЛ ПИШЕТСЯ ДО ДЕЙСТВИЯ")
    ws = fresh(tmp, "before")
    ws.add_page(page("p1", "Важное"))
    ws.boom = lambda m, p: m == "PATCH"
    err = boom(W.archive, "p1")
    check("падение правки не проглатывается", err != "")
    check("след остался, хотя правка не удалась",
          len(journal()) == 1 and journal()[-1]["op"] == "archive")


def test_set_title(tmp: Path) -> None:
    print("\nИМЯ ЗАПИСИ")
    ws = fresh(tmp, "title")
    ws.add_page(page("p1", "старое", prop="Как называется"))
    W.set_title("p1", "новое")
    check("имя проставлено", A.title_of(ws.pages["p1"]) == "новое")
    rec = journal()[-1]
    check("в журнале старое имя и имя свойства",
          rec["before"] == {"title": "старое", "prop": "Как называется"})

    ws2 = fresh(tmp, "title2")
    ws2.add_page({"object": "page", "id": "p2", "archived": False,
                  "properties": {"Число": {"type": "number", "number": 1}}})
    err = boom(W.set_title, "p2", "x")
    check("без свойства-заголовка — отказ", "нет свойства-заголовка" in err)
    check("отказ не оставляет ни строки в журнале, ни правки",
          journal() == [] and ws2.patches() == [])


def test_set_props(tmp: Path) -> None:
    print("\nСВОЙСТВА ЗАПИСИ")
    ws = fresh(tmp, "props")
    ws.add_page(page("p1", "Расход",
                     Сумма={"type": "number", "number": 100},
                     Дата={"type": "date", "date": {"start": "2026-08-01"}},
                     Итог={"type": "formula", "formula": {"type": "number", "number": 5}}))
    W.set_props("p1", {"Сумма": {"number": 250},
                       "Дата": {"date": {"start": "2026-08-26"}}})
    check("свойства записаны", ws.pages["p1"]["properties"]["Сумма"]["number"] == 250)
    rec = journal()[-1]
    check("в журнале прежние значения обоих свойств",
          rec["before"] == {"Сумма": {"number": 100},
                            "Дата": {"date": {"start": "2026-08-01"}}})

    ws2 = fresh(tmp, "props2")
    ws2.add_page(page("p1", "Расход",
                      Сумма={"type": "number", "number": 100},
                      Итог={"type": "formula", "formula": {"type": "number", "number": 5}}))
    check("несуществующее свойство — отказ",
          "нет свойства" in boom(W.set_props, "p1", {"Нету": {"number": 1}}))
    check("нечитаемый тип (формула) — отказ",
          "не пишется" in boom(W.set_props, "p1", {"Итог": {"number": 1}}))
    check("после отказа ни журнала, ни правки",
          journal() == [] and ws2.patches() == [])
    W.set_props("p1", {"Сумма": {"number": 7}})
    check("годное свойство после отказа всё равно пишется",
          ws2.pages["p1"]["properties"]["Сумма"]["number"] == 7)


def test_replace(tmp: Path) -> None:
    print("\nПРАВКА ТЕКСТА БЛОКОВ")
    ws = fresh(tmp, "repl")
    ws.add_block("pg", block("b1", "paragraph",
                             [txt("мусор", link="https://a.b", ann={"bold": True}),
                              txt(" хвост")]))
    ws.add_block("pg", block("b2", "paragraph", [txt("чистая строка")]))
    ws.add_block("pg", block("b3", "bulleted_list_item", [txt("мусор")], children=True))
    ws.add_block("b3", block("b4", "paragraph", [txt("вложенный мусор")]))
    ws.add_block("pg", {"object": "block", "id": "cd", "type": "child_database",
                        "child_database": {"title": "База"}, "has_children": True})
    ws.add_block("cd", block("b5", "paragraph", [txt("мусор внутри базы")]))

    n = W.replace_in_page("pg", "мусор", "")
    check("изменены только блоки с совпадением", n == 3)
    check("блок без совпадения не трогали", "b2" not in [x[1] for x in ws.patches()])
    check("правка идёт вглубь", any("b4" in x[1] for x in ws.patches()))
    check("в дочернюю базу правка не заходит",
          not any("b5" in x[1] for x in ws.patches()))
    left = ws.blocks["b1"]["paragraph"]["rich_text"]
    check("опустевший фрагмент выброшен, остальные целы",
          [i["text"]["content"] for i in left] == [" хвост"])
    ws2 = fresh(tmp, "repl2")
    ws2.add_block("pg", block("b1", "paragraph",
                              [txt("мусор хвост", link="https://a.b", ann={"bold": True})]))
    W.replace_in_page("pg", "мусор ", "")
    it = ws2.blocks["b1"]["paragraph"]["rich_text"][0]
    check("ссылка и оформление переживают правку",
          it["text"].get("link") == {"url": "https://a.b"}
          and it.get("annotations") == {"bold": True})
    check("правка блока записана в журнал с прежним текстом",
          journal()[-1]["op"] == "edit_block"
          and journal()[-1]["before"]["rich_text"][0]["text"]["content"] == "мусор хвост")


def test_rollback(tmp: Path) -> None:
    print("\nОТКАТ")
    ws = fresh(tmp, "rb")
    ws.add_page(page("p1", "было", prop="Имя",
                     Сумма={"type": "number", "number": 10}))
    ws.add_block("pg", block("b1", "paragraph", [txt("старый текст")]))
    W.set_title("p1", "стало")
    W.set_title("p1", "стало дважды")
    W.set_props("p1", {"Сумма": {"number": 99}})
    W.replace_in_page("pg", "старый", "новый")
    W.archive("p1")
    done = W.rollback()
    check("откачены все записи журнала", done == 5)
    check("имя вернулось к самому первому",
          A.title_of(ws.pages["p1"]) == "было")
    check("свойство вернулось", ws.pages["p1"]["properties"]["Сумма"]["number"] == 10)
    check("текст блока вернулся",
          ws.blocks["b1"]["paragraph"]["rich_text"][0]["text"]["content"] == "старый текст")
    check("архив снят", ws.pages["p1"]["archived"] is False)

    ws2 = fresh(tmp, "rb2")
    ws2.add_page(page("p1", "уже в корзине"))
    ws2.pages["p1"]["archived"] = True
    W.archive("p1")
    ws2.log.clear()
    W.rollback()
    check("то, что и до нас лежало в архиве, откат не достаёт",
          ws2.patches() == [])

    ws3 = fresh(tmp, "rb3")
    W.create_page("parent", "черновик", ["текст"])
    W.create_row("db1", {"Имя": {"title": [{"text": {"content": "строка"}}]}})
    check("созданное записано в журнал",
          [r["op"] for r in journal()] == ["create_page", "create_row"])
    W.rollback()
    check("созданное откатывается архивированием",
          all(p["archived"] for p in ws3.pages.values()) and len(ws3.pages) == 2)

    ws4 = fresh(tmp, "rb4")
    ws4.add_page(page("p1", "живая", prop="Имя"))
    ws4.add_page(page("p2", "исчезнет", prop="Имя"))
    W.set_title("p1", "новое имя")
    W.set_title("p2", "новое имя")
    del ws4.pages["p2"]        # страницу за это время удалили руками в Notion
    ws4.log.clear()
    done = W.rollback()
    check("одна неудачная строка не рвёт откат остальных",
          done == 1 and A.title_of(ws4.pages["p1"]) == "живая")
    check("архив несуществующего объекта падает и следа не оставляет",
          boom(W.archive, "нет-такого") != "" and len(journal()) == 2)

    ws5 = fresh(tmp, "rb5")
    ws5.add_page(page("p1", "ноль", prop="Имя"))
    W.set_title("p1", "раз")
    W.set_title("p1", "два")
    W.set_title("p1", "три")
    W.rollback(1)
    check("откат последних n берёт именно последние",
          A.title_of(ws5.pages["p1"]) == "два")

    fresh(tmp, "rb6")
    check("пустой журнал — ноль откатов, а не падение", W.rollback() == 0)


# --- Д. живая память --------------------------------------------------------

def test_zhivaya_pamyat(before: bytes | None) -> None:
    print("\nЖИВОЙ ЖУРНАЛ НЕ ТРОНУТ")
    live = C.NOTION_WRITES
    after = live.read_bytes() if live.exists() else None
    check("memory/notion_writes.jsonl побайтово тот же", after == before)


def run() -> int:
    print("\n--- NOTION: чтение, правка, откат ---")
    live = C.NOTION_WRITES
    before = live.read_bytes() if live.exists() else None
    saved = {"call": A.call, "token": A.token, "time": A.time,
             "wtime": W.time, "journal": W.JOURNAL,
             "urlopen": urllib.request.urlopen, "interval": A.MIN_INTERVAL}
    net = {"open": None}

    def guard(req, timeout=None):
        if net["open"] is None:
            raise AssertionError("самотест полез в сеть — забыта подмена")
        return net["open"](req, timeout)

    urllib.request.urlopen = guard
    A.token = lambda: "ntn_test"
    A.time = types.SimpleNamespace(sleep=lambda x: None, monotonic=time.monotonic)
    W.time = types.SimpleNamespace(sleep=lambda x: None)
    A.MIN_INTERVAL = 0.0
    try:
        section(test_transport, net)
        net["open"] = None          # дальше сеть под запретом
        section(test_razbor)
        section(test_paged)
        section(test_blocks_text)
        with tempfile.TemporaryDirectory(prefix="jarvis-notion-selftest-") as td:
            tmp = Path(td)
            for t in (test_archive, test_zhurnal_do_deystviya, test_set_title,
                      test_set_props, test_replace, test_rollback):
                section(t, tmp)
    finally:
        A.call, A.token, A.time = saved["call"], saved["token"], saved["time"]
        W.time, W.JOURNAL = saved["wtime"], saved["journal"]
        urllib.request.urlopen = saved["urlopen"]
        A.MIN_INTERVAL = saved["interval"]

    test_zhivaya_pamyat(before)
    check("журнал правок восстановлен на живой после прогона",
          W.JOURNAL == C.NOTION_WRITES)
    print("\nВСЁ ХОРОШО" if not fails else f"\nСЛОМАНО ({len(fails)}): {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(run())
