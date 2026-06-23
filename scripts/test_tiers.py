from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTEST_BASE_ARGS = ["-m", "pytest", "-o", "addopts=", "-q"]

FAST_TESTS = [
    "tests/test_intent_classifier.py",
    "tests/test_task_switch_coordinator.py",
    "tests/test_thinker_dispatch_coordinator.py",
    "tests/test_dispatch_lifecycle_coordinator.py",
    "tests/test_notification_coordinator.py",
    "tests/test_route_clarification_coordinator.py",
    "tests/test_kernel_session_coordinator.py",
    "tests/test_kernel_direct_reply_coordinator.py",
    "tests/test_runtime_event_adapter.py",
    "tests/test_state_source_audit.py",
    "tests/test_state_primary_read_switch.py",
    "tests/test_legacy_state_migration.py",
    "tests/test_task_conversation_refs.py",
    "tests/test_test_tiers.py",
]

KMS_FAST_TESTS = [
    "tests/test_intent_classifier.py",
    "tests/test_task_switch_coordinator.py",
    "tests/test_thinker_dispatch_coordinator.py",
    "tests/test_dispatch_lifecycle_coordinator.py",
    "tests/test_dispatch_preparation.py",
    "tests/test_dispatch_response.py",
    "tests/test_kms_manager_components.py",
    "tests/test_route_clarification_coordinator.py",
    "tests/test_kernel_session_coordinator.py",
    "tests/test_kernel_direct_reply_coordinator.py",
]

KMS_DISPATCH_TESTS = [
    *KMS_FAST_TESTS,
    "tests/test_dispatch_execution.py",
    "tests/test_manager_observer_views.py",
    "tests/test_task_conversation_refs.py",
]

KMS_INTEGRATION_TESTS = [
    *KMS_DISPATCH_TESTS,
    "tests/test_task_directory_router.py",
    "tests/test_smoke_interrupt.py",
]

CORE_TESTS = [
    *FAST_TESTS,
    "tests/test_task_directory_router.py",
    "tests/test_manager_observer_views.py",
    "tests/test_state_alias_and_thinker_dispatch.py",
    "tests/test_observer_notifications.py",
]

INTEGRATION_TESTS = [
    *CORE_TESTS,
    "tests/test_pipeline_event_flow.py",
    "tests/test_requested_user_scenarios.py",
    "tests/test_smoke_interrupt.py",
    "tests/test_architecture_ab_experiment.py",
]

TIER_TESTS = {
    "fast": FAST_TESTS,
    "kms-fast": KMS_FAST_TESTS,
    "kms-dispatch": KMS_DISPATCH_TESTS,
    "kms-integration": KMS_INTEGRATION_TESTS,
    "core": CORE_TESTS,
    "integration": INTEGRATION_TESTS,
    "full": [],
}


def run_tier(tier: str, pytest_args: list[str] | None = None) -> int:
    if tier not in TIER_TESTS:
        raise ValueError(f"unknown test tier: {tier}")
    test_paths = TIER_TESTS[tier]
    command = [
        sys.executable,
        *PYTEST_BASE_ARGS,
        *test_paths,
        *(pytest_args or []),
    ]
    return subprocess.run(command, cwd=ROOT).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run agent-state-kernel test tiers.")
    parser.add_argument("tier", choices=sorted(TIER_TESTS))
    args, pytest_args = parser.parse_known_args(argv)
    return run_tier(args.tier, pytest_args)


if __name__ == "__main__":
    raise SystemExit(main())
