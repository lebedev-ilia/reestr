import sys
sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")
import jarvis.core.config as C
import jarvis.core.memory as M

digest = M.rules_digest()
size = len(digest.encode())
print("digest size:", size, "limit:", C.RULES_BLOCK_MAX_BYTES)

head, _, folded = digest.partition(M.FOLDED_HEADER)
n_full = max(1, len([l for l in head.splitlines() if l.startswith("- [")]))
n_folded = max(1, len([l for l in folded.splitlines() if l.startswith("- [")]))
full_price = len(head.encode()) // n_full
folded_price = len(folded.encode()) // n_folded
free = C.RULES_BLOCK_MAX_BYTES - size
need = full_price + 3 * folded_price
print(f"full_price={full_price} folded_price={folded_price} free={free} need={need}")

print("\nPer-rule sizes (full tier):")
for block in head.split("\n- [")[1:]:
    rid = block.split("]")[0]
    text = "- [" + block
    print(f"  {rid}: {len(text.encode())} bytes")
