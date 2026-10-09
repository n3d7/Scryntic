"""Reviewed immutable artifacts; loading and provisioning are separate actions."""

from dataclasses import dataclass

MODEL_REPOSITORY = "google/timesfm-2.5-200m-pytorch"
MODEL_REVISION = "1d952420fba87f3c6dee4f240de0f1a0fbc790e3"
RUNTIME_LOCK_SHA256 = "4dd42f738e5ea4ec00240acb593d0eeee7e5b0f85ed80b585de88d8fdca2946f"
PACKAGE_SHA256 = "cfa3d22ddc3f019f0d9d95f19055428c4a4713526dafd7e5d1a7769782e7613f"
ARTIFACTS = (
    (
        "model.safetensors",
        "2f776efe6245e42b24bc4153ffdf61810140210e4bd3b01fb21f7aa779ab6ce8",
        925181104,
    ),
    (
        "config.json",
        "cd3315b760d5cc7e278d7afdf41b897031ced888fc6c115bd9b3ac0ea2c47408",
        475,
    ),
    (
        "README.md",
        "4c3f16633a3d8b9324904bf004429aced2ee009b84e64effb59a902520cc7f2e",
        2496,
    ),
)
DIRECT_PACKAGES = {
    "timesfm": "3.0.2",
    "torch": "2.14.0+cpu",
    "numpy": "2.5.3",
    "safetensors": "0.8.0",
    "huggingface-hub": "2.1.1",
}
RUNTIME_PACKAGES = {
    **DIRECT_PACKAGES,
    "anyio": "4.15.1",
    "click": "8.5.0",
    "filelock": "4.0.12",
    "fsspec": "2026.9.0",
    "h11": "0.16.0",
    "hf-xet": "1.6.0",
    "httpcore2": "2.13.1",
    "httpx2": "2.13.1",
    "idna": "3.20",
    "jinja2": "3.1.6",
    "markupsafe": "3.0.4",
    "mpmath": "1.3.0",
    "networkx": "3.7",
    "packaging": "26.3",
    "pyyaml": "6.0.3",
    "setuptools": "84.0.0",
    "sympy": "1.14.0",
    "tqdm": "4.70.1",
    "truststore": "0.10.4",
    "typing-extensions": "4.16.0",
}


@dataclass(frozen=True, slots=True)
class ModelAssets:
    artifacts: tuple[tuple[str, str, int], ...]
    package_sha256: str
    runtime_lock_sha256: str
    runtime_packages: tuple[tuple[str, str], ...]
    model_card: str
    terms: str


TIMESFM_ASSETS = ModelAssets(
    ARTIFACTS,
    PACKAGE_SHA256,
    RUNTIME_LOCK_SHA256,
    tuple(sorted(RUNTIME_PACKAGES.items())),
    "timesfm-2p5-card-" + ARTIFACTS[2][1],
    "apache-2.0-offline-forecast-research",
)
