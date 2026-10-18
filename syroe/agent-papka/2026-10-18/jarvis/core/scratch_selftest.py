"""Самотест уборки derived/scratch (core/scratch.py). Запуск:

    cd "…/Agent" && jarvis/.venv/bin/python -m jarvis.core.scratch_selftest

Проверяется не «удаляет ли», а ГРАНИЦА удаления: что уборка не выходит за
свой каталог, не идёт по символическим ссылкам, не сносит свежую работу и не
может вычистить всё разом при скачке часов. Живой derived/scratch на время
проверок подменён временным каталогом, лог — тоже.
"""

import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jarvis.core import config as C
from jarvis.core import memory as M
from jarvis.core import scratch as S

fails: list[str] = []


def check(name: str, ok: bool) -> None:
    print(("  ок   " if ok else "  ПЛОХО") + f"  {name}")
    if not ok:
        fails.append(name)


NOW = 1_800_000_000.0            # фиксированный «сейчас»: проверки не зависят от часов


def age(p: Path, days: float) -> None:
    """Состарить запись (и всё внутри) на days суток относительно NOW."""
    t = NOW - days * 86400
    if p.is_dir() and not p.is_symlink():
        for cur, dirs, files in os.walk(p, topdown=False, followlinks=False):
            for name in files + dirs:
                os.utime(os.path.join(cur, name), (t, t), follow_symlinks=False)
    os.utime(p, (t, t), follow_symlinks=False)


def mkdir(name: str, days: float, files: dict | None = None) -> Path:
    d = S.root() / name
    d.mkdir(parents=True, exist_ok=True)
    for fname, text in (files or {}).items():
        (d / fname).write_text(text, encoding="utf-8")
    age(d, days)
    return d


def mkfile(name: str, days: float, text: str = "x") -> Path:
    p = S.root() / name
    p.write_text(text, encoding="utf-8")
    age(p, days)
    return p


def names(rep_key, rep) -> list:
    return sorted(rep.get(rep_key) or [])


def run() -> int:
    live = (C.DERIVED, C.LOG_DIR)
    live_scratch = sorted(p.name for p in (C.DERIVED / "scratch").iterdir()) \
        if (C.DERIVED / "scratch").is_dir() else None
    tmp = Path(tempfile.mkdtemp(prefix="jarvis-scratch-"))
    try:
        C.DERIVED = tmp / "derived"
        C.LOG_DIR = tmp / "log"
        S.root().mkdir(parents=True, exist_ok=True)
        (C.DERIVED / "rollups").mkdir(parents=True, exist_ok=True)   # сосед по зоне

        # --- возраст считается по самому свежему файлу внутри ----------------
        mkdir("2026-08-20-старый", days=9, files={"a.txt": "мусор"})
        d = mkdir("2026-08-20-дописанный", days=9, files={"a.txt": "мусор"})
        (d / "новое.txt").write_text("свежее", encoding="utf-8")
        age(d / "новое.txt", 0.2)
        mkdir("2026-08-26-свежий", days=0.5, files={"a.txt": "работа"})
        mkdir("пустой-старый", days=9)
        mkfile("старый-файл.txt", days=9)
        mkfile("россыпь.py", days=0.5)

        p = S.plan(now=NOW)
        check("протухший каталог в списке на снос",
              "2026-08-20-старый" in p["удалить"])
        check("каталог со свежим файлом внутри остаётся (возраст по содержимому)",
              "2026-08-20-дописанный" in p["оставить"]
              and "2026-08-20-дописанный" not in p["удалить"])
        check("свежий каталог остаётся", "2026-08-26-свежий" in p["оставить"])
        check("пустой протухший каталог не бессмертен",
              "пустой-старый" in p["удалить"])
        check("протухший файл в корне уходит по тому же правилу",
              "старый-файл.txt" in p["удалить"])
        check("свежий файл в корне записан в россыпь",
              p["россыпь"] == ["россыпь.py"])
        check("свежий каталог россыпью не считается",
              "2026-08-26-свежий" not in p["россыпь"])

        # --- сама граница зоны -------------------------------------------------
        check("свой ребёнок внутри зоны", S._inside(S.root() / "что-то"))
        check("выход вверх по '..' зоной не считается",
              not S._inside(S.root() / ".." / "rollups"))
        check("сосед по derived зоной не считается",
              not S._inside(C.DERIVED / "rollups"))
        check("внук зоной не считается (только прямые дети)",
              not S._inside(S.root() / "2026-08-26-свежий" / "a.txt"))

        # --- сухой прогон ничего не трогает ----------------------------------
        before = sorted(x.name for x in S.root().iterdir())
        rep = S.prune(dry=True, now=NOW)
        check("сухой прогон ничего не удалил",
              sorted(x.name for x in S.root().iterdir()) == before
              and rep["удалено"] == 0 and rep["всухую"] is True)
        check("сухой прогон показал, что ушло бы",
              names("список", rep) == sorted(p["удалить"]))

        # --- настоящая уборка -------------------------------------------------
        rep = S.prune(now=NOW)
        left = sorted(x.name for x in S.root().iterdir())
        check("после уборки протухшего не осталось",
              left == ["2026-08-20-дописанный", "2026-08-26-свежий", "россыпь.py"])
        check("отчёт совпал с делом", rep["удалено"] == 3
              and names("список", rep) == ["2026-08-20-старый", "пустой-старый",
                                           "старый-файл.txt"])
        check("корень зоны цел и не удалён", S.root().is_dir())
        check("соседи по derived не тронуты", (C.DERIVED / "rollups").is_dir())
        check("уборка оставила след в логе",
              any("scratch_prune" in line
                  for f in C.LOG_DIR.glob("*.jsonl")
                  for line in f.read_text(encoding="utf-8").splitlines()))

        # --- россыпь из корня переезжает в каталог по дате ---------------------
        vchera = mkfile("вчерашний.py", days=1.5)
        segodnya = mkfile("сегодняшний.py", days=0.1)
        mkdir("2026-08-26-каталог", days=0.1, files={"a.txt": "работа"})
        day = datetime.fromtimestamp(NOW - 1.5 * 86400,
                                     ZoneInfo(C.TZ)).date().isoformat()
        moved = S.tidy(now=NOW)
        check("вчерашний файл уехал из корня",
              not vchera.exists() and (S.root() / f"{day}-rossyp" / "вчерашний.py").exists()
              and len(moved) == 1)
        check("сегодняшний файл остался на месте", segodnya.exists())
        check("каталоги переезд не трогает", (S.root() / "2026-08-26-каталог").is_dir())
        check("время правки при переезде сохранилось",
              abs((S.root() / f"{day}-rossyp" / "вчерашний.py").stat().st_mtime
                  - (NOW - 1.5 * 86400)) < 2)
        check("каталог россыпи получил время содержимого, а не переезда",
              abs((S.root() / f"{day}-rossyp").stat().st_mtime
                  - (NOW - 1.5 * 86400)) < 2)

        mkfile("вчерашний.py", days=1.5, text="тёзка")
        S.tidy(now=NOW)
        check("тёзка не затёр прежний файл",
              (S.root() / f"{day}-rossyp" / "вчерашний.py").read_text(
                  encoding="utf-8") == "x"
              and (S.root() / f"{day}-rossyp" / "вчерашний-2.py").read_text(
                  encoding="utf-8") == "тёзка")

        for x in (S.root() / f"{day}-rossyp", S.root() / "2026-08-26-каталог"):
            shutil.rmtree(x, ignore_errors=True)
        segodnya.unlink(missing_ok=True)

        # --- ссылки: наружу уборка не выходит ---------------------------------
        outside = tmp / "снаружи"
        outside.mkdir()
        (outside / "важное.txt").write_text("не трогать", encoding="utf-8")
        link = S.root() / "ссылка-наружу"
        link.symlink_to(outside)
        age(link, 9)
        S.prune(now=NOW)
        check("ссылка снята как ссылка, цель снаружи цела",
              not link.exists() and not link.is_symlink()
              and (outside / "важное.txt").exists())

        holder = mkdir("2026-08-20-со-ссылкой", days=9)
        (holder / "внутрь").symlink_to(outside)
        age(holder, 9)
        age(holder / "внутрь", 9)
        S.prune(now=NOW)
        check("каталог со ссылкой удалён, а цель ссылки — нет",
              not holder.exists() and (outside / "важное.txt").exists())

        # --- корень, подменённый ссылкой, зоной уборки не считается ------------
        shutil.rmtree(S.root())
        S.root().symlink_to(outside)
        p = S.plan(now=NOW)
        check("корень-ссылка: план пуст, наружу не пошли",
              p["удалить"] == [] and p["оставить"] == []
              and (outside / "важное.txt").exists())
        S.root().unlink()
        S.root().mkdir()

        # --- потолок на один прогон -------------------------------------------
        for i in range(S.MAX_DELETE + 5):
            mkfile(f"скачок-{i:03d}.tmp", days=400)
        rep = S.prune(now=NOW)
        check("за прогон уходит не больше потолка",
              rep["удалено"] == S.MAX_DELETE and rep["потолок"] is True)
        check("остаток дождётся следующего прогона",
              len(list(S.root().iterdir())) == 5)

        # --- нет каталога — не падаем -----------------------------------------
        shutil.rmtree(S.root())
        rep = S.prune(now=NOW)
        check("снесённая зона не роняет уборку",
              rep["удалено"] == 0 and not rep["не_вышло"])

        # --- будущее время не считается протухшим ------------------------------
        S.root().mkdir(parents=True, exist_ok=True)
        mkfile("из-будущего.txt", days=-30)
        check("запись из будущего остаётся",
              S.plan(now=NOW)["удалить"] == [])
    finally:
        C.DERIVED, C.LOG_DIR = live
        shutil.rmtree(tmp, ignore_errors=True)

    now_scratch = sorted(p.name for p in (C.DERIVED / "scratch").iterdir()) \
        if (C.DERIVED / "scratch").is_dir() else None
    check("живой derived/scratch самотест не тронул", now_scratch == live_scratch)

    print()
    if fails:
        print(f"ПЛОХО: {len(fails)} — " + "; ".join(fails))
        return 1
    print("всё зелёное")
    return 0


main = run

if __name__ == "__main__":
    raise SystemExit(run())
