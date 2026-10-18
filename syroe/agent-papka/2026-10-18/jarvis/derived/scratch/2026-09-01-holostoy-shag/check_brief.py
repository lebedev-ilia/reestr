import sys
sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")
from jarvis.modules.agents import roles

brief, params = roles.build_brief("critic", "тестовая задача проверки текста брифа", mode="work")
has_note = "Как сдаётся результат" in brief
has_line = "Свой result.md не пиши" in brief
idx = brief.find("Как сдаётся результат")
print("has_note:", has_note, "has_line:", has_line)
print(brief[idx - 5: idx + 420])
