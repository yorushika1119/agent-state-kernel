from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = [
    "tests/test_pipeline_event_flow.py",
    "tests/test_manager_observer_views.py",
    "tests/test_observer_notifications.py",
    "tests/test_task_directory_router.py",
    "tests/test_dispatch_execution.py",
    "tests/test_requested_user_scenarios.py",
    "tests/test_smoke_interrupt.py",
]


if __name__ == "__main__":
    env = os.environ.copy()
    env["KERNEL_CREATE_LEGACY_STATE_TABLES"] = "0"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-o",
        "addopts=",
        "-q",
        *TESTS,
        *sys.argv[1:],
    ]
    raise SystemExit(subprocess.run(command, cwd=ROOT, env=env).returncode)
