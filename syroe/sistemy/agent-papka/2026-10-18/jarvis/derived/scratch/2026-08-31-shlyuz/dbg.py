import os
os.environ.setdefault("JARVIS_SELFTEST", "1")
from jarvis.core import agent as A
from jarvis.core import config as C

for cmd in ('python3 -c "$(cat body.py)"',
            'python3 -c "$CODE"',
            'python3 -c `cat body.py`'):
    body, hd = A._cut_heredocs(cmd)
    print(repr(cmd))
    print("  сегменты:", [[t for k, t in seg if k == 'w']
                          for _s, seg in A._segments(A._words(body))])
    print("  _root_write_danger:", A._root_write_danger(cmd, C.ROOT))
    print("  classify:", A.classify("Bash", {"command": cmd}))
