"""Trusted Linux bootstrap; install hard controls before loading native parsers."""

import ctypes
import errno
import os
import resource
import sys

_SYSCALL_FAILURE = "Missing syscall control"


def restrict(memory: int, cpu: int) -> None:
    for kind, limit in (
        (resource.RLIMIT_AS, memory),
        (resource.RLIMIT_CPU, cpu),
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_FSIZE, 0),
        (resource.RLIMIT_NOFILE, 32),
        (resource.RLIMIT_NPROC, 0),
    ):
        resource.setrlimit(kind, (limit, limit))
        if resource.getrlimit(kind) != (limit, limit):
            raise RuntimeError("Missing resource control")
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise RuntimeError("Missing privilege control")
    library = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    library.seccomp_rule_add.restype = ctypes.c_int
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    library.seccomp_load.restype = ctypes.c_int
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    context = library.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise RuntimeError(_SYSCALL_FAILURE)
    try:
        for name in (
            "socket",
            "socketpair",
            "connect",
            "bind",
            "listen",
            "accept",
            "accept4",
            "fork",
            "vfork",
            "clone",
            "clone3",
            "execve",
            "execveat",
            "ptrace",
            "mount",
            "umount2",
            "pivot_root",
            "chroot",
            "unshare",
            "setns",
            "mknod",
            "mknodat",
            "bpf",
            "perf_event_open",
            "userfaultfd",
            "io_uring_setup",
            "keyctl",
            "add_key",
            "request_key",
            "open_by_handle_at",
            "process_vm_readv",
            "process_vm_writev",
            "kill",
            "pidfd_getfd",
            "setuid",
            "setgid",
            "setgroups",
            "setresuid",
            "setresgid",
        ):
            number = library.seccomp_syscall_resolve_name(name.encode("ascii"))
            if (
                number < 0
                or library.seccomp_rule_add(
                    context, 0x00050000 | errno.EPERM, number, 0
                )
                != 0
            ):
                raise RuntimeError(_SYSCALL_FAILURE)
        if library.seccomp_load(context) != 0:
            raise RuntimeError(_SYSCALL_FAILURE)
    finally:
        library.seccomp_release(context)


def main() -> None:
    # All arguments are emitted by the fixed launcher, never a manifest.
    memory, cpu = int(sys.argv[1]), int(sys.argv[2])
    restrict(memory, cpu)
    sys.path[:] = ["/app", "/packages", *sys.path]
    from scryntic.imports.probe import check

    check()
    if sys.argv[3:] == ["probe"]:
        os.write(1, b'{"isolation":"verified","protocol":1}')
        return
    if sys.argv[3] == "analysis":
        from scryntic.dataset.worker import run as analyze

        analyze(sys.argv[4:])
        return
    from scryntic.imports.worker import run

    run(sys.argv[3:])


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        # No exception text, payload or native diagnostics in the result protocol.
        os._exit(1)
