"""Connect to devices, pull configs, store them and detect changes."""

from __future__ import annotations

import difflib
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .inventory import Device

log = logging.getLogger(__name__)

# Lines that change on every "show run" without a real config change.
# Stripping them keeps diffs meaningful.
VOLATILE_PATTERNS = [
    re.compile(r"^Building configuration\.\.\.$"),
    re.compile(r"^Current configuration : \d+ bytes$"),
    re.compile(r"^! Last configuration change at .*$"),
    re.compile(r"^! NVRAM config last updated at .*$"),
    re.compile(r"^ntp clock-period \d+$"),
    re.compile(r"^## Last commit: .*$"),
    re.compile(r"^# \w{3} \w{3} +\d+ [\d:]+ \d{4} by RouterOS.*$"),
]


def normalize_config(raw: str) -> str:
    lines = [line.rstrip() for line in raw.replace("\r\n", "\n").split("\n")]
    kept = [line for line in lines if not any(p.match(line) for p in VOLATILE_PATTERNS)]
    # Trim leading/trailing blank lines
    while kept and not kept[0]:
        kept.pop(0)
    while kept and not kept[-1]:
        kept.pop()
    return "\n".join(kept) + "\n"


def fetch_config(device: Device) -> str:
    # Imported lazily so unit tests and --dry-run don't need netmiko installed.
    from netmiko import ConnectHandler

    with ConnectHandler(**device.netmiko_params()) as conn:
        if device.secret:
            conn.enable()
        return conn.send_command(device.backup_command, read_timeout=60)


@dataclass
class BackupResult:
    device: str
    status: str  # "changed" | "unchanged" | "new" | "failed"
    path: Path | None = None
    diff: str = ""
    error: str = ""


def save_and_diff(device: Device, config: str, backup_dir: Path, timestamp: str) -> BackupResult:
    device_dir = backup_dir / device.name
    device_dir.mkdir(parents=True, exist_ok=True)
    latest = device_dir / "latest.cfg"

    previous = latest.read_text(encoding="utf-8") if latest.exists() else None
    if previous == config:
        return BackupResult(device.name, "unchanged", latest)

    diff = ""
    if previous is not None:
        diff = "".join(
            difflib.unified_diff(
                previous.splitlines(keepends=True),
                config.splitlines(keepends=True),
                fromfile=f"{device.name}/previous",
                tofile=f"{device.name}/{timestamp}",
            )
        )

    snapshot = device_dir / f"{timestamp}.cfg"
    snapshot.write_text(config, encoding="utf-8")
    latest.write_text(config, encoding="utf-8")
    return BackupResult(device.name, "new" if previous is None else "changed", snapshot, diff)


def run_backups(
    devices: list[Device],
    backup_dir: Path,
    workers: int = 8,
    fetcher: Callable[[Device], str] = fetch_config,
) -> list[BackupResult]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results: list[BackupResult] = []

    def job(device: Device) -> BackupResult:
        try:
            log.info("Connecting to %s (%s)", device.name, device.host)
            config = normalize_config(fetcher(device))
            return save_and_diff(device, config, backup_dir, timestamp)
        except Exception as exc:  # noqa: BLE001 - one bad device must not stop the rest
            log.error("%s failed: %s", device.name, exc)
            return BackupResult(device.name, "failed", error=str(exc))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(job, d) for d in devices]
        for fut in as_completed(futures):
            results.append(fut.result())

    return sorted(results, key=lambda r: r.device)
