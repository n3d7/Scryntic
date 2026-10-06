"""Application-owned CPU policy, never supplied by jobs or provider code."""

MEMORY = 256 * 1024 * 1024
CPU_SECONDS = 5
WALL_SECONDS = 30
TASKS = 8
OUTPUT_BYTES = 65_536
WIRE_BYTES = 2 * OUTPUT_BYTES + 16_384
ENVIRONMENT = {"HOME": "/home", "PATH": "/python/bin", "LC_ALL": "C"}
NAMESPACES = ("user", "mnt", "pid", "net", "ipc", "uts")
REQUEST_PATH = "/input/request"
CONTROL_PATH = "/control"
RESULT_NAME = "result.json"
READONLY_MOUNTS = ("/", "/python", "/app/scryntic", REQUEST_PATH, CONTROL_PATH)
TMPFS_BYTES = {"/home": 1024 * 1024, "/tmp": 16 * 1024 * 1024, "/output": 1024 * 1024}
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
    "TemporaryFileSystem=/tmp:rw,nodev,nosuid,noexec,size=16M,mode=1777 "
    "/home:rw,nodev,nosuid,noexec,size=1M,mode=1777 "
    "/output:rw,nodev,nosuid,noexec,size=1M,mode=1777",
)


class IsolationError(RuntimeError):
    """Sanitized launch, control, IPC or cleanup failure."""
