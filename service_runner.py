import subprocess
import sys
import os
import time

log_f = open(r"c:\Users\User\Desktop\odds\launcher_out.log", "w", encoding="utf-8")

proc = subprocess.Popen(
    [sys.executable, "launcher.py"],
    cwd=r"c:\Users\User\Desktop\odds",
    stdout=log_f,
    stderr=log_f
)
print("Started launcher PID:", proc.pid)
time.sleep(3)
