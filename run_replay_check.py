#!/usr/bin/env python3
import sys, os, signal, subprocess, time, logging
os.environ["PYTHONIOENCODING"]="utf-8"
os.environ["PYTHONUTF8"]="1"
os.chdir("C:/Users/Eahiya/workspace/voE7s6HaN")
prog = [sys.executable, "main.py", "--refresh-data", "--event", "Belgian Grand Prix", "--year", "2023"]
logfile = open("replay_launch.log","w")
p = subprocess.Popen(prog, stdout=logfile, stderr=subprocess.STDOUT)
start = time.time()
while time.time()-start < 45:
    if p.poll() is not None:
        break
    time.sleep(1)
if p.poll() is not None:
    logfile.close()
    with open("replay_launch.log") as fh:
        print(fh.read())
    sys.exit(p.returncode)
else:
    print("STILL_RUNNING after 45s")
    try:
        os.kill(p.pid, signal.CTRL_BREAK_EVENT)
    except Exception:
        pass
    p.wait(timeout=10)
    logfile.close()
    print("---- LOG ----")
    with open("replay_launch.log") as fh:
        print(fh.read())
    sys.exit(99)
