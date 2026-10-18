import sys, time
sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")
from jarvis.core import agent as A

t0 = time.perf_counter()
s = A.build_system()
t1 = time.perf_counter()
print("build_system sec:", round(t1 - t0, 3), "len bytes:", len(s.encode()))

t0 = time.perf_counter()
p = A.write_system_prompt(s)
t1 = time.perf_counter()
print("write_system_prompt sec:", round(t1 - t0, 4))
p.unlink()
