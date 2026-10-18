import sys
sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")
from jarvis.core import agent as A

live = "memory/projects/vnutrennii/tasks/t-cap-proba/task.md"
abs_live = "/home/ilya/Рабочий стол/Agent/jarvis/" + live
long_text = "\n".join(f"строка {i}" for i in range(101))

for name, inp in [
    ("Write отн. путь", {"file_path": live, "content": long_text}),
    ("Write абс. путь", {"file_path": abs_live, "content": long_text}),
    ("Edit абс. путь", {"file_path": abs_live, "old_string": "# Проба потолка",
                        "new_string": "# Проба потолка\n" + long_text}),
]:
    tool = "Edit" if name.startswith("Edit") else "Write"
    print(name, "->", A.task_md_cap_danger(tool, inp))
