"""Самотест файлового индекса. Запуск:

    cd "…/Agent" && jarvis/.venv/bin/python -m jarvis.modules.fs.selftest

Живого индекса и живой памяти не касается вовсе: дерево-образец и база строятся
в `derived/scratch/fs-selftest-<pid>/` и там же умирают (правила
`odnorazovye-proverki-v-derived`, `samotest-ne-pishet-v-zhivuyu-pamyat`).
Модуль `fs` в память не пишет ни строки — подменять `M.log` не приходится.
"""

import builtins
import contextlib
import io
import os
import shutil
import time
from pathlib import Path

from jarvis.core import config as C
from jarvis.modules.fs import index as I
from jarvis.modules.fs import scan

fails: list[str] = []


def check(name: str, ok: bool) -> None:
    print(("  ок   " if ok else "  ПЛОХО") + f"  {name}")
    if not ok:
        fails.append(name)


# --------------------------------------------------------------------------
# Дерево-образец: по одному представителю каждого правила отсева и каждой метки
# --------------------------------------------------------------------------

def make_tree(root: Path) -> None:
    def f(rel: str, size: int = 10, age_days: float = 0) -> Path:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
        when = time.time() - age_days * 86400
        os.utime(p, (when, when))
        return p

    f("Документы/отчет_2024.pdf", 2000, 3)
    f("Документы/Отчёт Большой.docx", 3 << 20, 400)
    f("Документы/вложенная/заметка.txt", 100, 1)
    f("Проект/main.py", 500, 2)
    f("Проект/README.md", 300, 2)
    f("фото.JPG", 5 << 20, 10)

    # шум: ни одна из этих строк не должна оказаться в индексе
    f("Проект/node_modules/left-pad/index.js", 50)
    f("Проект/.git/config", 50)
    f("Проект/__pycache__/main.cpython-314.pyc", 50)
    f("Проект/.venv/pyvenv.cfg", 50)
    f("Проект/.venv/lib/python3.14/os.py", 50)
    f(".cache/thumbnails/big.bin", 50)
    # окружение с произвольным именем — ловится маркером, а не именем
    f("Проект/своё-окружение/pyvenv.cfg", 50)
    f("Проект/своё-окружение/bin/python", 50)

    # личное: путь в индексе есть, содержимое не читается никогда
    f(".ssh/id_rsa", 100)
    f("Проект/.env", 100)
    f("Проект/certs/server.pem", 100)
    f(".mozilla/firefox/profile/cookies.sqlite", 100)


EXPECTED_FILES = {
    "отчет_2024.pdf", "Отчёт Большой.docx", "заметка.txt", "main.py",
    "README.md", "фото.JPG", "pyvenv.cfg", "id_rsa", ".env", "server.pem",
    "cookies.sqlite",
}


def paths(con, sql="SELECT path FROM files WHERE kind='f'", args=()) -> set[str]:
    return {r[0] for r in con.execute(sql, args)}


# --------------------------------------------------------------------------

def test_scan_rules() -> None:
    print("\nПРАВИЛА ОТСЕВА И МЕТОК")
    real, prune = scan.classify([
        ("/", "ext4"), ("/proc", "proc"), ("/tmp", "tmpfs"),
        ("/snap/firefox/1", "squashfs"), ("/var/lib/docker/x", "overlay"),
        ("/run/media/ilya/Новый том", "fuseblk")])
    check("настоящие ФС становятся корнями",
          real == ["/", "/run/media/ilya/Новый том"])
    check("виртуальные ФС и снапы отсекаются",
          {"/proc", "/snap/firefox/1", "/var/lib/docker/x"} <= prune)
    check("жёсткий список отсева на месте", {"/proc", "/sys", "/dev", "/run"} <= prune)
    check("диск отсекается по точному пути, а не по префиксу /run",
          "/run" in prune and "/run/media/ilya/Новый том" not in prune)
    check("настоящие корни машины найдены: система и всё смонтированное",
          "/" in scan.roots_and_prune()[0])

    check("ключ помечается личным", scan.is_secret("/home/i/a.pem", "a.pem", "pem"))
    check(".env помечается личным", scan.is_secret("/home/i/.env", ".env", ""))
    check("всё в .ssh личное",
          scan.is_secret("/home/i/.ssh/config", "config", ""))
    check("профиль браузера личный",
          scan.is_secret("/home/i/.mozilla/firefox/p/cookies.sqlite",
                         "cookies.sqlite", "sqlite"))
    check("обычный документ не личный",
          not scan.is_secret("/home/i/Документы/отчет.pdf", "отчет.pdf", "pdf"))
    check("расширение берётся последнее и в нижнем регистре",
          scan.split_ext("Отчёт.Итог.PDF") == "pdf")
    check("точка в начале имени расширением не считается",
          scan.split_ext(".env") == "")


def test_build(tree: Path, db: Path) -> None:
    print("\nСБОРКА")
    # Настоящая проверка запрета на чтение: на время сборки открыть файл
    # питоном физически нельзя. Так проверяется обещание из факта
    # pc-dostup-ustroystvo, а не намерение автора.
    real_open = builtins.open
    builtins.open = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("модуль fs попытался прочитать файл"))
    try:
        st = I.build(db, [str(tree)], quiet=True)
    finally:
        builtins.open = real_open
    check("сборка обходится без чтения файлов", True)

    con = I.connect(db)
    names = {os.path.basename(p) for p in paths(con)}
    check("в индексе ровно ожидаемые файлы", names == EXPECTED_FILES)
    check("счётчик файлов сходится с таблицей", st["files"] == len(names))
    check("каталоги тоже в индексе", st["dirs"] >= 6)

    all_paths = paths(con, "SELECT path FROM files")
    for noise in ("node_modules", "/.git", "__pycache__", "/.venv", "/.cache"):
        check(f"шум не в индексе: {noise}",
              not any(noise in p for p in all_paths))
    check("окружение по маркеру не развёрнуто",
          any(p.endswith("своё-окружение/pyvenv.cfg") for p in all_paths)
          and not any("своё-окружение/bin" in p for p in all_paths))
    check("сам каталог окружения записан",
          any(p.endswith("своё-окружение") for p in all_paths))
    con.close()


def test_search(tree: Path, db: Path) -> None:
    print("\nПОИСК")
    con = I.connect(db)

    def find(q, **kw):
        kw.setdefault("kind", "f")      # каталоги в индексе тоже есть и тоже ищутся
        rows, _more = I.search(con, q, **kw)
        return {os.path.basename(r[2]) for r in rows}

    check("префикс имени находит", "отчет_2024.pdf" in find("отчет"))
    check("кусок между разделителями — тоже слово", "отчет_2024.pdf" in find("2024"))
    check("регистр не важен", "фото.JPG" in find("ФОТО"))
    check("ё и е не различаются", "Отчёт Большой.docx" in find("отчет"))
    check("подстрока в середине имени", "отчет_2024.pdf" in find("тчет", sub=True))
    check("подстрока сама подхватывается, когда слово не нашлось",
          "отчет_2024.pdf" in find("тчет"))
    check("несуществующее не выдумывается", find("зюзюкатор") == set())

    check("фильтр по расширению", find("", ext="pdf") == {"отчет_2024.pdf"})
    check("расширений можно несколько",
          find("", ext="pdf,md") == {"отчет_2024.pdf", "README.md"})
    check("фильтр по каталогу",
          find("", dir=str(tree / "Документы"))
          == {"отчет_2024.pdf", "Отчёт Большой.docx", "заметка.txt"})
    check("соседний каталог с тем же префиксом не цепляется",
          find("", dir=str(tree / "Документы/вложенная")) == {"заметка.txt"})
    check("фильтр по размеру снизу",
          find("", min_size=1 << 20) == {"Отчёт Большой.docx", "фото.JPG"})
    check("фильтр по размеру сверху", "фото.JPG" not in find("", max_size=1 << 20))
    fresh = find("", newer=I.parse_when("5d"))
    check("фильтр по свежести пускает только свежее",
          {"отчет_2024.pdf", "заметка.txt", "main.py"} <= fresh
          and "Отчёт Большой.docx" not in fresh and "фото.JPG" not in fresh)
    check("фильтр по давности", find("", older=I.parse_when("100d")) == {"Отчёт Большой.docx"})
    check("только каталоги", all(r[3] == "d" for r in I.search(con, "", kind="d")[0]))

    secret = find("", secret=True)
    check("личное помечено", {"id_rsa", ".env", "server.pem", "cookies.sqlite"} <= secret)
    check("документы личными не считаются", "отчет_2024.pdf" not in secret)
    rows, _m = I.search(con, "id_rsa")
    check("выдача предупреждает про личное", "содержимое не открывать" in I.format_rows(rows, False))

    rows, more = I.search(con, "", limit=2)
    check("лимит соблюдается и про остаток сказано", len(rows) == 2 and more)
    check("сортировка по размеру",
          [r[0] for r in I.search(con, "", kind="f", order="size", limit=3)[0]]
          == sorted([r[0] for r in I.search(con, "", kind="f", order="size", limit=3)[0]],
                    reverse=True))

    t0 = time.perf_counter()
    for _ in range(50):
        I.search(con, "отчет")
    check("запрос дешёвый", (time.perf_counter() - t0) / 50 < 0.05)
    con.close()


def test_tree(tree: Path, db: Path) -> None:
    print("\nКАРТА КАТАЛОГОВ")
    con = I.connect(db)
    out = I.tree(con, depth=2, top=8)
    check("корень назван", str(tree) in out)
    check("узлы верхнего уровня на месте", "Документы" in out and "Проект" in out)
    check("второй уровень показан", "вложенная" in out)
    check("глубже второго уровня не спускается", "python3.14" not in out)
    check("узел несёт число файлов и объём", "3.0М" in out or "3,0М" in out or "3М" in out)
    check("карта помещается в контекст", len(out.encode()) < 4000)
    check("глубина 1 короче глубины 2",
          len(I.tree(con, depth=1, top=8)) < len(out))
    con.close()


def test_update(tree: Path, db: Path) -> None:
    print("\nОБНОВЛЕНИЕ")
    (tree / "Документы" / "новый.txt").write_bytes(b"y" * 42)
    (tree / "Документы" / "отчет_2024.pdf").unlink()
    shutil.rmtree(tree / "Документы" / "вложенная")
    (tree / "Свежий" / "внутри").mkdir(parents=True)
    (tree / "Свежий" / "внутри" / "глубоко.txt").write_bytes(b"z")

    st = I.update(db, [str(tree)], quiet=True)
    con = I.connect(db)
    names = {os.path.basename(p) for p in paths(con)}
    check("новый файл появился", "новый.txt" in names)
    check("удалённый файл исчез", "отчет_2024.pdf" not in names)
    check("удалённое поддерево исчезло целиком", "заметка.txt" not in names)
    check("новое поддерево обойдено вглубь", "глубоко.txt" in names)
    rows, _m = I.search(con, "новый")
    check("полнотекст догнал новый файл", len(rows) == 1)
    rows, _m = I.search(con, "отчет_2024")
    check("полнотекст забыл удалённый файл", rows == [])
    check("нетронутые каталоги не перечитывались",
          st["touched_dirs"] < st["dirs"])
    con.close()

    # Второй прогон без изменений обязан быть пустым: если он всё равно
    # что-то трогает, значит метки времени не сходятся и инкремент бесполезен.
    st2 = I.update(db, [str(tree)], quiet=True)
    check("повторный update ничего не трогает", st2["touched_dirs"] == 0)
    con = I.connect(db)
    check("состав индекса не поехал",
          {os.path.basename(p) for p in paths(con)} == names)
    con.close()


def test_zone(db: Path) -> None:
    print("\nЗОНА")
    check("боевой индекс лежит в derived", I.DB.is_relative_to(C.DERIVED))
    check("самотест боевую базу не трогает", db != I.DB)
    # Пересборка одной командой — условие правила v-derived-tolko-vosstanovimoe:
    # снесли derived, и индекс возвращается `build`. Проверяем, что команды есть
    # в CLI и что он не падает на --help.
    for cmd in ("build", "update", "search", "tree", "stat"):
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                I.main([cmd, "--help"])
            ok = False
        except SystemExit as e:
            ok = e.code == 0
        check(f"команда есть в CLI: {cmd}", ok)


def run() -> int:
    # Громкий отказ вместо тихой записи мимо: если предыдущий набор проверок
    # не вернул C.DERIVED на место (так делал night_selftest до 24.08), песочница
    # уехала бы во временный каталог за пределами папки агента.
    assert C.DERIVED.is_relative_to(C.ROOT), \
        f"derived уехал за пределы папки агента: {C.DERIVED} (чужой самотест не убрал за собой)"
    work = C.DERIVED / "scratch" / f"fs-selftest-{os.getpid()}"
    shutil.rmtree(work, ignore_errors=True)
    tree, db = work / "дерево", work / "index.db"
    try:
        make_tree(tree)
        test_scan_rules()
        test_build(tree, db)
        test_search(tree, db)
        test_tree(tree, db)
        test_update(tree, db)
        test_zone(db)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("\nВСЁ ХОРОШО" if not fails else f"\nСЛОМАНО ({len(fails)}): {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(run())
