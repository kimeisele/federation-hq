#!/usr/bin/env python3
"""run_heavy_command.py — host-local heavy-command guard (v0.1, Issue #39).

Execution Resource Governance mechanical boundary: at most ONE heavy
verification command may run per HQ run ID. The wrapper owns, as one bounded
operation: the host-local exclusive lease, the pressure preflight, and the
child launch. No daemon, no Redis, no service, no database, no distributed
lock — an OS-managed advisory file lock (fcntl.flock) whose ownership dies
with the holding process.

Usage:
    python scripts/run_heavy_command.py --run-id <canonical run id> -- <heavy command...>

Exit codes:
    0      child exited 0 (or preflight OK and child exited 0)
    1      usage error
    2      child exited nonzero (child's code is propagated instead)
    3      BUSY: another heavy command already holds the lease for this run
    4      PRESSURED: pressure preflight refused the launch (lease released)
    130    interrupted (SIGINT) while the child ran

A stale lock FILE may remain in /tmp/federation-hq-heavy/ after a run —
harmless, because flock ownership is tied to the open file description and
process lifetime, never to the file's existence. Lease refusal is a
coordination outcome, not a semantic failure; sustained pressure may
eventually terminalize the run as blocked: environment_resource_exhaustion
through the existing bounded path.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

LOCK_DIR = "/tmp/federation-hq-heavy"
BUSY = 3
PRESSURED = 4


def sanitize_run_id(run_id: str) -> str:
    """Return a safe lock name; reject traversal/emptiness."""
    if not run_id or not run_id.strip():
        raise ValueError("run-id must be non-empty")
    if "/" in run_id or "\\" in run_id or ".." in run_id:
        raise ValueError("run-id must not contain path separators or '..'")
    safe = "".join(ch for ch in run_id if ch.isalnum() or ch in "._-")
    if not safe:
        raise ValueError("run-id contains no safe characters")
    return safe


def acquire_lock(run_id: str) -> int:
    """Open (creating if needed) and non-blocking flock the per-run lock file.

    Returns the open fd on success; raises OSError (EAGAIN/EACCES) when
    another heavy command already holds the lease for this run. The fd MUST
    be closed by the caller to release the lease.
    """
    safe = sanitize_run_id(run_id)
    os.makedirs(LOCK_DIR, exist_ok=True)
    lock_path = os.path.join(LOCK_DIR, safe + ".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise
    return fd


def pressure_status() -> int:
    """Run the pressure preflight, returning OK/UNKNOWN/PRESSURED (0/1/2)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import check_execution_pressure as pressure

    return pressure.decide(pressure._load_1m(), pressure._cpu_count(),
                           pressure._threshold())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Host-local heavy-command guard")
    parser.add_argument("--run-id", required=True, help="canonical HQ run id")
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="the heavy command to execute")
    args = parser.parse_args(argv)

    if not args.command or not args.command[0].strip():
        print("run_heavy_command: no command given", file=sys.stderr)
        return 1
    command = list(args.command)
    if command[0] == "--":
        command = command[1:]
    if not command or not command[0].strip():
        print("run_heavy_command: no command given", file=sys.stderr)
        return 1

    try:
        fd = acquire_lock(args.run_id)
    except ValueError as exc:
        print(f"run_heavy_command: invalid run-id: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print(f"run_heavy_command: BUSY — another heavy command holds the "
              f"lease for run {args.run_id!r}", file=sys.stderr)
        return BUSY

    try:
        status = pressure_status()
        if status == 2:
            print("run_heavy_command: PRESSURED — refusing to launch the "
                  "heavy command; lease released", file=sys.stderr)
            return PRESSURED
        try:
            result = subprocess.run(command)
            return result.returncode
        except KeyboardInterrupt:
            return 130
    finally:
        # Closing the fd releases the flock (and dies with this process).
        try:
            os.close(fd)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
