"""Drive the UI in a pseudo-terminal with scripted key presses, for recording the README GIF with asciinema."""
import fcntl
import os
import select
import struct
import sys
import termios
import time

COLS, ROWS = 120, 22
KEYS = [(3.0, "\x1b[B"), (4.5, "\x1b[A"), (6.0, "z"), (8.5, "y"), (12.0, "q")]  # move, kill zombies, yes, quit

pid, fd = os.forkpty()
if pid == 0:
    os.environ["TERM"] = "xterm-256color"
    os.execvp("zombieports", ["zombieports"])
fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
t0 = time.time()
keys = list(KEYS)
while True:
    if keys and time.time() - t0 >= keys[0][0]:
        os.write(fd, keys.pop(0)[1].encode())
    r, _, _ = select.select([fd], [], [], 0.05)
    if r:
        try:
            data = os.read(fd, 65536)
        except OSError:
            break
        if not data:
            break
        sys.stdout.buffer.write(data)
        sys.stdout.flush()
