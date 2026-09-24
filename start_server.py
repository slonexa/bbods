import subprocess
import sys
import os

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

# Start launcher detached so it persists seamlessly in background
proc = subprocess.Popen(
    [sys.executable, "launcher.py"],
    cwd=os.path.dirname(os.path.abspath(__file__)),
    creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
    close_fds=True
)
print("Started launcher PID:", proc.pid)
