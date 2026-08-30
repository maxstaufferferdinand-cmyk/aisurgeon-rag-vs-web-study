#!/usr/bin/env python3
"""Run or resume the 20-call pilot or human-frozen 800-cell main study."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aisurgeon_decentralised.study_finalization import finalize_main_study
from aisurgeon_decentralised.study_phase2 import HumanQuestionFreezeRequired
from aisurgeon_decentralised.study_runner import (
    CostLimitApprovalRequired,
    ModelIdentityMismatch,
    StudyRunner,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("pilot", "main", "resume"))
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    try:
        runner = StudyRunner(root=args.root)
        if args.command == "pilot":
            result = runner.run_pilot()
        else:
            result = runner.run_main()
            if result.get("recorded_results") == 800:
                result["finalization"] = finalize_main_study(root=args.root)
    except HumanQuestionFreezeRequired as exc:
        print(
            json.dumps({"status": exc.status, "detail": str(exc)}, ensure_ascii=False)
        )
        return 3
    except CostLimitApprovalRequired as exc:
        print(
            json.dumps({"status": exc.status, "detail": str(exc)}, ensure_ascii=False)
        )
        return 4
    except ModelIdentityMismatch as exc:
        print(
            json.dumps({"status": exc.status, "detail": str(exc)}, ensure_ascii=False)
        )
        return 5
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
