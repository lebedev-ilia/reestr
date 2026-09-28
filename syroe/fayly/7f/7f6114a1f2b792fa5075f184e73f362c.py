import io

p = "/home/jarvis/agent/jarvis/core/config.py"
t = io.open(p, encoding="utf-8").read()

old_line = 'RASHOD_POTOLOK_USD = opt("JARVIS_RASHOD_POTOLOK", 200.0, float)'
new_line = 'RASHOD_POTOLOK_USD = opt("JARVIS_RASHOD_POTOLOK", 300.0, float)'

old_note = '# в поезд: «потолок 200». Повод — 13.09 вышло $207 за день, а подписка'
new_note = ('# в поезд: «потолок 200». Поднят до 300 по его же слову 15.09.2026 из\n'
            '# поезда («повышай до 300 потолок») — за 15.09 система выбрала $175 к\n'
            '# 20:37 и упиралась в стену до полуночи. Повод для самого потолка —\n'
            '# 13.09 вышло $207 за день, а подписка')

assert old_line in t, "строка потолка не найдена — ничего не менял"
assert old_note in t, "комментарий не найден — ничего не менял"

t = t.replace(old_note, new_note, 1).replace(old_line, new_line, 1)
io.open(p, "w", encoding="utf-8").write(t)
print("потолок поднят до 300")
