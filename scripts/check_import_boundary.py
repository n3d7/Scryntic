"""Explicit supported-host F16 qualification; unavailable controls are failure."""

import platform
import subprocess
import sys

from scryntic.imports.launcher import LinuxDecoder
from scryntic.imports.protocol import ImportLimits


def main() -> None:
    LinuxDecoder(ImportLimits()).probe()
    bubblewrap = subprocess.run(
        ["/usr/bin/bwrap", "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    print(
        f"F16 controls verified: {platform.system()} {platform.release()} {platform.machine()}; Python {sys.version.split()[0]}; {bubblewrap}"
    )


if __name__ == "__main__":
    main()
