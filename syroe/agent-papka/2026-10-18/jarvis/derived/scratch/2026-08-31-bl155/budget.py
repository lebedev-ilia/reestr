import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path("/home/ilya/Рабочий стол/Agent")))
from jarvis.core import memory  # noqa: E402

b = memory.context_budget()
print(json.dumps(b, ensure_ascii=False, indent=1)[:4000])

from jarvis.core import memory as M
print("=== блок свёрток целиком:")
print(M._rollups()[-1500:])
