#!/usr/bin/env python3
import sys, os, subprocess, time, signal
os.environ["PYTHONIOENCODING"]="utf-8"
os.environ["PYTHONUTF8"]="1"
os.chdir("E:/Project/f1-race-replay")

log_path = "replay_launch.log"
if os.path.exists(log_path):
    os.remove(log_path)
prog = [sys.executable, "main.py", "--refresh-data", "--event", "Belgian Grand Prix", "--year", "2023"]
p = subprocess.Popen(prog, stdout=open(log_path,"w"), stderr=subprocess.STDOUT)
start = time.time()
while time.time() - start < 90:
    if p.poll() is not None:
        break
    try:
        sz = os.path.getsize(log_path)
    except OSError:
        sz = 0
    if sz > 0:
        time.sleep(0.5)
        break
    time.sleep(1)

if p.poll() is not None:
    p.wait(timeout=5)
else:
    try:
        os.kill(p.pid, signal.CTRL_BREAK_EVENT)
    except Exception:
        pass
    p.wait(timeout=8)

print("EXIT_CODE=", p.returncode)
print("---- LOG ----")
print(open(log_path).read())
