import sys
from jarvis.core import config as C
C.WEEK_ROLLUP_MAX_BYTES = 30_000          # свёртка заведомо не влезает в потолок блока
from jarvis.core import selftest as S
rc = S.run()
print("RC самотеста на сломанных числах:", rc)
sys.exit(0)
