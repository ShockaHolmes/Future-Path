from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PID_DIR = PROJECT_ROOT / ".dashboard_runtime"
LOG_DIR = PID_DIR / "logs"

DASHBOARD_CONFIG: dict[str, dict[str, str | int]] = {
    "overview": {
        "label": "Overview",
        "script": "dashboard/overview.py",
        "url": "http://localhost:8501",
        "port": 8501,
    },
    "profile_lookup": {
        "label": "Youth Profiles",
        "script": "dashboard/profile_lookup.py",
        "url": "http://localhost:8502",
        "port": 8502,
    },
    "ai_assistant": {
        "label": "AI Assistant",
        "script": "dashboard/ai_assistant.py",
        "url": "http://localhost:8503",
        "port": 8503,
    },
    "caseworker_dashboard": {
        "label": "Caseworker Dashboard",
        "script": "dashboard/caseworker_dashboard.py",
        "url": "http://localhost:8504",
        "port": 8504,
    },
    "youth_dashboard": {
        "label": "Youth Dashboard",
        "script": "dashboard/youth_dashboard.py",
        "url": "http://localhost:8505",
        "port": 8505,
    },
}


def _ensure_runtime_dirs() -> None:
    PID_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _pid_path(dashboard_key: str) -> Path:
    return PID_DIR / f"{dashboard_key}.pid"


def _log_path(dashboard_key: str) -> Path:
    return LOG_DIR / f"{dashboard_key}.log"


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid(dashboard_key: str) -> int | None:
    pid_file = _pid_path(dashboard_key)
    if not pid_file.exists():
        return None
    raw = pid_file.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _write_pid(dashboard_key: str, pid: int) -> None:
    _pid_path(dashboard_key).write_text(str(pid), encoding="utf-8")


def _clear_pid(dashboard_key: str) -> None:
    pid_file = _pid_path(dashboard_key)
    if pid_file.exists():
        pid_file.unlink(missing_ok=True)


def _is_port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def is_dashboard_running(dashboard_key: str) -> bool:
    if dashboard_key not in DASHBOARD_CONFIG:
        raise ValueError(f"Unknown dashboard key: {dashboard_key}")

    cfg = DASHBOARD_CONFIG[dashboard_key]
    port = int(cfg["port"])
    pid = _read_pid(dashboard_key)
    if pid is not None and _is_pid_alive(pid):
        return _is_port_listening(port)

    if pid is not None and not _is_pid_alive(pid):
        _clear_pid(dashboard_key)

    return False


def wait_for_dashboard(dashboard_key: str, timeout_seconds: float = 15.0) -> bool:
    cfg = DASHBOARD_CONFIG[dashboard_key]
    port = int(cfg["port"])
    started = time.time()
    while (time.time() - started) < timeout_seconds:
        if _is_port_listening(port):
            return True
        time.sleep(0.2)
    return False


def start_dashboard(dashboard_key: str, python_executable: str | None = None) -> bool:
    if dashboard_key not in DASHBOARD_CONFIG:
        raise ValueError(f"Unknown dashboard key: {dashboard_key}")

    _ensure_runtime_dirs()
    if is_dashboard_running(dashboard_key):
        return False

    cfg = DASHBOARD_CONFIG[dashboard_key]
    script_path = PROJECT_ROOT / str(cfg["script"])
    port = int(cfg["port"])

    python_bin = python_executable or sys.executable
    log_handle = _log_path(dashboard_key).open("a", encoding="utf-8")
    process = subprocess.Popen(
        [
            python_bin,
            "-m",
            "streamlit",
            "run",
            str(script_path),
            "--server.port",
            str(port),
            "--server.headless",
            "true",
        ],
        cwd=str(PROJECT_ROOT),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    _write_pid(dashboard_key, process.pid)
    return True


def stop_dashboard(dashboard_key: str, grace_seconds: float = 2.0, force: bool = False) -> bool:
    if dashboard_key not in DASHBOARD_CONFIG:
        raise ValueError(f"Unknown dashboard key: {dashboard_key}")

    pid = _read_pid(dashboard_key)
    if pid is None:
        return False

    stopped = False
    if _is_pid_alive(pid):
        if force:
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            stopped = True
        else:
            try:
                os.killpg(pid, signal.SIGTERM)
            except OSError:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass

            deadline = time.time() + grace_seconds
            while time.time() < deadline:
                if not _is_pid_alive(pid):
                    stopped = True
                    break
                time.sleep(0.1)

            if not stopped and _is_pid_alive(pid):
                try:
                    os.killpg(pid, signal.SIGKILL)
                except OSError:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
                stopped = True
    else:
        stopped = True

    _clear_pid(dashboard_key)
    return stopped


def stop_all_dashboards(except_keys: set[str] | None = None, force: bool = False) -> list[str]:
    keep = except_keys or set()
    stopped: list[str] = []
    for key in DASHBOARD_CONFIG:
        if key in keep:
            continue
        if stop_dashboard(key, force=force):
            stopped.append(key)
    return stopped


def _schedule_delayed_stop(dashboard_key: str, delay_seconds: float = 2.0) -> None:
    if dashboard_key not in DASHBOARD_CONFIG:
        return

    snippet = (
        "import time; "
        f"time.sleep({delay_seconds}); "
        "from dashboard_server_manager import stop_dashboard; "
        f"stop_dashboard('{dashboard_key}')"
    )
    subprocess.Popen(
        [sys.executable, "-c", snippet],
        cwd=str(PROJECT_ROOT / "src"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def switch_dashboard(target_key: str, current_key: str | None = None) -> str:
    if target_key not in DASHBOARD_CONFIG:
        raise ValueError(f"Unknown dashboard key: {target_key}")

    start_dashboard(target_key)
    wait_for_dashboard(target_key, timeout_seconds=20.0)

    keep = {target_key}
    if current_key and current_key != target_key:
        keep.add(current_key)
    stop_all_dashboards(except_keys=keep)

    if current_key and current_key != target_key:
        _schedule_delayed_stop(current_key, delay_seconds=2.5)

    return str(DASHBOARD_CONFIG[target_key]["url"])


def ensure_single_dashboard(current_key: str) -> None:
    if current_key not in DASHBOARD_CONFIG:
        raise ValueError(f"Unknown dashboard key: {current_key}")
    stop_all_dashboards(except_keys={current_key})


def dashboard_url(dashboard_key: str) -> str:
    if dashboard_key not in DASHBOARD_CONFIG:
        raise ValueError(f"Unknown dashboard key: {dashboard_key}")
    return str(DASHBOARD_CONFIG[dashboard_key]["url"])
