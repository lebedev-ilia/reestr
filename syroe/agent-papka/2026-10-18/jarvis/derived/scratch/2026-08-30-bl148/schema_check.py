import sys
sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")
from jarvis.core.tools import delegate

s = delegate.input_schema
print("type:", type(s).__name__)
print("required:", s.get("required"))
print("props:", sorted(s.get("properties", {})))
