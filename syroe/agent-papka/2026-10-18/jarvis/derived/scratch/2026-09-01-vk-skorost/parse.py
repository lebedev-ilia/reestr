"""
job-190: сборка ходов канала ВК за 30.08-01.09.2026.

Ключевая находка (проверено на данных, отличается от приёма старых скриптов
bl-155/bl-156): поле message_in.turn НЕ равно message_in.ref — оно хранит id
предыдущего турна (контекст прихода сообщения, см. soobshchenie-posredi-hoda),
а не ключ группировки для СВОИХ данных. Все последующие записи (action, self,
message_out, status) турна используют turn == ref того message_in, который их
запустил. Поэтому группировать нужно так:
  - message_in кладём в группу по СВОЕМУ ref
  - все остальные типы кладём в группу по своему turn (это и есть тот ref)
Старые скрипты (zamer.py, moi.py, klass.py) группировали message_in по turn —
это создавало пустышки и теряло привязку actor/channel к своим self-записям.
Проверено: со старой группировкой группы с self(steps) почти никогда не
получают actor вообще (message_in летит в другую, часто "пустую" группу).

VK-ход (реальный, Илья ждёт) = группа, где есть self с полем 'channel'=='vk'
и 'steps' (это ЕДИНСТВЕННОЕ место в коде, где channel="vk" простав­ляется —
modules/vk/daemon.py:796 — job'ы и фон всегда 'self', night/system тоже 'self').
"""
import json
from collections import defaultdict

ROOT = "/home/ilya/Рабочий стол/Agent/jarvis"
DAYS = ["2026-08-30", "2026-08-31", "2026-09-01"]


def load_days(days=DAYS):
    recs = []
    for d in days:
        with open(f"{ROOT}/memory/log/{d}.jsonl", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                r["_day"] = d
                recs.append(r)
    return recs


def build_turns(recs):
    """Группировка турнов по исправленному признаку (см. докстринг модуля)."""
    turns = {}

    def get(tid):
        if tid not in turns:
            turns[tid] = {
                "id": tid, "message_in": None, "actions": [], "selfs": [],
                "message_out": [], "status": [], "ts_first": None, "ts_last": None,
                "day": None,
            }
        return turns[tid]

    for r in recs:
        ts = r.get("ts")
        typ = r.get("type")
        if typ == "message_in":
            tid = r.get("ref")
            if not tid:
                continue
            g = get(tid)
            g["message_in"] = r
            g["ts_first"] = ts
            g["day"] = r.get("_day")
        else:
            tid = r.get("turn")
            if not tid:
                continue
            g = get(tid)
            if g["day"] is None:
                g["day"] = r.get("_day")
            if typ == "action":
                g["actions"].append(r)
            elif typ == "self":
                g["selfs"].append(r)
            elif typ == "message_out":
                g["message_out"].append(r)
            elif typ == "status":
                g["status"].append(r)
            g["ts_last"] = ts
    return turns


def self_json(r):
    try:
        d = json.loads(r.get("content") or "")
    except Exception:
        return None
    return d if isinstance(d, dict) else None


def turn_steps_record(g):
    """Финальная self-запись турна: содержит steps/sec/channel/auto_status."""
    for r in g["selfs"]:
        d = self_json(r)
        if d and "steps" in d and "limit" not in d:
            return d
    return None


def turn_result_record(g):
    """Запись self с result/tokens/cost_usd (итог хода Claude Agent SDK)."""
    for r in g["selfs"]:
        d = self_json(r)
        if d and "result" in d and "tokens" in d:
            return d
    return None


def is_vk_turn(g):
    st = turn_steps_record(g)
    return bool(st and st.get("channel") == "vk")


def is_job_turn(g):
    for r in g["selfs"]:
        d = self_json(r)
        if d and any(k.startswith("job_") for k in d.keys()):
            return True
    return False


if __name__ == "__main__":
    recs = load_days()
    turns = build_turns(recs)
    vk = [g for g in turns.values() if is_vk_turn(g)]
    job = [g for g in turns.values() if is_job_turn(g)]
    print(f"всего групп-турнов: {len(turns)}")
    print(f"VK-ходов (channel=vk): {len(vk)}")
    print(f"job-турнов (есть job_* в self): {len(job)}")
    have_mi = sum(1 for g in vk if g["message_in"])
    print(f"VK-ходов с привязанным message_in: {have_mi} из {len(vk)}")
    no_mi = [g["id"] for g in vk if not g["message_in"]][:5]
    print("примеры без message_in:", no_mi)
