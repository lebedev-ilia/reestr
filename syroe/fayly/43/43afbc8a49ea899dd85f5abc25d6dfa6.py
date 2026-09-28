import pathlib

p = pathlib.Path("/home/jarvis/agent/jarvis/modules/telegram/read.py")
t = p.read_text(encoding="utf-8")

маяк = "def _secure_session_file() -> None:"
assert маяк in t, "точка вставки не найдена — ничего не менял"

loader = '''def _podtyanut_nastroyki() -> None:
    """Дочитать ключи из .env, если их нет в окружении процесса.

    Модуль читал только os.environ, а окружение наполняет systemd из
    .env.server — при ручном запуске из терминала его там нет, и вход падал
    с «TG_PHONE не задан в .env», хотя номер в файле стоял. Поймано 16.09.2026
    на живом входе Ильи: ему дали команду, которая не могла сработать.

    Подтягивать оболочкой (`set -a && . .env.server`) нельзя: значения не
    экранированы, в одном из паролей знак `&` — оболочка на нём падает,
    а кусок пароля попадает в сообщение об ошибке. Читаем файл сами, как это
    и записано в core/config.py: «модули, которым секрет нужен, читают его
    САМИ из файла .env корня агента».

    Окружение главнее файла: то, что уже задано, не перетирается.
    """
    from jarvis.core import config as C
    for имя in (".env", ".env.server"):
        файл = C.ROOT / имя
        if not файл.exists():
            continue
        for строка in файл.read_text(encoding="utf-8").splitlines():
            строка = строка.strip()
            if not строка or строка.startswith("#") or "=" not in строка:
                continue
            ключ, _, значение = строка.partition("=")
            ключ = ключ.strip()
            if ключ and not os.environ.get(ключ):
                os.environ[ключ] = значение.strip().strip('"').strip("'")


'''

t = t.replace(маяк, loader + маяк, 1)

# Зовём подтягивание там, где ключи впервые нужны.
t = t.replace('    api_id = os.environ.get("TG_API_ID")',
              '    _podtyanut_nastroyki()\n    api_id = os.environ.get("TG_API_ID")', 1)
t = t.replace('    phone = os.environ.get("TG_PHONE")',
              '    _podtyanut_nastroyki()\n    phone = os.environ.get("TG_PHONE")', 1)

p.write_text(t, encoding="utf-8")
print("скрипт научился читать настройки сам")

import py_compile
py_compile.compile(str(p), doraise=True)
print("файл разбирается без ошибок")
