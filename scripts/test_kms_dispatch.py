from __future__ import annotations

import sys

from test_tiers import run_tier


if __name__ == "__main__":
    raise SystemExit(run_tier("kms-dispatch", sys.argv[1:]))
