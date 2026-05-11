#!/usr/bin/env python3
"""Run Hummingbot headless without starting the MQTT bridge.

This is a local Maverick wrapper for supervised one-shot pilots. It preserves
Hummingbot's headless strategy startup path, but monkeypatches MQTT startup to a
no-op so a missing local broker cannot produce reconnect spam. The external
supervisor handles stop/cancel guardrails instead of MQTT commands.

Secrets are read from macOS Keychain and never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "bin"))

from bin.hummingbot_quickstart import quick_start  # noqa: E402
from hummingbot.client.config.config_crypt import ETHKeyFileSecretManger  # noqa: E402
from hummingbot.client.hummingbot_application import HummingbotApplication  # noqa: E402


def keychain(service: str) -> str:
    return subprocess.check_output(
        ["security", "find-generic-password", "-a", "maverick", "-s", service, "-w"],
        text=True,
    ).strip()


def noop_mqtt_start(self: HummingbotApplication, timeout: float = 30.0) -> None:
    self.logger().info("MQTT Bridge startup skipped by Maverick supervised pilot wrapper.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-f", "--config-file-name", help="Strategy YAML in conf/strategies")
    parser.add_argument("--v2", dest="v2_conf", help="V2 script config YAML in conf/scripts")
    parser.add_argument(
        "--password-keychain-service",
        default="hummingbot-config-password",
        help="macOS Keychain service containing Hummingbot config password",
    )
    args = parser.parse_args()

    if bool(args.config_file_name) == bool(args.v2_conf):
        parser.error("Specify exactly one of --config-file-name/-f or --v2")

    HummingbotApplication.mqtt_start = noop_mqtt_start  # type: ignore[method-assign]

    quickstart_args = argparse.Namespace(
        config_file_name=args.config_file_name,
        v2_conf=args.v2_conf,
        config_password=keychain(args.password_keychain_service),
        auto_set_permissions=None,
        headless=True,
    )
    secrets_manager = ETHKeyFileSecretManger(quickstart_args.config_password)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(quick_start(quickstart_args, secrets_manager))
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
