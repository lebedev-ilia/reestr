p = bytes.fromhex("6d656d6f72792f70726f6a656374732f70726f62612d6368757a686f792f646f73736965722e6d64")
print(repr(p.decode()))
print(open(p.decode()).read()[:120])
