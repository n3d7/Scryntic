"""Application-owned CPU policies, never supplied by jobs or provider code."""

from dataclasses import dataclass

MEMORY = 256 * 1024 * 1024
CPU_SECONDS = 5
WALL_SECONDS = 30
TASKS = 8
OUTPUT_BYTES = 65_536
WIRE_BYTES = 2 * OUTPUT_BYTES + 16_384
HOME_PATH = "/worker-home"
TEMP_PATH = "/worker-tmp"
ENVIRONMENT = {
    "HOME": HOME_PATH,
    "TMPDIR": TEMP_PATH,
    "PATH": "/python/bin",
    "LC_ALL": "C",
}
NAMESPACES = ("user", "mnt", "pid", "net", "ipc", "uts")
REQUEST_PATH = "/input/request"
CONTROL_PATH = "/control"
RESULT_NAME = "result.json"
READONLY_MOUNTS = ("/", "/python", "/app/scryntic", REQUEST_PATH, CONTROL_PATH)
TMPFS_BYTES = {
    HOME_PATH: 1024 * 1024,
    TEMP_PATH: 16 * 1024 * 1024,
    "/output": 1024 * 1024,
}
WRITABLE_MOUNTS = tuple(TMPFS_BYTES)

# Unsupported properties are fatal; no downgrade for old systemd/kernel hosts.
PROPERTIES = (
    "Type=exec",
    "Slice=system.slice",
    "DynamicUser=yes",
    "PrivateUsers=yes",
    "PrivatePIDs=yes",
    "PrivateNetwork=yes",
    "PrivateIPC=yes",
    "PrivateMounts=yes",
    "PrivateDevices=yes",
    "ProtectHostname=yes",
    "ProtectSystem=strict",
    "ProtectHome=yes",
    # DynamicUser's implicit PrivateTmp must not add writable, unbounded scratch.
    "InaccessiblePaths=/tmp /var/tmp",
    "ProtectControlGroups=yes",
    "ProtectKernelTunables=yes",
    "ProtectKernelModules=yes",
    "ProtectKernelLogs=yes",
    "NoNewPrivileges=yes",
    "CapabilityBoundingSet=",
    "AmbientCapabilities=",
    "SupplementaryGroups=",
    "RestrictNamespaces=yes",
    "RestrictSUIDSGID=yes",
    "LockPersonality=yes",
    "RemoveIPC=yes",
    "SetLoginEnvironment=no",
    "UMask=0077",
    "WorkingDirectory=/",
    "KillMode=control-group",
    "SendSIGKILL=yes",
    "TimeoutStopSec=1s",
    "OOMPolicy=kill",
    f"RuntimeMaxSec={WALL_SECONDS}s",
    f"MemoryMax={MEMORY}",
    "MemorySwapMax=0",
    f"TasksMax={TASKS}",
    "CPUQuota=100%",
    f"LimitAS={MEMORY}",
    f"LimitCPU={CPU_SECONDS}",
    "LimitCORE=0",
    f"LimitFSIZE={OUTPUT_BYTES}",
    "LimitNOFILE=32",
    "LimitNPROC=0",
    f"TemporaryFileSystem={TEMP_PATH}:rw,nodev,nosuid,noexec,size=16M,mode=1777 "
    f"{HOME_PATH}:rw,nodev,nosuid,noexec,size=1M,mode=1777 "
    "/output:rw,nodev,nosuid,noexec,size=1M,mode=1777",
)


@dataclass(frozen=True, slots=True)
class CPUProfile:
    name: str
    memory: int
    address_space: int
    cpu_seconds: int
    wall_seconds: int
    tasks: int
    nofile: int
    nproc: int
    allow_threads: bool
    readonly: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]

    def require_owned(self) -> None:
        if self not in (SYNTHETIC_CPU, FORECAST_CPU):
            raise IsolationError("Unknown fixed CPU profile")

    @property
    def properties(self) -> tuple[str, ...]:
        self.require_owned()
        if self == SYNTHETIC_CPU:
            return PROPERTIES
        replacements = {
            "RuntimeMaxSec": f"{self.wall_seconds}s",
            "MemoryMax": str(self.memory),
            "TasksMax": str(self.tasks),
            "LimitAS": str(self.address_space),
            "LimitCPU": str(self.cpu_seconds),
            "LimitNOFILE": str(self.nofile),
            "LimitNPROC": str(self.nproc),
        }
        return tuple(
            key + "=" + replacements.get(key, value)
            for item in PROPERTIES
            for key, value in [item.split("=", 1)]
        )


SYNTHETIC_CPU = CPUProfile(
    "f21-cpu-v1",
    MEMORY,
    MEMORY,
    CPU_SECONDS,
    WALL_SECONDS,
    TASKS,
    32,
    0,
    False,
    READONLY_MOUNTS,
    tuple(ENVIRONMENT.items()),
)
FORECAST_CPU = CPUProfile(
    "f22-cpu-v1",
    4 * 1024**3,
    8 * 1024**3,
    90,
    180,
    8,
    64,
    8,
    True,
    (*READONLY_MOUNTS, "/runtime", "/model"),
    tuple(
        (
            ENVIRONMENT
            | {
                "OMP_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "HF_HUB_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "CUDA_VISIBLE_DEVICES": "",
                "TORCH_HOME": HOME_PATH,
            }
        ).items()
    ),
)


class IsolationError(RuntimeError):
    """Sanitized launch, control, IPC or cleanup failure."""
