import sys, itertools
sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")
import jarvis.core.config as C
import jarvis.core.memory as M

FULL = list(C.EVERY_TURN_RULES)
print("current full:", FULL)


def sim(remove):
    saved = C.EVERY_TURN_RULES
    C.EVERY_TURN_RULES = tuple(r for r in FULL if r not in remove)
    try:
        digest = M.rules_digest()
        size = len(digest.encode())
        head, _, folded = digest.partition(M.FOLDED_HEADER)
        n_full = max(1, len([l for l in head.splitlines() if l.startswith("- [")]))
        n_folded = max(1, len([l for l in folded.splitlines() if l.startswith("- [")]))
        full_price = len(head.encode()) // n_full
        folded_price = len(folded.encode()) // n_folded
        free = C.RULES_BLOCK_MAX_BYTES - size
        need = full_price + 3 * folded_price
        return size, free, need, free >= need
    finally:
        C.EVERY_TURN_RULES = saved


candidates = [
    (),
    ("git-stash-zapreshchen",),
    ("promezhutochnye-statusy",),
    ("sekrety-ne-cherez-dialog",),
    ("git-stash-zapreshchen", "promezhutochnye-statusy"),
    ("promezhutochnye-statusy", "sekrety-ne-cherez-dialog"),
    ("git-stash-zapreshchen", "sekrety-ne-cherez-dialog"),
    ("dnyom-rabotayut-podagenty",),
    ("promezhutochnye-statusy", "dnyom-rabotayut-podagenty"),
]
for remove in candidates:
    print(remove, "->", sim(remove))
