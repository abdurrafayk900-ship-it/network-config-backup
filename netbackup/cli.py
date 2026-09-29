"""Command-line entry point: python -m netbackup"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

from . import __version__
from .backup import run_backups
from .inventory import InventoryError, load_inventory

ICONS = {"new": "+", "changed": "~", "unchanged": "=", "failed": "x"}


def git_commit(backup_dir: Path, message: str) -> None:
    """Version the backup folder with git so every change has history."""
    if not (backup_dir / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=backup_dir, check=True)
    subprocess.run(["git", "add", "-A"], cwd=backup_dir, check=True)
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=backup_dir, check=False)
    if staged.returncode != 0:
        subprocess.run(["git", "commit", "-q", "-m", message], cwd=backup_dir, check=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="netbackup", description=__doc__)
    p.add_argument("-i", "--inventory", default="inventory.yaml", help="Inventory YAML file")
    p.add_argument("-o", "--output", default="backups", help="Backup directory")
    p.add_argument("-g", "--group", help="Only back up devices in this group")
    p.add_argument("-w", "--workers", type=int, default=8, help="Parallel SSH sessions")
    p.add_argument("--git", action="store_true", help="Commit backups to a git repo in the output dir")
    p.add_argument("--show-diff", action="store_true", help="Print diffs for changed devices")
    p.add_argument("--report", help="Write a JSON summary to this file")
    p.add_argument("--dry-run", action="store_true", help="Validate inventory and list devices only")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )

    try:
        devices = load_inventory(args.inventory, args.group)
    except (InventoryError, FileNotFoundError) as exc:
        print(f"Inventory error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        for d in devices:
            print(f"{d.name:20} {d.host:16} {d.device_type:18} {d.backup_command}")
        print(f"\n{len(devices)} device(s) would be backed up.")
        return 0

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    results = run_backups(devices, out, workers=args.workers)

    print()
    for r in results:
        extra = f"  {r.error}" if r.error else ""
        print(f" [{ICONS[r.status]}] {r.device:20} {r.status}{extra}")
        if args.show_diff and r.diff:
            print(r.diff)

    counts = {s: sum(r.status == s for r in results) for s in ICONS}
    print(
        f"\n{len(results)} devices: {counts['new']} new, {counts['changed']} changed, "
        f"{counts['unchanged']} unchanged, {counts['failed']} failed"
    )

    if args.report:
        Path(args.report).write_text(
            json.dumps(
                [{"device": r.device, "status": r.status, "error": r.error, "diff": r.diff} for r in results],
                indent=2,
            ),
            encoding="utf-8",
        )

    if args.git and (counts["new"] or counts["changed"]):
        changed = [r.device for r in results if r.status in ("new", "changed")]
        git_commit(out, f"Config backup: {', '.join(changed)}")

    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
