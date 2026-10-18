import sys
sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")
from jarvis.core import selftest as S

S.test_project_scope()
print("СЛОМАНО:", S.BROKEN if hasattr(S, "BROKEN") else "см. вывод выше")
