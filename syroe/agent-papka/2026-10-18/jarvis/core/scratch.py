"""Уборка derived/scratch — рабочего стола на один ход.

Зачем. Мусор здесь растёт по одному «положу пока сюда»: на 25.08 в каталоге
лежало 19 папок и три десятка файлов от разовых проверок (bl-085). Руками я его
не сношу и не должен: `rm -rf` шлюз запрещает навсегда, а обход запрета
поштучным `find -delete` — это тот же `rm -rf`, только написанный так, чтобы
предохранитель не сработал. Значит уборку делает код, по одному правилу, каждую
ночь, и оставляет след в логе.

Правило одно: запись верхнего уровня (каталог или файл) удаляется, если её
содержимое не трогали KEEP_DAYS суток. Возраст каталога — по САМОМУ СВЕЖЕМУ
файлу внутри, а не по имени и не по времени самой папки: `2026-08-24-finance`,
куда вчера дописали разбор, — живая работа, а не позавчерашний мусор.

Чем эта уборка ограничена (границу держит код, а не аккуратность):
  · трогается ровно один каталог — derived/scratch, и только его прямые дети;
    путь каждого кандидата сверяется с настоящим (realpath) корнем зоны;
  · символическая ссылка снимается как ссылка и никогда не разворачивается —
    иначе ссылка из scratch наружу увела бы удаление в memory или в дом;
  · сам корень зоны не удаляется никогда;
  · за один прогон уходит не больше MAX_DELETE записей: скачок часов или
    поломанное mtime не должны вычистить всё разом молча.

Россыпь. Правило odnorazovye-proverki-v-derived велит класть проверку в
`scratch/ГГГГ-ММ-ДД-<зачем>/`, но файлы всё равно ложатся прямо в корень.
Уборка их не выделяет — они стареют по тому же правилу, — но СЧИТАЕТ и кладёт
имена в лог: нарушение дисциплины должно быть видно в ночной сводке, пока файлы
ещё свежие, а не через три дня, когда они уже удалены.

Ручной прогон:
    cd "…/Agent" && jarvis/.venv/bin/python -m jarvis.core.scratch        # что уйдёт
    cd "…/Agent" && jarvis/.venv/bin/python -m jarvis.core.scratch --yes  # убрать
"""

import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config as C
from . import memory as M


KEEP_DAYS = 3           # столько суток живёт одноразовый каталог после последней правки
MAX_DELETE = 200        # потолок на один прогон: защита от скачка часов и битых mtime


def root() -> Path:
    return C.DERIVED / "scratch"


def _inside(p: Path) -> bool:
    """Кандидат — прямой ребёнок настоящего корня зоны.

    Сверяется realpath родителя, а не текст пути: `scratch/x/../../memory`
    и подменённый на ссылку `scratch` обязаны отсеяться здесь, а не в
    рассуждении о том, что такого не бывает.
    """
    try:
        base = os.path.realpath(root())
        parent = os.path.realpath(p.parent)
    except OSError:
        return False
    return parent == base and p.name not in ("", ".", "..")


def newest_mtime(p: Path) -> float:
    """Самое свежее время правки в записи. Для каталога — обход всего дерева.

    Пустой каталог не бессмертен: за его возраст считается время самой папки.
    Ссылки не разворачиваются (lstat) — время берётся у самой ссылки.
    """
    try:
        newest = p.lstat().st_mtime
    except OSError:
        return time.time()          # не смогли посмотреть — считаем свежим, не трогаем
    if p.is_symlink() or not p.is_dir():
        return newest
    for cur, dirs, files in os.walk(p, followlinks=False):
        for name in dirs + files:
            try:
                newest = max(newest, os.lstat(os.path.join(cur, name)).st_mtime)
            except OSError:
                continue
    return newest


def size_of(p: Path) -> int:
    if p.is_symlink() or not p.is_dir():
        try:
            return p.lstat().st_size
        except OSError:
            return 0
    total = 0
    for cur, _dirs, files in os.walk(p, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(cur, name)).st_size
            except OSError:
                continue
    return total


def plan(now: float | None = None, keep_days: int = KEEP_DAYS) -> dict:
    """Что уйдёт, что останется, сколько россыпи в корне. Ничего не меняет."""
    now = now if now is not None else time.time()
    edge = now - keep_days * 86400
    base = root()
    out = {"удалить": [], "оставить": [], "россыпь": [], "байт": 0, "потолок": False}
    if not base.is_dir() or base.is_symlink():
        # Корень-ссылка — не зона уборки, а чужой каталог под её именем:
        # realpath у него и у его детей сойдётся, и удаление ушло бы наружу.
        return out

    for p in sorted(base.iterdir()):
        if not _inside(p):
            continue                              # чужое или подменённое — не наше дело
        stale = newest_mtime(p) < edge
        if not p.is_dir() or p.is_symlink():
            if not stale:
                out["россыпь"].append(p.name)     # свежий файл в корне — нарушение дисциплины
        if stale:
            out["удалить"].append(p.name)
            out["байт"] += size_of(p)
        else:
            out["оставить"].append(p.name)

    if len(out["удалить"]) > MAX_DELETE:
        out["потолок"] = True
        out["удалить"] = out["удалить"][:MAX_DELETE]
    return out


LOOSE_TIDY_HOURS = 24    # через столько часов файл из корня переезжает в каталог по дате


def tidy(now: float | None = None, keep_days: int = KEEP_DAYS) -> list[str]:
    """Собрать россыпь из корня в каталог `<дата>-rossyp` и вернуть, что переехало.

    Зачем переезд, а не удаление: файл, брошенный в корень вчера, — такой же
    одноразовый мусор, но его срок ещё не вышел, и сносить его рано. Зато корень
    обязан оставаться списком дат: пока в нём три десятка `bl096_probe.py`,
    глазами там не найти ничего.

    Сегодняшнее не трогается вовсе — работа идущего хода должна лежать там, куда
    её положили. Время правки при переезде сохраняется, и каталог получает время
    самого свежего файла внутри: иначе переезд обнулил бы трёхдневный счётчик.
    """
    now = now if now is not None else time.time()
    edge = now - LOOSE_TIDY_HOURS * 3600
    base = root()
    moved: list[str] = []
    if not base.is_dir() or base.is_symlink():
        return moved

    touched: dict[Path, float] = {}
    for p in sorted(base.iterdir()):
        if not _inside(p) or (p.is_dir() and not p.is_symlink()):
            continue
        mt = newest_mtime(p)
        if mt >= edge or mt < now - keep_days * 86400:
            continue        # либо ещё сегодняшнее, либо и так уходит следом — не возить зря
        day = datetime.fromtimestamp(mt, ZoneInfo(C.TZ)).date().isoformat()
        dst_dir = base / f"{day}-rossyp"
        dst_dir.mkdir(exist_ok=True)
        dst = dst_dir / p.name
        n = 2
        while dst.exists() or dst.is_symlink():             # тёзка из прошлого переезда
            dst = dst_dir / f"{p.stem}-{n}{p.suffix}"
            n += 1
        try:
            os.rename(p, dst)                               # в одной зоне, ссылки не рвём
        except OSError:
            continue
        os.utime(dst, (mt, mt), follow_symlinks=False)
        moved.append(f"{p.name} → {dst_dir.name}")
        touched[dst_dir] = max(touched.get(dst_dir, 0), mt)

    for d, mt in touched.items():
        try:
            os.utime(d, (mt, mt))                           # не обнулять срок каталога
        except OSError:
            pass
    return moved


def _remove(p: Path) -> None:
    """Снять запись. Ссылка снимается как ссылка, каталог — деревом без ссылок."""
    if p.is_symlink() or not p.is_dir():
        p.unlink()
        return
    shutil.rmtree(p)                              # rmtree не идёт по ссылкам-каталогам


def prune(dry: bool = False, now: float | None = None,
          keep_days: int = KEEP_DAYS) -> dict:
    """Убрать протухшее. Возвращает отчёт; в лог кладёт строку scratch_prune."""
    moved = [] if dry else tidy(now=now, keep_days=keep_days)
    p = plan(now=now, keep_days=keep_days)
    removed, failed = [], []
    if not dry:
        for name in p["удалить"]:
            path = root() / name
            if not _inside(path):                 # повторная сверка перед самим удалением
                failed.append(f"{name}: вне зоны")
                continue
            try:
                _remove(path)
                removed.append(name)
            except OSError as e:
                failed.append(f"{name}: {type(e).__name__}")
    report = {
        "удалено": len(removed) if not dry else 0,
        "список": p["удалить"] if dry else removed,
        "кб": round(p["байт"] / 1024),
        "осталось": len(p["оставить"]),
        "убрано_из_корня": moved or None,
        "россыпь_в_корне": p["россыпь"] or None,
        "не_вышло": failed or None,
        "потолок": p["потолок"] or None,
        "всухую": dry or None,
    }
    M.log("self", {"scratch_prune": {k: v for k, v in report.items() if v not in (None, 0, [])}
                   or {"удалено": 0}})
    return report


def main(argv: list[str] | None = None) -> int:
    import json
    import sys
    argv = argv if argv is not None else sys.argv[1:]
    dry = "--yes" not in argv
    rep = prune(dry=dry)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    if dry:
        print("\nэто сухой прогон; убрать — тот же вызов с --yes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
