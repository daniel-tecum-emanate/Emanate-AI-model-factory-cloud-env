#!/usr/bin/env python3
"""pty-drive — run a command on a real pty, answer one prompt, print the transcript.

`cs new`'s chooser is the only interactive surface in the tool, and it is the one that
decides whether work runs on this laptop or on a cloud VM. Driving it needs three things
at once: a real controlling terminal (`[[ -t 0 && -t 1 ]]`), stdin that stays open, and
an answer delivered *after* the prompt is printed.

Piping into `script` gives none of that reliably: on macOS the child's `read` reaches EOF
before the piped bytes arrive, so every answer looks like an abandoned prompt. Sleeping
first would make the suite timing-dependent, and a flaky check is worse than no check.
This waits for the prompt text to actually appear on the pty before writing, so the only
way it can hang is if the prompt never appears — which is reported as a timeout, loudly,
rather than as a passing check.

  pty-drive.py <expect> <reply> <timeout-seconds> -- <cmd> [args...]

Prints the full pty transcript on stdout and exits with the child's status. If <expect>
never appears the transcript ends with the line PTYDRIVE-TIMEOUT and the exit status is
99, so a test asserting on the prompt fails instead of passing vacuously. Passing an
empty <expect> sends nothing and simply records what an unanswered prompt does.
"""
import os
import pty
import select
import signal
import sys
import time

TIMEOUT_MARKER = "PTYDRIVE-TIMEOUT"


def main() -> int:
    if len(sys.argv) < 6 or sys.argv[4] != "--":
        sys.stderr.write(__doc__)
        return 2
    expect, reply, timeout = sys.argv[1], sys.argv[2], float(sys.argv[3])
    cmd = sys.argv[5:]

    pid, fd = pty.fork()
    if pid == 0:  # child: pty.fork() has already made this the controlling terminal
        try:
            os.execvp(cmd[0], cmd)
        except OSError:
            os._exit(127)

    out = bytearray()
    sent = not expect
    timed_out = False
    deadline = time.time() + timeout
    while True:
        if time.time() > deadline:
            timed_out = True
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
            break
        try:
            ready, _, _ = select.select([fd], [], [], 0.1)
        except OSError:
            break
        if not ready:
            continue
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break  # EIO on Linux when the child side closes; normal EOF
        if not chunk:
            break
        out += chunk
        if not sent and expect.encode() in bytes(out):
            os.write(fd, reply.encode())
            sent = True

    try:
        _, status = os.waitpid(pid, 0)
    except OSError:
        status = 0
    try:
        os.close(fd)
    except OSError:
        pass

    sys.stdout.write(out.decode("utf-8", "replace"))
    if timed_out:
        sys.stdout.write("\n%s waiting for %r\n" % (TIMEOUT_MARKER, expect))
        sys.stdout.flush()
        return 99
    sys.stdout.flush()
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    return 128 + os.WTERMSIG(status) if os.WIFSIGNALED(status) else 1


if __name__ == "__main__":
    sys.exit(main())
