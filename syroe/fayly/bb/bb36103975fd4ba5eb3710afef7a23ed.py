import pathlib

p = pathlib.Path("/home/jarvis/agent/jarvis/modules/telegram/read.py")
t = p.read_text(encoding="utf-8")

old = "    await client.start(phone=phone)"
new = '''    # Облачный пароль двухэтапной проверки. Telegram спрашивает его ПОСЛЕ кода,
    # и это отдельный пароль, не от аккаунта: Настройки -> Конфиденциальность ->
    # Двухэтапная аутентификация. 16.09.2026 Илья трижды ввёл его вслепую через
    # удалённый терминал и трижды получил отказ. Печать вслепую по ssh — частая
    # причина ложного «пароль неверный»: спецзнаки и раскладка искажаются по
    # дороге. Поэтому пароль можно заранее положить в .env.server как
    # TG_PASSWORD (руками, мимо чата) — тогда терминал не спросит ничего, кроме
    # кода. Нет в настройках — работает как раньше, спросит вводом.
    пароль = os.environ.get("TG_PASSWORD") or None
    if пароль:
        await client.start(phone=phone, password=пароль)
    else:
        await client.start(phone=phone)'''

assert old in t, "точка вставки не найдена — ничего не менял"
p.write_text(t.replace(old, new, 1), encoding="utf-8")
print("вход научился брать облачный пароль из настроек")

import py_compile
py_compile.compile(str(p), doraise=True)
print("файл разбирается без ошибок")

# Убираем полуготовый файл сессии от неудачной попытки
s = pathlib.Path("/home/jarvis/agent/jarvis/memory/telegram/session.session")
if s.exists():
    s.unlink()
    print("недоделанный файл сессии убран — следующая попытка начнётся с чистого")
