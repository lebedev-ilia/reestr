import sys, os
sys.path.insert(0, os.path.expanduser("~/Рабочий стол/Agent"))
from jarvis.core import claims as CL

texts = [
    "Собрать контакты автосервисов Владивостока: имя, сайт, почта, телефон, район города",
    "Нужны контакты автосервисов Владивостока — имена, сайты, почты, телефоны и районы города",
    "Разобрать банковскую выписку Сбера за август по категориям трат и крупным переводам",
    "critic work: проверь вчерашний след работы",
    "teamlead-vnutrennii work по расписанию 490000",
]
for t in texts:
    k = CL._key(t)
    print(len(k), sorted(k))
