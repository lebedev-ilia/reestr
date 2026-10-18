import pathlib
root = pathlib.Path(__file__).resolve().parents[3]  # jarvis/
targets = [
    root / "memory" / "projects" / "proba-chuzhoy" / "dossier.md",
    root / ".env",
]
out = root / "derived" / "scratch" / "2026-08-30-bl148" / "read_trick_result.txt"
lines = []
for t in targets:
    lines.append(f"=== {t} ===")
    try:
        data = t.read_text(errors="replace")
        lines.append(data[:400])
    except Exception as e:
        lines.append(f"ERROR: {e!r}")
    lines.append("")
out.write_text("\n".join(lines))
print("done")
