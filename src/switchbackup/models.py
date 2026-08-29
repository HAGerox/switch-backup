from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Credential:
    id: int
    name: str
    username: str


@dataclass(slots=True)
class Switch:
    id: int
    ip: str
    name: str = ""
    last_credential_id: int | None = None
    last_device_type: str | None = None
    model: str = ""


@dataclass(slots=True)
class DiscoveryResult:
    switch_id: int
    ip: str
    ok: bool
    discovered_name: str = ""
    model: str = ""
    device_type: str = ""
    credential_id: int | None = None
    message: str = ""


@dataclass(slots=True)
class BackupResult:
    switch_id: int
    ip: str
    ok: bool
    config: str = ""
    discovered_name: str = ""
    device_type: str = ""
    credential_id: int | None = None
    message: str = ""
