#!/usr/bin/env python3
import sys, os, subprocess, time, signal, contextlib, io
os.environ["PYTHONIOENCODING"]="utf-8"
os.environ["PYTHONUTF8"]="1"

@contextlib.contextmanager
def capture_stdout():
    old = sys.stdout
    buf = io.StringIO()
    sys.stdout = buf
    try:
        yield buf
    finally:
        sys.stdout = old

os.chdir("E:/Project/f1-race-replay")

print("CHILD START")
log_lines = []
def _log(line=""):
    global log_lines
    log_lines.append(line)

proc = subprocess.Popen(
    [sys.executable, "-c", """
import sys, os, logging
os.environ["PYTHONIOENCODING"]="utf-8"
os.environ["PYTHONUTF8"]="1"

# route C extension stderr into the capture
import warnings
warnings.filterwarnings("ignore")

import main
if hasattr(main, "main"):
    main.main()
else:
    print("main.main not present")
"""],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    env={**os.environ, "PYTHONIOENCODING":"utf-8", "PYTHONUTF8":"1"},
    text=True,
    bufsize=1,
)

start = time.time()
while time.time()-start < 30:
    line = proc.stdout.readline()
    if not line:
        break
    print(line.rstrip())
    log_lines.append(line.rstrip())

proc.stdout.close()
try:
    proc.wait(timeout=5)
except subprocess.TimeoutExpired:
    proc.kill()
    proc.wait(timeout=3)
print("CHILD_EXIT=", proc.returncode)
print("---- LOG LINES ----")
for ln in log_lines:
    print(ln)
