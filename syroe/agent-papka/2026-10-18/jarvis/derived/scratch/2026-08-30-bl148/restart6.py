"""Снять job-120 (шёл без JARVIS_PROJECT — заход недействителен) и поставить
заход заново с project=vnutrennii."""
import sys
sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")
from jarvis.core import jobs

brief = open("/home/ilya/Рабочий стол/Agent/jarvis/derived/scratch/"
             "2026-08-30-bl148/brief6.md", encoding="utf-8").read()
jid = jobs.submit(brief, "bl-148 шестой заход проверки изоляции", "sonnet",
                  project="vnutrennii", claim_text="bl-148 заход изоляции 6")
print("поставлено:", jid)
print(jobs.read(jid))
