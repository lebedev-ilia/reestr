"""Мутационная проба bl-152: ломаем зону наблюдения и смотрим, краснеет ли
самотест. Проверка, которая не краснеет на сломанном коде, ничего не сторожит.
Правило git-stash-zapreshchen: рабочее дерево не трогаем, ломаем в памяти."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from jarvis.core import selftest as ST                     # noqa: E402
from jarvis.modules.agents import schedule as SS           # noqa: E402


def run(name, mutate):
    ST.fails.clear()
    saved = (SS.watch_paths, SS._files_touched_since, SS._commits_since)
    try:
        mutate()
        try:
            ST.test_role_zone()
        except Exception as e:                             # noqa: BLE001
            ST.fails.append(f"упало: {type(e).__name__}: {e}")
    finally:
        SS.watch_paths, SS._files_touched_since, SS._commits_since = saved
    red = [f for f in ST.fails]
    print(f"\n=== МУТАЦИЯ: {name} → красных {len(red)}")
    for f in red:
        print("   ·", f)
    return red


def m_zone_off():
    """Зоны нет: watch_paths всегда отдаёт пустой include (поведение ДО правки)."""
    SS.watch_paths = lambda role, cfg=None: {"include": [], "exclude": []}


def m_exclude_off():
    """exclude игнорируется: свой каталог роли снова отменяет тишину."""
    orig = SS._files_touched_since
    SS._files_touched_since = lambda ts, include=(), exclude=(): orig(ts, include, ())
    origc = SS._commits_since
    SS._commits_since = lambda ts, include=(), exclude=(): origc(ts, include, ())


def m_files_blind():
    """Незакоммиченное не видно: файлы по mtime не считаются."""
    SS._files_touched_since = lambda ts, include=(), exclude=(): 0


a = run("зона выключена (код до правки)", m_zone_off)
b = run("exclude не применяется", m_exclude_off)
c = run("mtime-файлы не считаются", m_files_blind)
print("\nИТОГ: краснеет на всех трёх мутациях:",
      bool(a) and bool(b) and bool(c))
