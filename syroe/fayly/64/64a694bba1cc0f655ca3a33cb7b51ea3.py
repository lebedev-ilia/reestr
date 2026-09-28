import json
import pathlib
import sys

sys.path.insert(0, "/home/jarvis/agent")
from jarvis.modules.svodki import store as S

day = "2026-09-16"
p = pathlib.Path(f"/home/jarvis/agent/jarvis/derived/svodki/{day}.jsonl")
строки = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]

переписано = 0
для_записи = []
for d in строки:
    пусто = d.get("sdelali") in ([S.БЕЗ_ИЗМЕНЕНИЙ], ["Без изменений."])
    if d.get("vid") == "часовая" and пусто:
        час = int(str(d.get("vremya", "00:00")).split(":")[0])
        сделали, не_вышло = S.что_было_за_час(day, час)
        if сделали or не_вышло:
            d["sdelali"] = сделали or [S.БЕЗ_ИЗМЕНЕНИЙ]
            d["ne_poluchilos"] = не_вышло or [S.ПУСТО]
            переписано += 1
    для_записи.append(d)

p.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in для_записи),
             encoding="utf-8")
print("переписано часовых записей:", переписано, "из", len(строки))
print()
for d in для_записи:
    print(d["vremya"], "|", "; ".join(d.get("sdelali", [])),
          ("| не вышло: " + "; ".join(d["ne_poluchilos"]))
          if d.get("ne_poluchilos") and d["ne_poluchilos"] != [S.ПУСТО] else "")
