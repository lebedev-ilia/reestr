import pathlib

НОВЫЙ = "+79841781810"   # тот же номер Ильи, международный вид — его требует Telegram

for имя in (".env", ".env.server"):
    p = pathlib.Path("/home/jarvis/agent/jarvis") / имя
    строки = p.read_text(encoding="utf-8").splitlines()
    было = None
    out = []
    for s in строки:
        if s.startswith("TG_PHONE="):
            было = len(s.split("=", 1)[1].strip())
            out.append(f"TG_PHONE={НОВЫЙ}")
        else:
            out.append(s)
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    p.chmod(0o600)
    print(f"{имя}: длина была {было}, стала {len(НОВЫЙ)}")
