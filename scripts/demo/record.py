"""Record a command's terminal output as an asciicast v2 file, without a real TTY.

Usage: python3 record.py COLS ROWS OUT.cast COMMAND...
"""

import fcntl
import json
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time

cols, rows, out, cmd = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4:]
master, slave = pty.openpty()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
env = dict(os.environ, TERM="xterm-256color", COLUMNS=str(cols), LINES=str(rows))
proc = subprocess.Popen(cmd, stdin=slave, stdout=slave, stderr=slave, env=env)
os.close(slave)
start = time.time()
with open(out, "w") as f:
    header = {"version": 2, "width": cols, "height": rows, "timestamp": int(start)}
    f.write(json.dumps(header) + "\n")
    while True:
        ready, _, _ = select.select([master], [], [], 0.1)
        if ready:
            try:
                data = os.read(master, 4096)
            except OSError:
                break
            if not data:
                break
            event = [round(time.time() - start, 3), "o", data.decode(errors="replace")]
            f.write(json.dumps(event) + "\n")
            f.flush()
        elif proc.poll() is not None:
            break
sys.exit(proc.wait())
