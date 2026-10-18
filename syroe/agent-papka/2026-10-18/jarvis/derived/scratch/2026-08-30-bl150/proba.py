import sys, tempfile
from pathlib import Path
sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")
from jarvis.core import selftest as S

with tempfile.TemporaryDirectory() as d:
    S.test_turn(Path(d) / "turn")
