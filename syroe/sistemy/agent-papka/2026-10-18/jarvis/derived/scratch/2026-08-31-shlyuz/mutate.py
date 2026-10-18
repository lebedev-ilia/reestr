"""Мутационная проба: ломаем проверяемое и смотрим, что покраснело."""
import io
import os
import contextlib
os.environ.setdefault("JARVIS_SELFTEST", "1")
from jarvis.core import agent as A          # noqa: E402
from jarvis.core import selftest as S       # noqa: E402


def run(label, mutate, restore):
    S.fails.clear()
    mutate()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            S.test_gate_chain()
    finally:
        restore()
    print(f"\n=== {label} ===")
    for f in S.fails:
        print("  ПОКРАСНЕЛО:", f)
    if not S.fails:
        print("  НИЧЕГО НЕ ПОКРАСНЕЛО — проверка не сторожит")
    S.fails.clear()


orig_body = A._root_py_body_danger
orig_mod = A._project_selftest_module
orig_root = A._root_write_danger

run("тело python -c снова не разбирается (возврат к старому отказу)",
    lambda: setattr(A, "_root_py_body_danger",
                    lambda c, r, w: "тело не разбирается"),
    lambda: setattr(A, "_root_py_body_danger", orig_body))

run("разбор тела ослаблен до «всё можно»",
    lambda: setattr(A, "_root_py_body_danger", lambda c, r, w: None),
    lambda: setattr(A, "_root_py_body_danger", orig_body))

run("модуль самотеста не резолвится (возврат к старому отказу)",
    lambda: setattr(A, "_project_selftest_module", lambda mod: None),
    lambda: setattr(A, "_project_selftest_module", orig_mod))

run("любое имя считается самотестом (послабление шире нужного)",
    lambda: setattr(A, "_project_selftest_module", lambda mod: True),
    lambda: setattr(A, "_project_selftest_module", orig_mod))
