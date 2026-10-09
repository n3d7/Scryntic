import subprocess
import sys

import pytest


@pytest.mark.parametrize("threads", [False, True])
def test_syscall_profile_keeps_fork_exec_and_network_denied(threads: bool) -> None:
    # This qualifies the filter in a subprocess, not the F21 host boundary.
    code = """
import errno, os, socket, threading
from scryntic.imports.bootstrap import restrict_syscalls
restrict_syscalls(allow_threads=ALLOW)
for action in (os.fork, lambda: socket.socket(), lambda: os.execv("/usr/bin/true", ["true"])):
    try: action()
    except OSError as e: assert e.errno == errno.EPERM
    else: raise AssertionError("missing deny")
values=[]
thread=threading.Thread(target=lambda: values.append("ran"))
try: thread.start()
except RuntimeError:
    assert not ALLOW
else:
    thread.join()
    assert ALLOW and values == ["ran"]
""".replace("ALLOW", repr(threads))
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], env={}, capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr.decode()
