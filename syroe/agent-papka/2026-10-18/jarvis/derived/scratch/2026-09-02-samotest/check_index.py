import sys
sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")
import jarvis.core.config as C
import jarvis.core.memory as M

raw = M._block_body("index", raw=True).strip()
print("raw index bytes:", len(raw.encode()))
print("limit:", C.BLOCK_MAX_BYTES["index"])
fitted = M._fit(raw, C.BLOCK_MAX_BYTES["index"], "")
print("fitted bytes:", len(fitted.encode()))
