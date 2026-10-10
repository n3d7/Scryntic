"""Minimal foreground bootstrap; operational CLI workflows belong to F24."""

import argparse
from typing import Never

from scryntic.configuration.paths import Installation
from scryntic.daemon.health import Fault, State
from scryntic.daemon.logging import Event, StructuredLogger


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise ValueError("Invalid daemon bootstrap")


def main() -> int:
    logger = StructuredLogger()
    uninstall = logger.install_standard_logging()
    try:
        parser = _Parser(description="Run the Scryntic foreground daemon.")
        parser.add_argument("--profile", choices=("bybit", "fixture"), default="bybit")
        parser.add_argument("--producer", default="daemon")
        parser.add_argument("--epoch", default="daemon-v1")
        parser.add_argument("--category", default="spot")
        parser.add_argument("--symbol", default="BTCUSDT")
        parser.add_argument("--shutdown-seconds", type=float, default=15.0)
        arguments = parser.parse_args()
        # Import only after process logging is sanitized, including dependencies.
        from scryntic.daemon.components import Services
        from scryntic.daemon.config import DaemonConfig
        from scryntic.daemon.foreground import run_foreground
        from scryntic.daemon.runtime import LifecycleLimits

        config = DaemonConfig(
            Installation.workstation(),
            producer=arguments.producer,
            epoch=arguments.epoch,
            profile=arguments.profile,
            category=arguments.category,
            symbol=arguments.symbol,
        )
        return run_foreground(
            Services(config),
            limits=LifecycleLimits(shutdown_s=arguments.shutdown_seconds),
            logger=logger,
        )
    except (Exception, KeyboardInterrupt):
        logger.emit(Event.STATE, state=State.FAILED, fault=Fault.SOURCE)
        logger.close(timeout=0.1)
        return 2
    finally:
        uninstall()


if __name__ == "__main__":
    raise SystemExit(main())
