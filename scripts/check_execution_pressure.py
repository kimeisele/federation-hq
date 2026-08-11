#!/usr/bin/env python3
"""Federation HQ execution-pressure preflight (v0.1, Execution Resource Governance).

Harness-agnostic, no daemon/scheduler/telemetry. A worker SHOULD NOT start a
HEAVY command (repository-wide or very large suite, >~60s normal runtime,
substantial subprocess/CPU load) when this reports PRESSURED.

Exit codes / output:
    OK         (0)  load average available and below the threshold
    PRESSURED  (2)  load average available and at/above the threshold
    UNKNOWN    (1)  load average unavailable (portability), or host state
                    cannot be determined

Observed fields are printed as `key=value` lines so callers can log them:

    cpu_count=4
    load_1m=6.0
    normalized_load=1.5
    threshold=1.5

Threshold: repository-configured via env FHQ_HEAVY_LOAD_THRESHOLD (a float
>= 0.5), else the default 1.5 (normalized load = load_1m / cpu_count). The
default is a conservative v0.1 HEURISTIC for deciding whether to launch
another CPU-heavy repository-wide verification: a normalized load around 1.0
already means roughly one runnable/uninterruptible task per CPU on average,
so a NEW heavy job should preserve headroom. It is NOT a universal CPU-
utilization truth — load average is a pressure signal, not CPU utilization;
the repository/environment may override. UNKNOWN never blocks focused work.
No persistent state is created.
"""

from __future__ import annotations

import os
import sys

DEFAULT_THRESHOLD = 1.5
MIN_ACCEPTED_THRESHOLD = 0.5


def _cpu_count() -> int:
    try:
        return os.cpu_count() or 1
    except Exception:
        return 1


def _load_1m() -> float | None:
    """Return the 1-minute load average, or None where unavailable."""
    try:
        with open("/proc/loadavg", "r", encoding="utf-8") as fh:
            return float(fh.read().split()[0])
    except Exception:
        pass
    try:
        import ctypes
        import ctypes.util

        libc_name = ctypes.util.find_library("c")
        if not libc_name:
            return None
        libc = ctypes.CDLL(libc_name)
        getloadavg = libc.getloadavg
        getloadavg.restype = ctypes.c_int
        getloadavg.argtypes = [ctypes.POINTER(ctypes.c_double), ctypes.c_int]
        buf = (ctypes.c_double * 3)()
        if getloadavg(buf, 3) == 3:
            return float(buf[0])
        return None
    except Exception:
        return None


def _threshold() -> float:
    raw = os.environ.get("FHQ_HEAVY_LOAD_THRESHOLD", "")
    if raw:
        try:
            value = float(raw)
            if value >= MIN_ACCEPTED_THRESHOLD:
                return value
        except ValueError:
            pass
    return DEFAULT_THRESHOLD


def decide(load_1m: float | None, cpu_count: int, threshold: float) -> int:
    """0 = OK, 1 = UNKNOWN (load unavailable), 2 = PRESSURED."""
    if load_1m is None:
        return 1
    normalized = load_1m / cpu_count
    if normalized >= threshold:
        return 2
    return 0


def main() -> int:
    cpu = _cpu_count()
    load = _load_1m()
    threshold = _threshold()
    print(f"cpu_count={cpu}")
    if load is None:
        print("load_1m=unavailable")
        print("normalized_load=unavailable")
        print(f"threshold={threshold}")
        print("UNKNOWN")
        return 1
    normalized = load / cpu
    print(f"load_1m={load:.2f}")
    print(f"normalized_load={normalized:.2f}")
    print(f"threshold={threshold}")
    status = decide(load, cpu, threshold)
    if status == 2:
        print("PRESSURED")
        return 2
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
