"""Пульт сборщика страницы состояния. Запуск из каталога ВЫШЕ корня агента:

    cd "…/Agent"
    jarvis/.venv/bin/python -m jarvis.modules.status.cli build

Одна подкоманда — build: собирает derived/status/index.html и печатает путь.
Код возврата 1 на любую ошибку сборки, 0 — страница готова (правило
samotest-schitat-po-kodu-vozvrata распространяется и на обычный запуск, не
только на самотест: демон и роли читают именно код возврата).
"""

from __future__ import annotations

import argparse
import sys

from . import build as B


def cmd_build(_args: argparse.Namespace) -> int:
    path = B.build()
    print(str(path))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="status")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="собрать derived/status/index.html")
    args = parser.parse_args(argv)

    if args.cmd == "build":
        return cmd_build(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
