from __future__ import annotations

import argparse
import subprocess
import time

from dashboard_server_manager import DASHBOARD_CONFIG, is_dashboard_running, stop_all_dashboards


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stop dashboard servers when all dashboard clients are closed.")
    parser.add_argument("--idle-seconds", type=float, default=20.0)
    parser.add_argument("--startup-grace-seconds", type=float, default=45.0)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    return parser.parse_args()


def _port_has_established_clients(port: int) -> bool:
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:ESTABLISHED"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        # If lsof is unavailable, avoid accidental shutdown.
        return True

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return len(lines) > 1


def _any_dashboard_client_connected() -> bool:
    for key, cfg in DASHBOARD_CONFIG.items():
        if is_dashboard_running(key):
            if _port_has_established_clients(int(cfg["port"])):
                return True
    return False


def _any_dashboard_running() -> bool:
    return any(is_dashboard_running(key) for key in DASHBOARD_CONFIG)


def main() -> int:
    args = _parse_args()
    started_at = time.time()
    idle_since: float | None = None

    while True:
        if not _any_dashboard_running():
            return 0

        now = time.time()
        if (now - started_at) < args.startup_grace_seconds:
            time.sleep(args.poll_seconds)
            continue

        if _any_dashboard_client_connected():
            idle_since = None
        else:
            if idle_since is None:
                idle_since = now
            elif (now - idle_since) >= args.idle_seconds:
                stop_all_dashboards(force=True)
                return 0

        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
