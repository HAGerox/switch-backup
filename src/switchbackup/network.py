from __future__ import annotations

import re
import socket

from .models import BackupResult, Credential, DiscoveryResult, Switch
from .storage import Database

CISCO_PROBE_DRIVERS = (
    "cisco_s300",
    "cisco_ios",
    "cisco_xe",
    "cisco_nxos",
)

_CONNECT_HANDLER = None
ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-_][0-?]*[ -/]*[@-~])")


def prepare_networking() -> None:
    """Import the SSH stack on the main thread before worker threads start."""
    global _CONNECT_HANDLER
    if _CONNECT_HANDLER is None:
        from netmiko import ConnectHandler

        _CONNECT_HANDLER = ConnectHandler


class CiscoBackupClient:
    """Cisco-focused discovery and startup-config retrieval using Netmiko."""

    def __init__(self, db: Database):
        self.db = db

    def discover_one(
        self, switch: Switch, credentials: list[Credential]
    ) -> DiscoveryResult:
        """Authenticate, identify the Cisco platform, hostname, and model."""
        if not self._tcp_open(switch.ip, 22):
            return DiscoveryResult(
                switch_id=switch.id,
                ip=switch.ip,
                ok=False,
                message="SSH not reachable",
            )

        ordered = self._ordered_credentials(switch, credentials)
        if not ordered:
            return DiscoveryResult(
                switch_id=switch.id,
                ip=switch.ip,
                ok=False,
                message="No credentials",
            )

        errors: list[str] = []

        known_device_type = self._device_type_for_switch(switch)
        if known_device_type:
            for credential in ordered:
                result = self._connect_and_discover(
                    switch, credential, known_device_type
                )
                if result.ok:
                    return result
                errors.append(f"{credential.name}: {result.message}")

        for credential in ordered:
            password = self.db.get_credential_password(credential.id)
            if not password:
                errors.append(f"{credential.name}: password missing")
                continue

            for driver in CISCO_PROBE_DRIVERS:
                result = self._connect_and_discover(switch, credential, driver)
                if result.ok:
                    return result
                errors.append(f"{credential.name}/{driver}: {result.message}")

        return DiscoveryResult(
            switch_id=switch.id,
            ip=switch.ip,
            ok=False,
            message="; ".join(errors[-3:]) or "Unable to identify switch",
        )

    def backup_one(self, switch: Switch, credentials: list[Credential]) -> BackupResult:
        if not self._tcp_open(switch.ip, 22):
            return BackupResult(
                switch_id=switch.id,
                ip=switch.ip,
                ok=False,
                message="SSH (TCP/22) not reachable",
            )

        ordered = self._ordered_credentials(switch, credentials)
        if not ordered:
            return BackupResult(
                switch_id=switch.id,
                ip=switch.ip,
                ok=False,
                message="No credentials configured",
            )

        errors: list[str] = []

        # Fast path after a previous successful discovery: reuse the known driver,
        # but still try all credentials if the old one no longer works.
        known_device_type = self._device_type_for_switch(switch)
        if known_device_type:
            for credential in ordered:
                result = self._connect_and_fetch(
                    switch, credential, known_device_type
                )
                if result.ok:
                    return result
                errors.append(f"{credential.name}: {result.message}")

        # Fresh Cisco probe path. Generic SSHDetect is intentionally avoided because
        # some Catalyst Small Business prompts contain ANSI control sequences that
        # make its prompt polling take minutes.
        for credential in ordered:
            password = self.db.get_credential_password(credential.id)
            if not password:
                errors.append(f"{credential.name}: password missing from Keychain")
                continue

            for driver in CISCO_PROBE_DRIVERS:
                result = self._connect_and_fetch(switch, credential, driver)
                if result.ok:
                    return result
                errors.append(f"{credential.name}/{driver}: {result.message}")

        message = "; ".join(errors[-4:]) or "Unable to authenticate or identify switch"
        return BackupResult(
            switch_id=switch.id,
            ip=switch.ip,
            ok=False,
            message=message,
        )

    def _connect_and_fetch(
        self, switch: Switch, credential: Credential, device_type: str
    ) -> BackupResult:
        prepare_networking()
        password = self.db.get_credential_password(credential.id)
        if not password:
            return BackupResult(
                switch_id=switch.id,
                ip=switch.ip,
                ok=False,
                message="Password missing from Keychain",
            )

        device = self._base_device(switch.ip, credential.username, password)
        device["device_type"] = device_type

        try:
            with _CONNECT_HANDLER(**device) as connection:
                prompt = connection.find_prompt()
                discovered_name = self._hostname(connection, prompt)
                config = connection.send_command(
                    "show startup-config",
                    read_timeout=120,
                    strip_prompt=True,
                    strip_command=True,
                )

            problem = self._config_problem(config)
            if problem:
                return BackupResult(
                    switch_id=switch.id,
                    ip=switch.ip,
                    ok=False,
                    message=problem,
                )

            return BackupResult(
                switch_id=switch.id,
                ip=switch.ip,
                ok=True,
                config=config.rstrip() + "\n",
                discovered_name=discovered_name,
                device_type=device_type,
                credential_id=credential.id,
                message="Backed up",
            )
        except Exception as exc:
            return BackupResult(
                switch_id=switch.id,
                ip=switch.ip,
                ok=False,
                message=self._friendly_error(exc),
            )

    def _connect_and_discover(
        self, switch: Switch, credential: Credential, device_type: str
    ) -> DiscoveryResult:
        prepare_networking()
        password = self.db.get_credential_password(credential.id)
        if not password:
            return DiscoveryResult(
                switch_id=switch.id,
                ip=switch.ip,
                ok=False,
                message="Password missing",
            )

        device = self._base_device(switch.ip, credential.username, password)
        device["device_type"] = device_type

        try:
            with _CONNECT_HANDLER(**device) as connection:
                prompt = connection.find_prompt()
                discovered_name = self._hostname(connection, prompt)
                version = connection.send_command_timing(
                    "show version",
                    read_timeout=30,
                    last_read=1,
                    strip_prompt=True,
                    strip_command=True,
                )
                model = self._model_from_output(version)
                if not model:
                    inventory = connection.send_command_timing(
                        "show inventory",
                        read_timeout=20,
                        last_read=1,
                        strip_prompt=True,
                        strip_command=True,
                    )
                    model = self._model_from_output(inventory)

            detected_type = self._device_type_from_output(version, device_type)

            return DiscoveryResult(
                switch_id=switch.id,
                ip=switch.ip,
                ok=True,
                discovered_name=discovered_name,
                model=model or self._device_family(detected_type),
                device_type=detected_type,
                credential_id=credential.id,
                message="Discovered",
            )
        except Exception as exc:
            return DiscoveryResult(
                switch_id=switch.id,
                ip=switch.ip,
                ok=False,
                message=self._friendly_error(exc),
            )

    @staticmethod
    def _base_device(
        host: str, username: str, password: str
    ) -> dict[str, object]:
        return {
            "host": host,
            "username": username,
            "password": password,
            "port": 22,
            "conn_timeout": 8,
            "auth_timeout": 10,
            "banner_timeout": 10,
            "read_timeout_override": 120,
            "fast_cli": True,
            "use_keys": False,
            "allow_agent": False,
            "ssh_strict": False,
        }

    @staticmethod
    def _hostname(connection, prompt: str) -> str:
        base = getattr(connection, "base_prompt", "") or prompt
        clean = ANSI_ESCAPE_RE.sub("", str(base))
        return clean.strip().rstrip("#>").strip() or "Switch"

    @staticmethod
    def _device_type_from_output(output: str, probed_type: str) -> str:
        text = ANSI_ESCAPE_RE.sub("", output or "").lower()
        if "nx-os" in text or "nexus" in text:
            return "cisco_nxos"
        if "ios xe" in text:
            return "cisco_xe"
        if any(marker in text for marker in ("c1300", "c1200", "cbs", "s300")):
            return "cisco_s300"
        if "cisco ios" in text:
            return "cisco_ios"
        return probed_type

    @staticmethod
    def _device_type_for_switch(switch: Switch) -> str | None:
        model = (switch.model or "").lower()
        if any(marker in model for marker in ("c1300", "c1200", "cbs", "s300")):
            return "cisco_s300"
        return switch.last_device_type

    @staticmethod
    def _model_from_output(output: str) -> str:
        text = output or ""
        patterns = (
            r"(?im)^\s*Model [Nn]umber\s*:\s*([^\s,]+)",
            r"(?im)^\s*Model\s*:\s*([^\r\n]+)",
            r"(?im)^\s*cisco\s+Nexus\S*\s+(\S+)\s+Chassis",
            r"(?im)^\s*cisco\s+(\S+)\s+\([^)]+\)\s+processor",
            r"(?im)\bPID:\s*([^,\s]+)",
            r"(?im)^\s*Chassis(?: type)?\s*:\s*([^\r\n]+)",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()
        return ""

    @staticmethod
    def _device_family(device_type: str) -> str:
        families = {
            "cisco_ios": "Cisco IOS",
            "cisco_xe": "Cisco IOS-XE",
            "cisco_nxos": "Cisco Nexus",
            "cisco_s200": "Cisco S200",
            "cisco_s300": "Cisco S300",
            "cisco_s500": "Cisco S500",
        }
        return families.get(device_type, "Cisco switch")

    @staticmethod
    def _config_problem(config: str) -> str:
        text = (config or "").strip()
        if len(text) < 20:
            return "Startup configuration was empty or unexpectedly short"
        lowered = text.lower()
        missing_config_markers = (
            "non-volatile configuration memory is not present",
            "startup configuration is not present",
            "startup-config is not present",
            "startup configuration file does not exist",
        )
        if any(marker in lowered for marker in missing_config_markers):
            return "Startup configuration is not present on the device"
        known_errors = (
            "% authorization failed",
            "command authorization failed",
            "% invalid input detected",
            "% incomplete command",
            "% ambiguous command",
            "permission denied",
        )
        for marker in known_errors:
            if marker in lowered:
                return f"Device rejected 'show startup-config' ({marker.strip('% ')})"
        if "--more--" in lowered or ("<space>" in lowered and "quit" in lowered):
            return "Startup configuration was paginated and therefore incomplete"
        return ""

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        name = exc.__class__.__name__
        text = str(exc).strip().replace("\n", " ")
        if "Authentication" in name or "authentication" in text.lower():
            return "authentication failed"
        if "Timeout" in name or "timed out" in text.lower():
            return "connection timed out"
        if text:
            return f"{name}: {text[:180]}"
        return name

    @staticmethod
    def _tcp_open(host: str, port: int, timeout: float = 1.5) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    @staticmethod
    def _ordered_credentials(
        switch: Switch, credentials: list[Credential]
    ) -> list[Credential]:
        if switch.last_credential_id is None:
            return credentials[:]
        preferred = [c for c in credentials if c.id == switch.last_credential_id]
        remaining = [c for c in credentials if c.id != switch.last_credential_id]
        return preferred + remaining
