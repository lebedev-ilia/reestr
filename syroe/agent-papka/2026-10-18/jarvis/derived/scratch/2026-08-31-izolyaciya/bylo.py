"""Что из тех же приёмов проходило ДО правки: новая проверка отключается.

Нужно по шагу 5 процедуры pravka-yadra: проверка, которая не краснеет ни на
чём, — самообман. Здесь видно, что краснеет она ровно на том, что чинилось.
"""
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from jarvis.core import agent as A  # noqa: E402

A._project_bash_verdict = lambda cmd, own: None       # как было до правки
runpy.run_path(str(Path(__file__).with_name("vzlom.py")), run_name="__main__")
