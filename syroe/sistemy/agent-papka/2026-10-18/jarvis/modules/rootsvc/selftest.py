"""Самотест посредника от root. Запуск:

    cd "…/Agent" && jarvis/.venv/bin/python -m jarvis.modules.rootsvc.selftest

Root не нужен и не берётся: демон гоняется в тестовом режиме (euid != 0),
очередь, белый список и журнал — во временном каталоге
`derived/scratch/rootsvc-selftest-<pid>/`, живых `/opt`, `/run` и
`/var/log` тест не касается (правила `odnorazovye-proverki-v-derived`,
`samotest-ne-pishet-v-zhivuyu-pamyat`).

Проверяются ОБЕ стороны запрета (правило `granicu-proveryaet-kod`): что
разрешённое проходит и что запрещённое не проходит. Односторонняя проверка
здесь бесполезна — «ничего не выполняется» тоже сдаст такой тест.
"""

import json
import os
import shutil
import threading
import time
from pathlib import Path

from jarvis.modules.rootsvc import daemon as D

fails: list[str] = []


def check(name: str, ok: bool) -> None:
    print(("  ок   " if ok else "  ПЛОХО") + f"  {name}")
    if not ok:
        fails.append(name)


REPO = Path(__file__).resolve().parents[2]
BOX = REPO / "derived" / "scratch" / f"rootsvc-selftest-{os.getpid()}"

# Белый список для теста: те же формы, что в живом (одиночный параметр,
# список, плейсхолдер внутри строки, шаг с confirm), но команды безобидные.
TEST_ALLOW = {
    "ops": {
        "say": {"desc": "эхо", "confirm": False,
                "params": {"word": "^[a-z]{1,10}$"},
                "steps": [["/bin/echo", "{word}"]]},
        "say.many": {"desc": "эхо списком", "confirm": False,
                     "params": {"words*": "^[a-z]{1,10}$"},
                     "steps": [["/bin/echo", "{words*}"]]},
        "say.path": {"desc": "плейсхолдер внутри строки", "confirm": False,
                     "params": {"name": "^[A-Za-z0-9._-]{1,20}$"},
                     "steps": [["/bin/echo", "/etc/sudoers.d/{name}"]]},
        "danger": {"desc": "требует согласия", "confirm": True,
                   "params": {"word": "^[a-z]{1,10}$"},
                   "steps": [["/bin/echo", "{word}"]]},
        "two": {"desc": "два шага, второй падает", "confirm": False,
                "params": {},
                "steps": [["/bin/echo", "первый"], ["/bin/false"]]},
    }
}


def put(svc: D.Service, body, name: str = None) -> str:
    """Кладёт заявку так же, как это делает клиент: временное имя + rename."""
    rid = name or f"r{os.urandom(3).hex()}"
    raw = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    tmp = svc.req_dir / f".{rid}.tmp"
    tmp.write_text(raw, encoding="utf-8")
    tmp.rename(svc.req_dir / f"{rid}.json")
    return rid


def ask(svc: D.Service, body) -> dict:
    rid = put(svc, body)
    svc.tick()
    return json.loads((svc.resp_dir / f"{rid}.json").read_text(encoding="utf-8"))


def main() -> int:
    if BOX.exists():
        shutil.rmtree(BOX)
    BOX.mkdir(parents=True)
    allow = BOX / "allow.json"
    allow.write_text(json.dumps(TEST_ALLOW, ensure_ascii=False), encoding="utf-8")
    os.environ["JARVIS_ROOT_QUEUE"] = str(BOX / "queue")
    os.environ["JARVIS_ROOT_ALLOW"] = str(allow)
    os.environ["JARVIS_ROOT_LOG"] = str(BOX / "journal.jsonl")

    print("\n— посредник от root —")
    svc = D.Service(uid=os.getuid(), test_mode=True)
    svc.prepare()

    # --- разрешённое проходит ------------------------------------------
    r = ask(svc, {"op": "say", "args": {"word": "привет"}})
    check("параметр не по регулярке — отказ", bool(r.get("rejected")))
    r = ask(svc, {"op": "say", "args": {"word": "ok"}})
    check("разрешённая операция выполняется", r["ok"] and r["stdout"].strip() == "ok")
    r = ask(svc, {"op": "say.many", "args": {"words": ["aa", "bb"]}})
    check("список параметров разворачивается", r["ok"] and r["stdout"].strip() == "aa bb")
    r = ask(svc, {"op": "say.path", "args": {"name": "fetcher-watchdog"}})
    check("плейсхолдер внутри строки собирается",
          r["ok"] and r["stdout"].strip() == "/etc/sudoers.d/fetcher-watchdog")
    r = ask(svc, {"op": "danger", "args": {"word": "ok"}, "confirm": True})
    check("операция с confirm при согласии выполняется", r["ok"])

    # --- запрещённое не проходит ---------------------------------------
    r = ask(svc, {"op": "danger", "args": {"word": "ok"}})
    check("операция с confirm без согласия — отказ",
          bool(r.get("rejected")) and "confirm" in r["rejected"])
    r = ask(svc, {"op": "shell", "args": {}})
    check("операция не из белого списка — отказ", bool(r.get("rejected")))
    r = ask(svc, {"op": "say", "args": {"word": "ok; rm -rf /"}})
    check("инъекция шелла в параметре — отказ", bool(r.get("rejected")))
    r = ask(svc, {"op": "say", "args": {"word": "ok", "lишний": "1"}})
    check("лишний параметр — отказ", bool(r.get("rejected")))
    r = ask(svc, {"op": "say", "args": {}})
    check("нет обязательного параметра — отказ", bool(r.get("rejected")))
    r = ask(svc, {"op": "say.many", "args": {"words": ["ok", "a b"]}})
    check("плохой элемент списка — отказ всей заявки", bool(r.get("rejected")))
    r = ask(svc, {"op": "say.many", "args": {"words": "ok"}})
    check("вместо списка строка — отказ", bool(r.get("rejected")))
    r = ask(svc, {"op": "say.path", "args": {"name": "../shadow"}})
    check("выход из каталога в имени — отказ", bool(r.get("rejected")))
    r = ask(svc, {"op": "say", "args": {"word": "ok"}, "confirm": "да"})
    check("confirm не булев — операции без confirm это не мешает", r["ok"])
    r = ask(svc, "не json вовсе")
    check("заявка не json — отказ", bool(r.get("rejected")))
    r = ask(svc, ["список", "а не объект"])
    check("заявка не объект — отказ", bool(r.get("rejected")))
    r = ask(svc, {"op": "say", "args": {"word": "a" * (D.MAX_REQ_BYTES + 100)}})
    check("заявка больше потолка — отказ", bool(r.get("rejected")))
    r = ask(svc, {"op": "say", "args": "строка"})
    check("args не объект — отказ", bool(r.get("rejected")))

    # --- поведение демона ----------------------------------------------
    r = ask(svc, {"op": "two", "args": {}})
    check("упавший шаг останавливает операцию", not r["ok"] and r["code"] != 0)
    check("вывод первого шага сохранён", "первый" in r["stdout"])
    check("заявки после обработки не остаются", not list(svc.req_dir.glob("*.json")))

    link = svc.req_dir / "symlink.json"
    link.symlink_to("/etc/shadow")
    svc.tick()
    body = json.loads((svc.resp_dir / "symlink.json").read_text(encoding="utf-8"))
    check("симлинк вместо заявки — отказ, файл не читается",
          bool(body.get("rejected")) and "root" not in body.get("stdout", ""))

    rows = [json.loads(x) for x in
            (BOX / "journal.jsonl").read_text(encoding="utf-8").splitlines() if x]
    # Строк в журнале ровно столько, сколько заявок дошло до ответа: молча
    # проглоченная заявка — худший из отказов, её не видно ни там, ни там.
    check("журнал пишет каждую заявку",
          len(rows) == len(list(svc.resp_dir.glob("*.json"))))
    check("в журнале есть и отказы, и выполненное",
          any(r["verdict"] == "выполнено" for r in rows)
          and any(r["verdict"] != "выполнено" for r in rows))

    # --- клиент целиком --------------------------------------------------
    from jarvis.modules.rootsvc import client as CL
    CL.QUEUE = Path(os.environ["JARVIS_ROOT_QUEUE"])
    CL.ALLOW = allow
    out: dict = {}
    t = threading.Thread(target=lambda: out.update(CL.call("say", word="ok", timeout=10)))
    t.start()
    for _ in range(50):
        if svc.tick():
            break
        time.sleep(0.1)
    t.join(timeout=10)
    check("клиент кладёт заявку и получает ответ", out.get("ok") is True)
    check("клиент прибирает за собой ответ", not list(svc.resp_dir.glob("*-*.json")))
    check("клиент видит белый список", "say" in CL.ops())

    # --- живой белый список ---------------------------------------------
    real = D.load_allow(Path(__file__).with_name("allow.json"))
    check("живой белый список загружается", len(real) >= 7)
    argv = D.build_argv(real["sudoers.remove"], {"name": "fetcher-watchdog"})
    check("живая операция собирает верный argv",
          argv == [["/usr/bin/rm", "-f", "--", "/etc/sudoers.d/fetcher-watchdog"]])
    for bad in ("/dev/sdb1 --force", "/dev/sdb1;ls", "../../dev/sdb1", "/dev/mapper/x"):
        try:
            D.build_argv(real["disk.mount"], {"device": bad})
            check(f"устройство {bad!r} должно отклоняться", False)
        except D.Reject:
            pass
    check("устройства мимо шаблона отклоняются", True)

    # Регулярка `^/(etc|proc|sys|var/log)/[A-Za-z0-9._/-]+$` пропускает `..`,
    # то есть чтение чего угодно от root. Ловится не ею, а общим запретом в
    # build_argv — проверяем именно живой список, а не тестовый.
    for op, bad in (("sys.read", "/etc/../root/.ssh/id_rsa"),
                    ("sys.read", "/proc/../etc/shadow"),
                    ("sys.list", "/etc/../root"),
                    ("sys.read", "/etc/ssh/../../home/ilya/.env")):
        try:
            D.build_argv(real[op], {"path": bad})
            check(f"{op} {bad!r} должно отклоняться", False)
        except D.Reject:
            pass
    check("выход из каталога в живых путях отклоняется", True)
    check("обычный системный путь при этом проходит",
          D.build_argv(real["sys.read"], {"path": "/etc/fstab"})
          == [["/usr/bin/cat", "/etc/fstab"]])

    # Кривой путь бинарника виден только в бою и только когда операция уже
    # нужна: /usr/sbin/ntfsfix на этой машине нет, есть /usr/bin/ntfsfix.
    missing = [step[0] for spec in real.values() for step in spec["steps"]
               if not os.access(step[0], os.X_OK)]
    check(f"все бинарники живого списка на месте (нет: {missing})", not missing)

    check("опасные операции живого списка помечены confirm",
          all(real[o]["confirm"] for o in
              ("pkg.install", "sudoers.remove", "svc.disable", "disk.fix_ntfs")))

    shutil.rmtree(BOX, ignore_errors=True)
    print(f"\nитог: {'все проверки прошли' if not fails else 'ПЛОХО: ' + '; '.join(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
