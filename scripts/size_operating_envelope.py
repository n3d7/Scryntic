"""Print capacity arithmetic from an operator-owned JSON sizing configuration."""

import argparse
import json
from pathlib import Path

from scryntic.benchmarks.sizing import config_from_dict, size_envelope


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.config.read_text(encoding="utf-8"))
    if type(data) is not dict:
        parser.error("Expected sizing configuration object")
    result = size_envelope(config_from_dict(data))
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
