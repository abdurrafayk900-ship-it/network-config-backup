"""Load and validate the device inventory."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Command used to pull the running config for each supported Netmiko platform.
BACKUP_COMMANDS: dict[str, str] = {
    "cisco_ios": "show running-config",
    "cisco_xe": "show running-config",
    "cisco_nxos": "show running-config",
    "cisco_asa": "show running-config",
    "arista_eos": "show running-config",
    "juniper_junos": "show configuration | display set",
    "fortinet": "show full-configuration",
    "mikrotik_routeros": "/export",
}


class InventoryError(ValueError):
    """Raised when the inventory file is malformed."""


@dataclass
class Device:
    name: str
    host: str
    device_type: str
    username: str
    password: str = field(repr=False)
    port: int = 22
    secret: str | None = field(default=None, repr=False)
    groups: list[str] = field(default_factory=list)

    @property
    def backup_command(self) -> str:
        return BACKUP_COMMANDS[self.device_type]

    def netmiko_params(self) -> dict:
        params = {
            "device_type": self.device_type,
            "host": self.host,
            "username": self.username,
            "password": self.password,
            "port": self.port,
            "conn_timeout": 15,
        }
        if self.secret:
            params["secret"] = self.secret
        return params


def _resolve(value: str | None) -> str | None:
    """Expand ${ENV_VAR} references so secrets never live in the inventory file."""
    if value is None:
        return None
    expanded = os.path.expandvars(str(value))
    if "${" in expanded:
        raise InventoryError(f"Environment variable not set for value {value!r}")
    return expanded


def load_inventory(path: str | Path, group: str | None = None) -> list[Device]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    defaults = data.get("defaults", {})
    raw_devices = data.get("devices")
    if not raw_devices:
        raise InventoryError("Inventory has no 'devices' section")

    devices: list[Device] = []
    seen: set[str] = set()
    for entry in raw_devices:
        merged = {**defaults, **entry}
        missing = [k for k in ("name", "host", "device_type", "username", "password") if not merged.get(k)]
        if missing:
            raise InventoryError(f"Device {entry.get('name', '?')} missing: {', '.join(missing)}")
        if merged["name"] in seen:
            raise InventoryError(f"Duplicate device name: {merged['name']}")
        if merged["device_type"] not in BACKUP_COMMANDS:
            raise InventoryError(
                f"{merged['name']}: unsupported device_type {merged['device_type']!r} "
                f"(supported: {', '.join(sorted(BACKUP_COMMANDS))})"
            )
        seen.add(merged["name"])
        devices.append(
            Device(
                name=merged["name"],
                host=merged["host"],
                device_type=merged["device_type"],
                username=_resolve(merged["username"]),
                password=_resolve(merged["password"]),
                port=int(merged.get("port", 22)),
                secret=_resolve(merged.get("secret")),
                groups=list(merged.get("groups", [])),
            )
        )

    if group:
        devices = [d for d in devices if group in d.groups]
    return devices
