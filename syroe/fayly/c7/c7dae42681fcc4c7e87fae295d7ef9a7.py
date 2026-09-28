import pathlib

p = pathlib.Path("/home/jarvis/agent/jarvis/modules/svodki/store.py")
t = p.read_text(encoding="utf-8")
n = t.count("io.open(")
t = t.replace("io.open(", "open(")
p.write_text(t, encoding="utf-8")
print("заменено мест:", n)

import py_compile
py_compile.compile(str(p), doraise=True)
print("файл разбирается без ошибок")
