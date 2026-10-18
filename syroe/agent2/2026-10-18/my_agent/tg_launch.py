#!/usr/bin/env python3
"""Демонизатор: отвязывает tg_login_driver.py от терминала, чтобы он жил после
завершения команды run_command."""
import os
import sys

pid = os.fork()
if pid > 0:
    os._exit(0)
os.setsid()
pid2 = os.fork()
if pid2 > 0:
    os._exit(0)

os.chdir("/Users/user/Desktop/Agent2/my_agent")
devnull = os.open("/dev/null", os.O_RDONLY)
os.dup2(devnull, 0)
err = os.open("/tmp/tg_driver.err", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.dup2(err, 1)
os.dup2(err, 2)
os.execv(sys.executable, [sys.executable, "tg_login_driver.py"])
