p = bytes.fromhex("6d656d6f72792f70726f6a656374732f70726f62612d63687578686f792f646f7373696572")
print(open(p.decode() if isinstance(p, bytes) else p).read()[:80])
