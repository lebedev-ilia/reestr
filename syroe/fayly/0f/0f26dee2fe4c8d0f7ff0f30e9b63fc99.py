import pathlib
import re

p = pathlib.Path("/home/jarvis/agent/jarvis/modules/svodki/store.py")
t = p.read_text(encoding="utf-8")

start = t.index("    сделали: list[str] = []")
end = t.index("    return сделали, не_вышло") + len("    return сделали, не_вышло")

new_body = '''    сделали: list[str] = []
    не_вышло: list[str] = []
    закончено = провалено = писем = сообщений = ошибок = 0

    лог = C.ROOT / "memory" / "log" / f"{day}.jsonl"
    метка = f"{day}T{час:02d}:"
    if лог.exists():
        for строка in open(лог, encoding="utf-8", errors="replace"):
            if метка not in строка[:40]:
                continue
            try:
                d = json.loads(строка)
            except Exception:
                continue
            тип = d.get("type")
            содержимое = d.get("content")
            if тип == "error":
                ошибок += 1
                continue
            if тип == "message_out" and d.get("actor") == "agent":
                сообщений += 1
                continue
            if isinstance(содержимое, str) and "job_done" in содержимое:
                try:
                    внутри = json.loads(содержимое)
                except Exception:
                    внутри = {}
                if isinstance(внутри, dict) and "job_done" in внутри:
                    закончено += 1
                    if внутри.get("status") != "done":
                        провалено += 1

    журнал_писем = C.ROOT / "memory" / "outbox" / "sent.jsonl"
    if журнал_писем.exists():
        for строка in open(журнал_писем, encoding="utf-8", errors="replace"):
            if метка in строка[:60]:
                писем += 1

    if закончено:
        слово = _склонение(закончено, "задание", "задания", "заданий")
        сделали.append(f"Команда закончила {закончено} {слово}.")
    if писем:
        слово = _склонение(писем, "письмо", "письма", "писем")
        сделали.append(f"Отправлено {писем} {слово} рассылки.")
    if сообщений:
        слово = _склонение(сообщений, "вопрос", "вопроса", "вопросов")
        сделали.append(f"Тебе ушло {сообщений} {слово}.")

    if провалено:
        слово = _склонение(провалено, "задание", "задания", "заданий")
        не_вышло.append(f"Не удалось довести до конца {провалено} {слово}.")
    if ошибок:
        слово = _склонение(ошибок, "сбой", "сбоя", "сбоев")
        не_вышло.append(f"В работе системы {ошибок} {слово}.")

    return сделали, не_вышло'''

t = t[:start] + new_body + t[end:]

if not re.search(r"^import json$", t, flags=re.M):
    t = t.replace("import json", "import json", 1)

p.write_text(t, encoding="utf-8")
print("разбор переписан на честный")

import py_compile
py_compile.compile(str(p), doraise=True)
print("файл разбирается без ошибок")
