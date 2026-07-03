"""Watch and relaunch the resumable full-market sync runner.

The underlying ``sync_full_market.py`` is intentionally simple: it processes
batches until completion or until it exits because the remaining symbols are
blocked/idle in that process.  Real provider calls can also terminate due to
network issues.  This watcher wraps the runner and relaunches it with the same
state file so long unattended syncs can continue without manual restarts.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from time import sleep
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--interval", type=float, default=0.35)
    parser.add_argument("--max-failures", type=int, default=3)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--state-file", type=Path, default=Path("full-market-sync.state.json"))
    parser.add_argument("--pid-file", type=Path, default=Path("full-market-sync.pid"))
    parser.add_argument("--restart-delay", type=float, default=30.0)
    parser.add_argument("--max-restarts", type=int, default=50)
    parser.add_argument("--runner", type=Path, default=Path("sync_full_market.py"))
    return parser.parse_args()


def build_runner_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        str(args.runner),
        "--start-date",
        args.start_date.isoformat(),
        "--end-date",
        args.end_date.isoformat(),
        "--batch-size",
        str(args.batch_size),
        "--interval",
        str(args.interval),
        "--max-failures",
        str(args.max_failures),
        "--state-file",
        str(args.state_file),
    ]
    if args.retry_failed:
        command.append("--retry-failed")
    return command


def read_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def should_stop_watching(state: dict[str, Any] | None, return_code: int) -> tuple[bool, str]:
    if state is None:
        return False, "state-missing"
    exit_reason = state.get("exit_reason")
    progress = state.get("progress") or {}
    remaining = int(progress.get("remaining", 1))
    if exit_reason == "completed" or remaining == 0:
        return True, "completed"
    # A non-zero child return with blocked/idle can still be recoverable after
    # delay because the next process has a fresh in-memory circuit breaker.
    if return_code == 0 and exit_reason not in {None, "continue"}:
        return True, str(exit_reason)
    return False, str(exit_reason or "continue")


def write_pid(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n", encoding="utf-8")


def run_watcher(args: argparse.Namespace) -> int:
    if args.max_restarts < 0:
        raise ValueError("max_restarts must be >= 0")
    if args.restart_delay < 0:
        raise ValueError("restart_delay must be >= 0")

    command = build_runner_command(args)
    restarts = 0
    while True:
        child = subprocess.Popen(command)
        write_pid(args.pid_file, child.pid)
        return_code = child.wait()
        state = read_state(args.state_file)
        stop, reason = should_stop_watching(state, return_code)
        print(
            json.dumps(
                {
                    "child_pid": child.pid,
                    "return_code": return_code,
                    "watch_reason": reason,
                    "restart_count": restarts,
                    "state_progress": (state or {}).get("progress"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if stop:
            return return_code
        if restarts >= args.max_restarts:
            return return_code if return_code != 0 else 3
        restarts += 1
        if args.restart_delay > 0:
            sleep(args.restart_delay)


def main() -> int:
    return run_watcher(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
