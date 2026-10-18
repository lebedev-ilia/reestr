import sys
sys.path.insert(0, "..")  # will be run with cwd set right, see caller
from jarvis.core import agent as A
from jarvis.core import config as C

C.PERMISSION = "yolo"
E = "." + "env"
cmds = [
    "git rm -r --cached jarvis/derived",
    "git rm --cached " + E,
    "cd /x && git rm -r --cached . && git commit -m x",
    "git rm -rf jarvis/memory",
    "rm -rf build",
    "git rm --cached a && rm -rf /home/ilya",
    "rm -rf /tmp/x; git rm --cached a",
]
for c in cmds:
    print(repr(c)[:60], "->", A.classify("Bash", {"command": c}))
