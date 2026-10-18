"""Поиск по файлам ПК: имена и метаданные в SQLite, полнотекст только по имени.

    cd "…/Agent"
    jarvis/.venv/bin/python -m jarvis.modules.fs.index build
    jarvis/.venv/bin/python -m jarvis.modules.fs.index search отчет --ext pdf
    jarvis/.venv/bin/python -m jarvis.modules.fs.index tree
    jarvis/.venv/bin/python -m jarvis.modules.fs.index update

Схема выбрана замерами прототипа (факт pc-dostup-ustroystvo): FTS5 только по
имени файла — путь, каталог, размер, mtime и расширение обычными столбцами.
Класть путь в FTS пробовали: база тяжелее на четверть, строится в полтора раза
дольше, выигрыша нет.

База живёт в `derived/fs/index.db` и пересобирается одной командой (`build`) —
условие правила `v-derived-tolko-vosstanovimoe`.

Содержимое файлов не читается ни при сборке, ни при поиске: см. modules/fs/scan.py.
Личные пути (`.ssh`, `.env`, `*.pem`, `*.key`, профили браузеров) в индексе есть
именем и путём и помечены столбцом `secret`; выдача показывает их со знаком «!».
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from jarvis.core import config as C
from jarvis.modules.fs import scan

DB = C.DERIVED / "fs" / "index.db"

SCHEMA = """
CREATE TABLE files(
  id     INTEGER PRIMARY KEY,
  path   TEXT NOT NULL,
  dir    TEXT NOT NULL,
  name   TEXT NOT NULL,
  norm   TEXT NOT NULL,          -- имя приведённое (нижний регистр, ё→е):
                                 -- по нему идёт и полнотекст, и подстрока
  ext    TEXT NOT NULL,
  size   INTEGER NOT NULL,
  mtime  INTEGER NOT NULL,
  mtns   INTEGER,                -- mtime каталога в нс: по нему update решает,
                                 -- заходить ли внутрь. У файлов пусто.
  kind   TEXT NOT NULL,          -- f файл, d каталог
  secret INTEGER NOT NULL
);
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
-- Подчёркивание намеренно оставлено разделителем: «отчет_2024.pdf» распадается
-- на «отчет» и «2024», и находится по любому из кусков.
CREATE VIRTUAL TABLE names USING fts5(
  norm, content='files', content_rowid='id',
  tokenize="unicode61 remove_diacritics 2");
"""

INDEXES = """
CREATE UNIQUE INDEX i_path ON files(path);
CREATE INDEX i_dir  ON files(dir);
-- Ради поиска подстрокой: LIKE '%…%' индексом не ускоряется, но по этому
-- индексу можно пробежать вместо таблицы — 21 МБ вместо 180, и время запроса
-- перестаёт скакать от 0,3 мс до полусекунды в зависимости от редкости куска.
CREATE INDEX i_norm ON files(norm);
CREATE INDEX i_ext  ON files(ext);
CREATE INDEX i_mt   ON files(mtime);
CREATE INDEX i_sz   ON files(size);
"""

# Триггеры держат FTS в согласии с таблицей при точечных правках (update).
# На массовой заливке они не нужны и только мешают: build ставит их после
# `rebuild`, когда все строки уже на месте.
TRIGGERS = """
CREATE TRIGGER files_ai AFTER INSERT ON files BEGIN
  INSERT INTO names(rowid, norm) VALUES (new.id, new.norm);
END;
CREATE TRIGGER files_ad AFTER DELETE ON files BEGIN
  INSERT INTO names(names, rowid, norm) VALUES ('delete', old.id, old.norm);
END;
CREATE TRIGGER files_au AFTER UPDATE ON files BEGIN
  INSERT INTO names(names, rowid, norm) VALUES ('delete', old.id, old.norm);
  INSERT INTO names(rowid, norm) VALUES (new.id, new.norm);
END;
"""

INSERT = ("INSERT INTO files(path,dir,name,norm,ext,size,mtime,mtns,kind,secret) "
          "VALUES(?,?,?,?,?,?,?,?,?,?)")
UPSERT = INSERT + (" ON CONFLICT(path) DO UPDATE SET size=excluded.size, "
                   "mtime=excluded.mtime, mtns=excluded.mtns, kind=excluded.kind")


# --------------------------------------------------------------------------
# Мелочи вывода
# --------------------------------------------------------------------------

def human(n: int) -> str:
    for unit, step in (("Т", 1 << 40), ("Г", 1 << 30), ("М", 1 << 20), ("К", 1 << 10)):
        if n >= step:
            v = n / step
            return f"{v:.0f}{unit}" if v >= 10 else f"{v:.1f}{unit}"
    return f"{n}Б"


def human_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}м"
    if n >= 1_000:
        return f"{n / 1000:.0f}к"
    return str(n)


def plural(n: int, one: str, few: str, many: str) -> str:
    """«1 узел», «2 узла», «121 узел», «5 узлов» — карту читает человек."""
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        form = one
    elif 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        form = few
    else:
        form = many
    return f"{n} {form}"


def parse_size(s: str) -> int:
    s = s.strip().upper().replace(",", ".")
    mult = 1
    for suffix, step in (("K", 1 << 10), ("M", 1 << 20), ("G", 1 << 30), ("T", 1 << 40)):
        if s.endswith(suffix):
            s, mult = s[:-1], step
            break
    return int(float(s) * mult)


def parse_when(s: str) -> int:
    """`30d`, `12h`, `2026-08-01` — в секунды эпохи."""
    s = s.strip()
    if s and s[-1] in "dhm" and s[:-1].isdigit():
        step = {"d": 86400, "h": 3600, "m": 60}[s[-1]]
        return int(time.time()) - int(s[:-1]) * step
    return int(time.mktime(time.strptime(s[:10], "%Y-%m-%d")))


def last_stats(db: Path = DB) -> dict:
    """Что записал о себе прошлый build/update. Пусто — базы ещё нет.

    Нужна не для показа, а чтобы посчитать разницу: сколько файлов прибавилось
    и сколько исчезло за сутки. Сам update этого не знает — он видит только
    тронутые каталоги, а прирост считается по двум замерам подряд.
    Читает в режиме ro и никогда не бросает: вызывается из ночного цикла,
    ронять свёртку из-за отсутствующей базы нечестно.
    """
    if not db.exists():
        return {}
    try:
        con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            row = con.execute("SELECT value FROM meta WHERE key='stats'").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return {}
    return json.loads(row[0]) if row else {}


def connect(db: Path = DB) -> sqlite3.Connection:
    if not db.exists():
        raise SystemExit(f"индекса нет: {db}\nсобрать: python -m jarvis.modules.fs.index build")
    # as_uri() экранирует пробелы и кириллицу пути; mode=ro — чтобы поиск
    # физически не мог испортить базу.
    return sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)


# --------------------------------------------------------------------------
# Сборка
# --------------------------------------------------------------------------

def _row(path: str, parent: str, name: str, isdir: bool, size: int,
         mtime: int, mtns: int | None):
    ext = "" if isdir else scan.split_ext(name)
    return (path, parent, name, scan.fold(name), ext, size, mtime, mtns,
            "d" if isdir else "f", int(scan.is_secret(path, name, ext)))


def _tune(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    con.execute("PRAGMA cache_size=-65536")
    con.execute("PRAGMA temp_store=MEMORY")


def build(db: Path = DB, roots: list[str] | None = None,
          prune=None, quiet: bool = False) -> dict:
    """Полная пересборка. Пишет во временный файл и подменяет им старый —
    оборванная сборка не оставляет полуживой базы, старая продолжает искать."""
    t0 = time.time()
    auto_roots, auto_prune = scan.roots_and_prune()
    roots = roots or auto_roots
    # Отсев считается всегда, даже когда корень задан руками: иначе `build
    # --root /` весело уедет в /proc и /sys.
    prune = frozenset(auto_prune if prune is None else prune)

    db.parent.mkdir(parents=True, exist_ok=True)
    tmp = db.with_name(db.name + ".building")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    _tune(con)
    con.executescript(SCHEMA)

    errors: list = []
    batch: list = []
    n = 0
    for root in roots:
        try:
            st = os.stat(root)
        except OSError:
            continue
        batch.append(_row(root, os.path.dirname(root) or "/", os.path.basename(root) or root,
                          True, 0, int(st.st_mtime), st.st_mtime_ns))
        for d, _dmtime, entries in scan.walk(root, None, prune, errors):
            for name, isdir, size, mtime, mtns in entries:
                batch.append(_row(os.path.join(d, name), d, name, isdir, size, mtime, mtns))
            if len(batch) >= 20000:
                con.executemany(INSERT, batch)
                n += len(batch)
                batch.clear()
                if not quiet:
                    print(f"\r  {n:>9} записей…", end="", file=sys.stderr, flush=True)
    if batch:
        con.executemany(INSERT, batch)
        n += len(batch)
    if not quiet:
        print(f"\r  {n:>9} записей, строю индексы…", end="", file=sys.stderr, flush=True)

    con.executescript(INDEXES)
    con.execute("INSERT INTO names(names) VALUES('rebuild')")
    con.executescript(TRIGGERS)
    stats = _finish(con, roots, errors, t0, n)
    con.commit()
    con.close()
    tmp.replace(db)
    stats["mb"] = round(db.stat().st_size / (1 << 20), 1)
    if not quiet:
        print("\r" + " " * 60 + "\r", end="", file=sys.stderr)
    return stats


def _finish(con, roots, errors, t0, n) -> dict:
    files = con.execute("SELECT count(*) FROM files WHERE kind='f'").fetchone()[0]
    dirs = con.execute("SELECT count(*) FROM files WHERE kind='d'").fetchone()[0]
    size = con.execute("SELECT coalesce(sum(size),0) FROM files WHERE kind='f'").fetchone()[0]
    stats = {"rows": n, "files": files, "dirs": dirs, "bytes": size,
             "errors": len(errors), "seconds": round(time.time() - t0, 1),
             "roots": roots}
    con.executemany("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                    [("built_at", str(int(time.time()))),
                     ("roots", json.dumps(roots, ensure_ascii=False)),
                     ("stats", json.dumps(stats, ensure_ascii=False)),
                     # первые полсотни недоступных мест — чтобы было видно,
                     # что именно осталось за бортом, и не гадать
                     ("errors", json.dumps(errors[:50], ensure_ascii=False))])
    return stats


# --------------------------------------------------------------------------
# Инкрементальное обновление
# --------------------------------------------------------------------------

def update(db: Path = DB, roots: list[str] | None = None, prune=None,
           quiet: bool = False) -> dict:
    """Перепройти только изменившиеся каталоги.

    Опора — mtime каталога: он меняется, когда в каталоге появился, исчез или
    переименовался ребёнок. Совпал с записанным — stat детей не делается вовсе,
    и обход становится дешёвым: остаётся один `scandir` на каталог.

    Чего этот способ по устройству не видит: правку файла на месте без смены
    имени (новый размер у старого имени). Для «где лежит файл» это неважно, для
    «что изменилось» есть отдельный журнал изменений — это другая задача.
    """
    if not db.exists():
        return build(db, roots, prune, quiet)
    t0 = time.time()
    con = sqlite3.connect(db)
    _tune(con)
    live, auto_prune = scan.roots_and_prune()
    if not roots:
        row = con.execute("SELECT value FROM meta WHERE key='roots'").fetchone()
        roots = live or (json.loads(row[0]) if row else ["/"])
    prune = frozenset(auto_prune if prune is None else prune)

    known = {p: m for p, m in con.execute(
        "SELECT path, mtns FROM files WHERE kind='d'")}
    errors: list = []
    touched = added = removed = 0
    for root in roots:
        for d, dmtime_ns, entries in scan.walk(root, known, prune, errors):
            if entries is None:
                continue
            touched += 1
            cur = {e[0]: e[1:] for e in entries}
            old = {name for (name,) in con.execute(
                "SELECT name FROM files WHERE dir=?", (d,))}
            for name in old - cur.keys():
                removed += _drop(con, os.path.join(d, name))
            con.executemany(UPSERT, [
                _row(os.path.join(d, name), d, name, *rest)
                for name, rest in cur.items()])
            added += len(cur)
            # Свой mtime каталог получает от родителя, но родитель мог не
            # меняться — тогда запись осталась бы со старой меткой, и следующий
            # update пришёл бы сюда снова. Проставляем сами.
            con.execute("UPDATE files SET mtns=? WHERE path=?", (dmtime_ns, d))
    # корни могли пропасть целиком (диск отмонтировали) — их не трогаем:
    # исчезнувший каталог верхнего уровня безопаснее оставить, чем стереть
    # половину индекса из-за неудачного момента запуска
    stats = _finish(con, roots, errors, t0, touched)
    stats.update(touched_dirs=touched, upserts=added, removed=removed)
    con.commit()
    con.close()
    stats["mb"] = round(db.stat().st_size / (1 << 20), 1)
    return stats


def _drop(con, path: str) -> int:
    """Удалить запись и всё её поддерево. Диапазоном по индексу, а не LIKE:
    '/' это 0x2F, следующий символ '0' — 0x30, значит `p/…` целиком лежит
    между `p/` и `p0`."""
    cur = con.execute("DELETE FROM files WHERE path=?1 OR (path>?1||'/' AND path<?1||'0')",
                      (path,))
    return cur.rowcount


# --------------------------------------------------------------------------
# Поиск
# --------------------------------------------------------------------------

ORDERS = {"mtime": "f.mtime DESC", "size": "f.size DESC",
          "name": "f.name ASC", "path": "f.path ASC"}


def _fts_query(q: str) -> str:
    terms = [t for t in scan.fold(q).replace('"', " ").split() if t]
    return " AND ".join(f'"{t}"*' for t in terms)


def search(con, query: str, *, sub: bool = False, dir: str | None = None,
           ext: str | None = None, min_size: int | None = None,
           max_size: int | None = None, newer: int | None = None,
           older: int | None = None, kind: str | None = None,
           secret: bool | None = None, order: str = "mtime",
           limit: int = 40) -> tuple[list[tuple], bool]:
    """Строки `(size, mtime, path, kind, secret)` и признак «есть ещё».

    Без `sub` ищет FTS-ом по префиксу слов имени — это доли миллисекунды.
    С `sub` — подстрокой в любом месте имени, полным просмотром колонки имён
    (сотни миллисекунд на полумиллионе записей). Если FTS не нашёл ничего,
    подстрока пробуется сама: человек, набравший «тчет», должен что-то получить.
    """
    where, args = [], []
    if dir:
        d = os.path.normpath(dir)
        where.append("(f.path=? OR (f.path>?||'/' AND f.path<?||'0'))")
        args += [d, d, d]
    if ext:
        exts = [e.strip().lower().lstrip(".") for e in ext.split(",") if e.strip()]
        where.append("f.ext IN (%s)" % ",".join("?" * len(exts)))
        args += exts
    if min_size is not None:
        where.append("f.size>=?"); args.append(min_size)
    if max_size is not None:
        where.append("f.size<=?"); args.append(max_size)
    if newer is not None:
        where.append("f.mtime>=?"); args.append(newer)
    if older is not None:
        where.append("f.mtime<=?"); args.append(older)
    if kind:
        where.append("f.kind=?"); args.append(kind)
    if secret is not None:
        where.append("f.secret=?"); args.append(int(secret))

    tail = (" AND " + " AND ".join(where)) if where else ""
    cols = "f.size, f.mtime, f.path, f.kind, f.secret"
    order_by = ORDERS.get(order, ORDERS["mtime"])

    def run(sql, params):
        rows = con.execute(sql, params).fetchall()
        return rows[:limit], len(rows) > limit

    if not sub and query.strip():
        sql = (f"SELECT {cols} FROM names n JOIN files f ON f.id=n.rowid "
               f"WHERE names MATCH ?{tail} ORDER BY {order_by} LIMIT ?")
        rows, more = run(sql, [_fts_query(query), *args, limit + 1])
        if rows:
            return rows, more
    if not query.strip():
        # Запрос из одних фильтров: «pdf крупнее 10 МБ», «что изменилось за
        # сутки». Подстроку сюда подмешивать нельзя — пустой образец совпадает
        # со всем, и полсекунды уходит на перебор всего индекса впустую.
        head = ("WHERE " + " AND ".join(where)) if where else ""
        sql = f"SELECT {cols} FROM files f {head} ORDER BY {order_by} LIMIT ?"
        return run(sql, [*args, limit + 1])
    like = "%" + scan.fold(query).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    # Отбор подстроки — отдельным подзапросом по одному столбцу: так SQLite
    # бежит по покрывающему индексу i_norm, а в таблицу лезет только за
    # найденным. Прямой `WHERE f.norm LIKE …` читал бы всю таблицу.
    sql = (f"SELECT {cols} FROM files f WHERE f.id IN "
           f"(SELECT rowid FROM files WHERE norm LIKE ? ESCAPE '\\'){tail} "
           f"ORDER BY {order_by} LIMIT ?")
    return run(sql, [like, *args, limit + 1])


def format_rows(rows: list[tuple], more: bool) -> str:
    if not rows:
        return "ничего не найдено"
    out = []
    for size, mtime, path, kind, secret in rows:
        when = time.strftime("%y-%m-%d", time.localtime(mtime)) if mtime else "  —   "
        mark = "!" if secret else " "
        left = " кат." if kind == "d" else human(size)
        out.append(f"{left:>6} {when} {mark}{path}")
    if more:
        out.append("… есть ещё, сузь запрос или подними --limit")
    if any(r[4] for r in rows):
        out.append("! — личное: путь показан, содержимое не открывать")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Карта каталогов
# --------------------------------------------------------------------------

def tree(con, depth: int = 2, top: int = 8, roots: list[str] | None = None) -> str:
    """Дерево на два уровня с числом файлов и объёмом узла.

    Считается одним проходом по столбцу каталогов — отдельной таблицы под это
    нет намеренно: она разъехалась бы с индексом после первого же `update`.
    Вывод — единицы килобайт, чтобы главный агент держал карту в контексте.
    """
    if roots is None:
        row = con.execute("SELECT value FROM meta WHERE key='roots'").fetchone()
        roots = json.loads(row[0]) if row else ["/"]
    roots = sorted(roots, key=len, reverse=True)
    node: dict[str, list[int]] = {}

    def add(key, size):
        cell = node.get(key)
        if cell is None:
            node[key] = [1, size]
        else:
            cell[0] += 1
            cell[1] += size

    for d, size in con.execute("SELECT dir, size FROM files WHERE kind='f'"):
        root = next((r for r in roots if d == r or d.startswith(r.rstrip("/") + "/")), None)
        if root is None:
            continue
        add(root, size)
        rel = d[len(root):].strip("/")
        if not rel:
            continue
        parts = rel.split("/", depth)
        cur = root.rstrip("/")
        for part in parts[:depth]:
            cur = cur + "/" + part
            add(cur, size)

    lines = []
    for root in sorted(roots):
        if root not in node:
            continue
        files, size = node[root]
        lines.append(f"{root}   {human_count(files)} файлов, {human(size)}")
        _branch(lines, node, root.rstrip("/"), 1, depth, top)
    return "\n".join(lines)


def _branch(lines, node, prefix, level, depth, top) -> None:
    if level > depth:
        return
    head = prefix + "/"
    # `len(k) > len(head)` отсекает сам корень: у «/» после отрезания «/»
    # остаётся пустая строка, и без этого условия корень попадал бы в
    # собственные дети безымянной строкой.
    kids = [(k, v) for k, v in node.items()
            if k.startswith(head) and len(k) > len(head) and "/" not in k[len(head):]]
    kids.sort(key=lambda kv: -kv[1][1])
    pad = "  " * level
    for k, (files, size) in kids[:top]:
        lines.append(f"{pad}{k[len(head):]:<28} {human_count(files):>6} {human(size):>7}")
        _branch(lines, node, k, level + 1, depth, top)
    rest = kids[top:]
    if rest:
        lines.append(f"{pad}{'… ещё ' + plural(len(rest), 'узел', 'узла', 'узлов'):<28} "
                     f"{human_count(sum(v[0] for _, v in rest)):>6} "
                     f"{human(sum(v[1] for _, v in rest)):>7}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _stats_line(s: dict) -> str:
    return (f"файлов {s['files']}, каталогов {s['dirs']}, "
            f"объём {human(s['bytes'])}, база {s.get('mb', '?')} МБ, "
            f"{s['seconds']} с, недоступно мест {s['errors']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="jarvis.modules.fs.index",
                                 description="индекс файлов ПК: имена и метаданные")
    ap.add_argument("--db", type=Path, default=DB)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="полная пересборка индекса")
    b.add_argument("--root", action="append", help="обойти только это (можно повторять)")
    u = sub.add_parser("update", help="перепройти изменившиеся каталоги")
    u.add_argument("--root", action="append")

    s = sub.add_parser("search", help="поиск по имени")
    s.add_argument("query", nargs="?", default="")
    s.add_argument("--sub", action="store_true", help="подстрока в любом месте имени")
    s.add_argument("--dir", help="только внутри этого пути")
    s.add_argument("--ext", help="расширения через запятую: pdf,docx")
    s.add_argument("--min", dest="min_size", help="размер от: 10M, 1G")
    s.add_argument("--max", dest="max_size", help="размер до")
    s.add_argument("--newer", help="изменён после: 7d, 12h, 2026-01-01")
    s.add_argument("--older", help="изменён до")
    s.add_argument("--kind", choices=("f", "d"), help="только файлы или каталоги")
    s.add_argument("--secret", action="store_true", help="только личные пути")
    s.add_argument("--sort", default="mtime", choices=tuple(ORDERS))
    s.add_argument("--limit", type=int, default=40)

    t = sub.add_parser("tree", help="карта каталогов на два уровня")
    t.add_argument("--depth", type=int, default=2)
    t.add_argument("--top", type=int, default=8)
    t.add_argument("--root", action="append")

    sub.add_parser("stat", help="что в индексе и когда собран")
    a = ap.parse_args(argv)

    if a.cmd in ("build", "update"):
        fn = build if a.cmd == "build" else update
        st = fn(a.db, a.root)
        print(_stats_line(st))
        return 0

    con = connect(a.db)
    if a.cmd == "search":
        rows, more = search(
            con, a.query, sub=a.sub, dir=a.dir, ext=a.ext,
            min_size=parse_size(a.min_size) if a.min_size else None,
            max_size=parse_size(a.max_size) if a.max_size else None,
            newer=parse_when(a.newer) if a.newer else None,
            older=parse_when(a.older) if a.older else None,
            kind=a.kind, secret=True if a.secret else None,
            order=a.sort, limit=a.limit)
        print(format_rows(rows, more))
    elif a.cmd == "tree":
        print(tree(con, a.depth, a.top, a.root))
    else:
        st = last_stats(Path(a.db))
        built = con.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()
        st["mb"] = round(Path(a.db).stat().st_size / (1 << 20), 1)
        print(_stats_line(st))
        print("корни: " + ", ".join(st.get("roots", [])))
        if built:
            print("собран: " + time.strftime("%Y-%m-%d %H:%M", time.localtime(int(built[0]))))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
