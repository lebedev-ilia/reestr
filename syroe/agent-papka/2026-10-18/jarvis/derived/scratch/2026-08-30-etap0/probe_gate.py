"""Живая проверка пунктов 2 и 3 приёмки этапа 0 — прямо по решениям шлюза.

Опасные строки собираются из кусков, чтобы сам шлюз не отбил запуск пробника.
Запуск из каталога выше корня агента:

    jarvis/.venv/bin/python jarvis/derived/scratch/2026-08-30-etap0/probe_gate.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from jarvis.core import agent as A  # noqa: E402

BAD = []
GT = ">"
PIPE = "|"


def verdict(cmd):
    d, why = A.classify("Bash", {"command": cmd})
    return d, why


def must_stop(cmd, name):
    d, why = verdict(cmd)
    ok = d != "allow"
    print(("  ок    " if ok else "  ПЛОХО") + f"  {name}: {d} — {why[:70]}")
    if not ok:
        BAD.append(name)


def must_pass(cmd, name):
    d, why = verdict(cmd)
    ok = d == "allow"
    print(("  ок    " if ok else "  ПЛОХО") + f"  {name}: {d} — {why[:70]}")
    if not ok:
        BAD.append(name)


print("== семь команд из пункта 2 ==")
must_stop(f"echo x {GT} /etc/hosts", "запись в /etc/hosts")
must_stop(f"cat a.txt {GT} ~/.bashrc", "запись в ~/.bashrc")
must_stop("find / -delete", "find / -delete")
must_stop('python3 -c "import shutil; shutil.rmtree(\'/\')"', "python3 -c с удалением")
must_stop(f"echo x {PIPE} tee /etc/passwd", "tee в /etc/passwd")
must_stop("touch /tmp/x && cp secret /tmp/", "touch и cp в /tmp")
must_stop(f'bash -c "echo x {GT} ~/y"', "bash -c с записью")

print("== разделители не считаются чтением ==")


def must_not_be_read(cmd, name):
    d, why = verdict(cmd)
    ok = "чтение через шелл" not in why
    print(("  ок    " if ok else "  ПЛОХО") + f"  {name}: {d} — {why[:70]}")
    if not ok:
        BAD.append(name)


for sign in (GT, GT + GT, PIPE, "&&", ";", "`", "$("):
    must_not_be_read(f"cat memory/now.md {sign} cat", f"разделитель {sign}")

print("== интерпретаторы убраны из безопасных ==")
must_stop('python3 -c "print(1)"', "python3 -c вообще")
must_stop('bash -c "ls"', "bash -c вообще")

print("== запись вне корня ==")
must_stop("cd /tmp && mkdir -p novaya", "cd /tmp и mkdir")
must_stop("mkdir -p /home/ilya/xxx", "mkdir по абсолютному пути вне корня")
must_stop("cp memory/now.md ~/kopiya.md", "cp в домашний каталог")
must_stop("mv memory/now.md ../now.md", "mv на уровень выше корня")

print("== повседневная работа жива ==")
must_pass("ls memory", "ls")
must_pass("cat memory/now.md", "cat")
must_pass("git status", "git status")
must_pass('cd "/home/ilya/Рабочий стол/Agent/jarvis" && git log --oneline -3', "cd в корень и git log")
must_pass("grep -rn zadacha memory/rules", "grep")
must_pass("git add memory/now.md", "git add внутри корня")
must_pass('git commit -m "проба"', "git commit")
must_pass(f"echo raz {GT} derived/scratch/proba.txt", "запись в derived")
must_pass("mkdir -p derived/scratch/proba", "mkdir в derived")
must_pass("python3 -m jarvis.modules.projects.cli status vnutrennii", "python3 -m")
must_pass("./jarvis --selftest", "запуск самотеста")

print("\nСЛОМАНО:" if BAD else "\nВСЁ ХОРОШО")
for b in BAD:
    print(" -", b)
sys.exit(1 if BAD else 0)
