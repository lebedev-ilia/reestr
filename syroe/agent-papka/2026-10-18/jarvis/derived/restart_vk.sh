#!/bin/sh
sleep 480
cd "/home/ilya/Рабочий стол/Agent/jarvis" || exit 1
i=0
while [ $i -lt 20 ]; do
    if pgrep -f "modules/jobs/run.py" >/dev/null 2>&1; then
        sleep 30; i=$((i + 1))
    else break; fi
done
pid=$(pgrep -f "run.py --vk" | head -1)
[ -n "$pid" ] || exit 0
kill "$pid"
sleep 3
nohup setsid ./.venv/bin/python run.py --vk >> derived/vk.log 2>&1 &
sleep 10
ps -eo pid,lstart,args --no-headers | grep "run.py --vk" | grep -v grep >> derived/vk.log
