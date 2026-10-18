"""Разовая проверка (не тест, не часть ядра): ломаем outside_project и
смотрим, что test_project_scope() покраснеет ровно на ожидаемых строках.
Живую память не трогает: только вызывает функцию из core.selftest, которая
сама не пишет в память (проверено чтением её тела — она только вызывает
check(), которая копит в модульный список fails).
"""
import sys
sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")

from jarvis.core import agent as A
from jarvis.core import selftest as ST

# Ломаем ровно то, что проверяется: отключаем отказ целиком.
A.outside_project = lambda tool_name, tool_input: None

ST.fails.clear()
ST.test_project_scope()
print("ПОКРАСНЕЛО:", len(ST.fails))
for f in ST.fails:
    print(" -", f)
