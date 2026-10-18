#!/usr/bin/env python3
"""
deploy_yolo_dataset.py — развернуть YOLO-датасет на сервере: загрузить метки, распаковать,
создать симлинки к картинкам /home/cvat_share.

РАЗРЕШЕНО владельцем работ (обучение по ТЗ). Фиксируется в server_changes.md.

Куда: /home/user2/baseline/{waste_up,dumpster}/
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, "/Users/user/Desktop/Agent2/my_agent")
import ssh_srv

REMOTE = "/home/user2/baseline"
LOCAL = Path("/tmp")
FILES = [
    ("waste_up.tar.gz", "waste_up"),
    ("dumpster.tar.gz", "dumpster"),
]
SCRIPT = Path("/Users/user/Desktop/Agent2/my_agent/build_links.py")


def run(c, cmd, timeout=1800):
    _, out, err = c.exec_command(cmd, timeout=timeout)
    o = out.read().decode(errors="replace")
    e = err.read().decode(errors="replace")
    return o, e


def upload_with_retry(sftp, local, remote, tries=5):
    for i in range(tries):
        try:
            sftp.put(local, remote)
            return True
        except Exception as ex:
            print(f"  попытка {i+1} не удалась: {ex}", flush=True)
            time.sleep(10)
    return False


def main():
    c = ssh_srv.connect(key=True)
    run(c, f"mkdir -p {REMOTE}")
    sftp = c.open_sftp()
    try:
        sftp.put(str(SCRIPT), f"{REMOTE}/build_links.py")
        for local, sub in FILES:
            lp = LOCAL / local
            rp = f"{REMOTE}/{local}"
            print(f"загрузка {local} ({lp.stat().st_size/1024:.0f} КБ)...", flush=True)
            if not upload_with_retry(sftp, str(lp), rp):
                print(f"НЕ УДАЛОСЬ загрузить {local}", flush=True)
                continue
            print(f"  распаковка в {REMOTE}/{sub}", flush=True)
            o, e = run(c, f"mkdir -p {REMOTE}/{sub} && tar -xzf {rp} -C {REMOTE}/{sub} && rm -f {rp}")
            if e.strip():
                print("  stderr:", e[:300], flush=True)
    finally:
        sftp.close()

    # симлинки
    for sub, root in [("waste_up", "/home/cvat_share"), ("dumpster", "/home/cvat_share")]:
        manifest = f"{REMOTE}/{sub}/manifest.txt"
        outdir = f"{REMOTE}/{sub}"
        print(f"симлинки для {sub}...", flush=True)
        o, e = run(c, f"/usr/bin/python3 {REMOTE}/build_links.py --manifest {manifest} "
                       f"--root {root} --out {outdir}", timeout=3600)
        print(o, flush=True)
        if e.strip():
            print("  stderr:", e[:300], flush=True)

    # проверка
    o, e = run(c, f"for s in waste_up dumpster; do echo \"=== $s ===\"; "
                   f"echo -n 'labels: '; find {REMOTE}/$s/labels -name '*.txt' | wc -l; "
                   f"echo -n 'symlinks: '; find {REMOTE}/$s/images -type l | wc -l; done")
    print(o, flush=True)
    c.close()
    print("ГОТОВО", flush=True)


if __name__ == "__main__":
    main()
