import sys
sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")
from jarvis.core import selftest as S

S.test_task_md_cap()
print("итог fails:", S.fails)
