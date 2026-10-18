import json
import re
from statistics import median
from collections import Counter

from parse import load_days, build_turns, is_vk_turn, turn_steps_record, turn_result_record, self_json

ISO = "%Y-%m-%dT%H:%M:%S"


def parse_ts(ts):
    # "2026-08-31T00:04:17+10:00" -> отбрасываем зону (все записи в одной зоне)
    core = ts[:19]
    import datetime
    return datetime.datetime.strptime(core, ISO)


def tool_sig(action_rec):
    try:
        d = json.loads(action_rec.get("content") or "")
    except Exception:
        return None
    return d.get("tool")


def sentence_count(text):
    # грубая оценка: разбиваем по . ! ? с последующим пробелом/концом
    parts = re.split(r"[.!?]+(?:\s|$)", text.strip())
    return len([p for p in parts if p.strip()])


def main():
    recs = load_days()
    turns = build_turns(recs)
    vk = [g for g in turns.values() if is_vk_turn(g)]
    vk.sort(key=lambda g: g["ts_first"] or "")

    rows = []
    for g in vk:
        mi = g["message_in"]
        st = turn_steps_record(g) or {}
        res = turn_result_record(g) or {}
        t_in = parse_ts(mi["ts"]) if mi and mi.get("ts") else None
        outs = sorted(g["message_out"], key=lambda r: r["ts"])
        stats = sorted(g["status"], key=lambda r: r["ts"])
        t_first_out = parse_ts(outs[0]["ts"]) if outs else None
        t_last_out = parse_ts(outs[-1]["ts"]) if outs else None
        # "конец хода" — последняя запись турна вообще (self summary обычно последний)
        all_ts = [r["ts"] for r in (g["actions"] + g["selfs"] + g["message_out"] + g["status"]) if r.get("ts")]
        t_end = parse_ts(max(all_ts)) if all_ts else None

        def dt(a, b):
            if a is None or b is None:
                return None
            return (b - a).total_seconds()

        tools = Counter(tool_sig(a) for a in g["actions"] if tool_sig(a))
        final_text = outs[-1]["content"] if outs else ""
        row = {
            "turn": g["id"],
            "day": g["day"],
            "ts_in": mi["ts"] if mi else None,
            "in_text": (mi.get("content") or "") if mi else "",
            "sec_to_first_out": dt(t_in, t_first_out),
            "sec_to_last_out": dt(t_in, t_last_out),
            "sec_to_end": dt(t_in, t_end),
            "steps": st.get("steps"),
            "steps_self_turns": res.get("turns"),
            "n_message_out": len(outs),
            "n_status": len(stats),
            "auto_status": st.get("auto_status"),
            "chars_final": len(final_text),
            "chars_all_out": sum(len(o["content"] or "") for o in outs),
            "sentences_final": sentence_count(final_text) if final_text else 0,
            "tools": dict(tools),
            "n_actions": len(g["actions"]),
            "cost_usd": res.get("cost_usd"),
            "gate_denies": sum(1 for r in g["selfs"] if (self_json(r) or {}).get("gate") == "deny"),
        }
        rows.append(row)

    with open("derived/scratch/2026-09-01-vk-skorost/rows.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f"VK-ходов: {len(rows)}")

    def dist(key, rows=rows):
        vals = sorted(r[key] for r in rows if r.get(key) is not None)
        if not vals:
            return None
        def pct(p):
            if not vals:
                return None
            idx = min(len(vals) - 1, int(round(p / 100 * (len(vals) - 1))))
            return vals[idx]
        return {
            "n": len(vals), "median": median(vals), "p75": pct(75), "p90": pct(90),
            "max": max(vals), "min": min(vals),
        }

    print("\n=== распределения ===")
    for key, label in [
        ("sec_to_first_out", "сек до первого message_out"),
        ("sec_to_last_out", "сек до последнего message_out"),
        ("sec_to_end", "сек до конца хода"),
        ("chars_final", "знаков в финальном сообщении"),
        ("steps", "число шагов"),
    ]:
        d = dist(key)
        print(f"{label}: {d}")

    print("\n=== доля сообщений против kratkost ===")
    with_final = [r for r in rows if r["chars_final"] > 0]
    long_sent = [r for r in with_final if r["sentences_final"] > 6]
    long_chars = [r for r in with_final if r["chars_final"] > 1500]
    print(f"итого финальных сообщений: {len(with_final)}")
    print(f"длиннее 6 предложений: {len(long_sent)} ({len(long_sent)/max(len(with_final),1):.0%})")
    print(f"длиннее 1500 знаков: {len(long_chars)} ({len(long_chars)/max(len(with_final),1):.0%})")
    print("топ-5 по знакам:")
    for r in sorted(with_final, key=lambda r: -r["chars_final"])[:5]:
        print(f"  {r['turn']} {r['ts_in']}: {r['chars_final']} знаков, {r['sentences_final']} предл.")

    print("\n=== топ-10 самых долгих ходов (по sec_to_end) ===")
    top = sorted([r for r in rows if r["sec_to_end"] is not None], key=lambda r: -r["sec_to_end"])[:10]
    for r in top:
        print(f"{r['turn']} | {r['ts_in']} | sec_to_end={r['sec_to_end']:.0f} steps={r['steps']} "
              f"tools={r['tools']} | вопрос: {' '.join(r['in_text'].split())[:140]}")

    print("\n=== кандидаты 'простой вопрос по медленному пути' (steps<=6, sec_to_end>60) ===")
    cands = [r for r in rows if (r.get("steps") or 99) <= 6 and (r.get("sec_to_end") or 0) > 60]
    for r in cands:
        print(f"{r['turn']} | {r['ts_in']} | steps={r['steps']} sec={r['sec_to_end']:.0f} | "
              f"{' '.join(r['in_text'].split())[:160]}")

    print("\n=== все VK-ходы кратко (для ручного разбора) ===")
    for r in rows:
        print(f"{r['turn']} | {r['ts_in']} | steps={r['steps']} sec_end={r.get('sec_to_end')} "
              f"chars_final={r['chars_final']} | {' '.join(r['in_text'].split())[:100]}")


if __name__ == "__main__":
    main()
