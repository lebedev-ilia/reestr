# Перестановка разделов свёртки W35 под новый порядок важности (bl-155).
# Содержание не меняется — меняется только очерёдность разделов, потому что
# в промпт теперь едет голова файла, а не файл целиком.
import re
from pathlib import Path

p = Path("/home/ilya/Рабочий стол/Agent/jarvis/derived/rollups/weeks/2026-W35.md")
text = p.read_text(encoding="utf-8").strip()
parts = re.split(r"\n(?=## )", text)
head, secs = parts[0], parts[1:]


def key(s: str) -> int:
    t = s.splitlines()[0]
    for i, mark in enumerate(["Что решено", "Что сломалось", "Что произошло",
                              "Что изменилось", "Чем закончились", "Состояние"]):
        if mark in t:
            return i
    return 99


order = sorted(secs, key=key)
out = "\n".join([head] + order) + "\n"
assert sorted(out.split()) == sorted((text + "\n").split()), "текст изменился, а не переставлен"
p.write_text(out, encoding="utf-8")
print("было", len(text.encode()), "стало", len(out.encode()))
print("порядок:", [s.splitlines()[0] for s in order])
