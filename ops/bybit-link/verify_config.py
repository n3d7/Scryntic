"""Offline transport checks: mock peer only; never connect or load a key."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class LinkConfigurationTests(unittest.TestCase):
    def test_templates_exist(self):
        for name in ("ssh_config", "peer.conf.example", "scryntic-bybit-link.service"):
            self.assertTrue((ROOT / name).is_file(), name)

    def parsed_client(self):
        with tempfile.TemporaryDirectory() as directory:
            peer = Path(directory) / "peer.conf"
            peer.write_text(
                "Host scryntic-bybit-broker\n"
                "    HostName 127.0.0.1\n    Port 22\n"
                "    User scryntic-bybit-tunnel\n"
            )
            config = Path(directory) / "ssh_config"
            config.write_text(
                (ROOT / "ssh_config")
                .read_text()
                .replace("/etc/scryntic/bybit-link/peer.conf", str(peer))
            )
            result = subprocess.run(
                [
                    shutil.which("ssh") or "ssh",
                    "-G",
                    "-F",
                    str(config),
                    "scryntic-bybit-broker",
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
        parsed = {}
        for line in result.stdout.splitlines():
            key, _, value = line.partition(" ")
            parsed.setdefault(key, []).append(value)
        return parsed

    def test_only_intended_unix_socket_forward(self):
        config = self.parsed_client()
        self.assertEqual(
            config["localforward"],
            ["/run/scryntic-bybit-link/broker.sock [127.0.0.1]:8765"],
        )
        self.assertNotIn("remoteforward", config)
        self.assertNotIn("dynamicforward", config)
        self.assertEqual(config["streamlocalbindmask"], ["07"])
        self.assertEqual(config["exitonforwardfailure"], ["yes"])

    def test_no_agent_password_or_interactive_session(self):
        config = self.parsed_client()
        for key, value in {
            "batchmode": "yes",
            "identitiesonly": "yes",
            "identityagent": "none",
            "forwardagent": "no",
            "passwordauthentication": "no",
            "kbdinteractiveauthentication": "no",
            "sessiontype": "none",
            "requesttty": "false",
            "permitlocalcommand": "no",
        }.items():
            self.assertEqual(config[key], [value], key)
        self.assertEqual(
            config["identityfile"], ["/var/lib/scryntic-bybit-link/id_ed25519"]
        )

    def test_host_trust_is_explicit_and_not_auto_enrolled(self):
        config = self.parsed_client()
        self.assertEqual(config["stricthostkeychecking"], ["true"])
        self.assertEqual(config["updatehostkeys"], ["false"])
        self.assertEqual(config["hostkeyalias"], ["scryntic-bybit-broker"])
        self.assertEqual(
            config["userknownhostsfile"], ["/etc/scryntic/bybit-link/known_hosts"]
        )
        self.assertEqual(config["globalknownhostsfile"], ["/dev/null"])

    def test_retry_is_bounded_and_noninteractive(self):
        config = self.parsed_client()
        self.assertEqual(config["connecttimeout"], ["10"])
        self.assertEqual(config["connectionattempts"], ["1"])
        self.assertEqual(config["serveraliveinterval"], ["30"])
        self.assertEqual(config["serveralivecountmax"], ["3"])

    def test_service_waits_for_configuration_and_has_separate_identity(self):
        unit = (ROOT / "scryntic-bybit-link.service").read_text()
        for setting in (
            "ConditionPathExists=/etc/scryntic/bybit-link/peer.conf",
            "ExecStartPre=/usr/bin/test -s /etc/scryntic/bybit-link/known_hosts",
            "User=scryntic-bybit-link",
            "Group=scryntic-bybit-client",
            "RuntimeDirectory=scryntic-bybit-link",
            "RuntimeDirectoryMode=0750",
            "ProtectSystem=strict",
            "ProtectHome=yes",
            "NoNewPrivileges=yes",
            "CapabilityBoundingSet=",
            "RestartSec=30",
            "MemoryMax=128M",
        ):
            self.assertIn(setting, unit)
        self.assertNotIn("LoadCredential", unit)
        self.assertNotIn("ExecStart=/bin/sh", unit)


if __name__ == "__main__":
    unittest.main(verbosity=2)
