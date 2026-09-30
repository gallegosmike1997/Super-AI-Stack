"""Run the whole Super AI Stack locally with one command.

``Makefile.txt`` only worked in WSL and started services with ``&``, which left
orphaned processes and no way to stop them. This launcher works the same on
Windows, macOS and Linux: it starts each service with ``uvicorn``, records the
PIDs so ``stop`` can find them, and waits for ``/health`` on every port.

    python scripts/dev.py up          # start everything
    python scripts/dev.py up gateway  # start one service
    python scripts/dev.py status      # health-check every port
    python scripts/dev.py stop        # stop what this launcher started
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The launcher records what it started here. Process discovery via wmic/ps is
# best-effort only (wmic is being removed from Windows), so the file is the
# source of truth and discovery is just a safety net.
PID_FILE = ROOT / ".sas-dev.pids"

# Start in dependency order; the gateway and router come up last so the console
# has something to talk to as soon as it is reachable.
SERVICES: list[tuple[str, int]] = [
    ("memory", 8004),
    ("agent", 8009),
    ("llm_general", 8002),
    ("llm_coding", 8003),
    ("llm_reasoning", 8005),
    ("vision", 8006),
    ("speech", 8007),
    ("image_gen", 8008),
    ("model_manager", 8010),
    ("router", 8001),
    ("gateway", 8000),
]

STARTUP_TIMEOUT = 30.0
POLL_INTERVAL = 0.4


def load_dotenv() -> None:
    """Load ``.env`` into the environment without requiring python-dotenv."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        # Never clobber a value the caller already exported.
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def health_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/health"


def is_healthy(port: int, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(health_url(port), timeout=timeout) as response:  # noqa: S310
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def command_for(service: str, port: int) -> list[str]:
    host = os.getenv("SAS_HOST", "127.0.0.1")
    return [sys.executable, "-m", "uvicorn", f"{service}.main:app", "--host", host, "--port", str(port)]


def start(service: str, port: int) -> subprocess.Popen:
    env = os.environ.copy()
    # The shared package lives at the repository root.
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT), env.get("PYTHONPATH", "")]))
    process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        command_for(service, port),
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        start_new_session=os.name != "nt",
    )
    print(f"  started {service:<14} pid={process.pid:<7} port={port}")
    return process


def record_pid(pid: int) -> None:
    """Remember a started PID so ``stop`` works without process discovery."""
    try:
        with PID_FILE.open("a", encoding="utf-8") as handle:
            handle.write(f"{pid}\n")
    except OSError as exc:
        print(f"  could not record pid {pid}: {exc}", file=sys.stderr)


def read_recorded_pids() -> list[int]:
    if not PID_FILE.exists():
        return []
    pids: list[int] = []
    for line in PID_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def wait_for(port: int, process: subprocess.Popen, timeout: float = STARTUP_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if is_healthy(port):
            return True
        time.sleep(POLL_INTERVAL)
    return False


def cmd_up(names: list[str]) -> int:
    load_dotenv()
    known = {name for name, _ in SERVICES}
    unknown = set(names) - known
    if unknown:
        print(f"unknown service(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        print(f"available: {', '.join(sorted(known))}", file=sys.stderr)
        return 2

    wanted = [item for item in SERVICES if not names or item[0] in names]
    running: list[tuple[str, int, subprocess.Popen]] = []
    for service, port in wanted:
        if is_healthy(port):
            print(f"  {service:<14} already listening on {port}, skipping")
            continue
        process = start(service, port)
        record_pid(process.pid)
        running.append((service, port, process))

    failed: list[str] = []
    for service, port, process in running:
        if wait_for(port, process):
            print(f"  healthy  {service:<12} http://127.0.0.1:{port}")
        else:
            failed.append(service)
            print(f"  FAILED   {service:<12} not healthy within {STARTUP_TIMEOUT:.0f}s")

    if failed:
        print(f"\nnot healthy: {', '.join(failed)}", file=sys.stderr)
        print("start that service in the foreground to see its error.", file=sys.stderr)
        return 1

    if any(name == "gateway" for name, _ in running):
        print("\nconsole: http://127.0.0.1:8000/")
    print("stop with: python scripts/dev.py stop")

    try:
        while True:
            for service, _, process in running:
                if process.poll() is not None:
                    print(f"\n{service} exited; stopping the rest")
                    return 1
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nstopping...")
        return 0
    finally:
        stop_all()


def find_pids() -> list[int]:
    """PIDs of uvicorn processes serving this stack, on any platform."""
    pids: list[int] = []
    if os.name == "nt":
        try:
            output = subprocess.run(  # noqa: S603, S607
                ["wmic", "process", "where", "name='python.exe'", "get", "ProcessId,CommandLine"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return pids
        for line in output.splitlines()[1:]:
            if ".main:app" in line:
                digits = "".join(ch for ch in line.split()[-1] if ch.isdigit())
                if digits:
                    pids.append(int(digits))
        return pids

    try:
        output = subprocess.run(  # noqa: S603
            ["ps", "-eo", "pid,args"], capture_output=True, text=True, timeout=20, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return pids
    for line in output.splitlines()[1:]:
        if "uvicorn" in line and ".main:app" in line and "dev.py" not in line:
            head = line.strip().split()
            if head and head[0].isdigit():
                pids.append(int(head[0]))
    return pids


def stop_all() -> None:
    """Terminate every process this launcher started.

    Uses the recorded PID file first, then falls back to process discovery so a
    crashed launcher still leaves a way to clean up.
    """
    pids = read_recorded_pids()
    source = "pid file"
    if not pids:
        pids = find_pids()
        source = "process list"
    if not pids:
        print("no running stack processes found")
        return

    for pid in pids:
        try:
            if os.name == "nt":
                os.kill(pid, signal.SIGTERM)
            else:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            print(f"  stopped pid={pid}")
        except (OSError, ProcessLookupError):
            # Already gone is the normal case, not a failure.
            print(f"  pid={pid} already stopped")
    PID_FILE.unlink(missing_ok=True)
    print(f"({source})")
    time.sleep(0.5)


def cmd_stop() -> int:
    stop_all()
    return 0


def cmd_status() -> int:
    load_dotenv()
    width = max(len(name) for name, _ in SERVICES)
    failures = 0
    print(f"{'SERVICE':<{width}}  {'PORT':<6} STATUS")
    for service, port in SERVICES:
        healthy = is_healthy(port)
        failures += not healthy
        print(f"{service:<{width}}  {port:<6} {'ok' if healthy else 'down'}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    up = sub.add_parser("up", help="start the stack (or the named services)")
    up.add_argument("services", nargs="*", help="service names; default is all of them")
    sub.add_parser("stop", help="stop every stack process")
    sub.add_parser("status", help="health-check every service port")

    args = parser.parse_args(argv)
    if args.command == "up":
        return cmd_up(args.services)
    if args.command == "stop":
        return cmd_stop()
    if args.command == "status":
        return cmd_status()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
