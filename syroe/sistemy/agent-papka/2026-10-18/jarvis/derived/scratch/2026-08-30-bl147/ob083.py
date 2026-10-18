import sys
sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")
from jarvis.core import memory as M   # noqa: E402

print(M.obligation_note("ob-083",
      "Сюда же второй случай, пойман 30.08 в 21:16: `2>/dev/null` в конце "
      "команды шлюз считает записью вне папки агента ('перенаправление вывода "
      "вне папки агента: /dev/null'). /dev/null (и /dev/stdout, /dev/stderr) "
      "надо внести в белый список целей перенаправления — это не запись."))
