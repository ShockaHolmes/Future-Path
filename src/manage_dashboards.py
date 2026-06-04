from __future__ import annotations

import argparse
import sys

from dashboard_server_manager import (
    DASHBOARD_CONFIG,
    dashboard_url,
    ensure_single_dashboard,
    is_dashboard_running,
    start_dashboard,
    stop_all_dashboards,
    stop_dashboard,
    switch_dashboard,
    wait_for_dashboard,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage Future Path Streamlit dashboard servers.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Start one dashboard server")
    start_parser.add_argument("dashboard", choices=sorted(DASHBOARD_CONFIG.keys()))

    stop_parser = subparsers.add_parser("stop", help="Stop one dashboard server")
    stop_parser.add_argument("dashboard", choices=sorted(DASHBOARD_CONFIG.keys()))

    switch_parser = subparsers.add_parser("switch", help="Start target dashboard and stop others")
    switch_parser.add_argument("dashboard", choices=sorted(DASHBOARD_CONFIG.keys()))
    switch_parser.add_argument("--current", choices=sorted(DASHBOARD_CONFIG.keys()))

    subparsers.add_parser("stop-all", help="Stop all dashboard servers")

    status_parser = subparsers.add_parser("status", help="Show dashboard server status")
    status_parser.add_argument("--dashboard", choices=sorted(DASHBOARD_CONFIG.keys()))

    ensure_parser = subparsers.add_parser("ensure-single", help="Stop every server except the current one")
    ensure_parser.add_argument("dashboard", choices=sorted(DASHBOARD_CONFIG.keys()))

    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    if args.command == "start":
        started = start_dashboard(args.dashboard)
        ready = wait_for_dashboard(args.dashboard, timeout_seconds=20.0)
        print(f"started={started} ready={ready} url={dashboard_url(args.dashboard)}")
        return 0 if ready else 1

    if args.command == "stop":
        stopped = stop_dashboard(args.dashboard)
        print(f"stopped={stopped} dashboard={args.dashboard}")
        return 0

    if args.command == "switch":
        url = switch_dashboard(args.dashboard, current_key=args.current)
        print(url)
        return 0

    if args.command == "stop-all":
        stopped = stop_all_dashboards()
        print(f"stopped={','.join(stopped)}")
        return 0

    if args.command == "ensure-single":
        ensure_single_dashboard(args.dashboard)
        return 0

    if args.command == "status":
        keys = [args.dashboard] if args.dashboard else sorted(DASHBOARD_CONFIG.keys())
        for key in keys:
            print(f"{key}: {'running' if is_dashboard_running(key) else 'stopped'} ({dashboard_url(key)})")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
