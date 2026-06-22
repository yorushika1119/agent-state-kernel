from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from test_tiers import CORE_TESTS, FAST_TESTS, INTEGRATION_TESTS, PYTEST_BASE_ARGS, TIER_TESTS


def test_test_tier_paths_exist():
    for tier, paths in TIER_TESTS.items():
        if tier == "full":
            assert paths == []
            continue
        missing = [path for path in paths if not (ROOT / path).exists()]
        assert missing == []


def test_core_tier_includes_fast_tier():
    assert set(FAST_TESTS).issubset(set(CORE_TESTS))


def test_integration_tier_includes_core_tier():
    assert set(CORE_TESTS).issubset(set(INTEGRATION_TESTS))


def test_tier_runner_clears_project_pytest_addopts():
    assert PYTEST_BASE_ARGS[:4] == ["-m", "pytest", "-o", "addopts="]
