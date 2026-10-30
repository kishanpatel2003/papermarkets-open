"""Main entry point for running validation against a data directory."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from papermarkets.validation.checks import run_all_checks
from papermarkets.validation.report import generate_report, print_summary


def validate(data_dir: Path, output_path: Path | None = None) -> dict:
    """Run all checks and produce a report.

    Returns the report dict. Optionally writes JSON to output_path.
    """
    issues, stats = run_all_checks(data_dir)
    report = generate_report(issues, stats)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2, default=str)

    return report


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]

    data_dir = Path(args[0]) if args else Path("data/output")
    if not data_dir.exists():
        print(f"Data directory not found: {data_dir}", file=sys.stderr)
        return 1

    output_path = Path(args[1]) if len(args) > 1 else None
    report = validate(data_dir, output_path)
    print_summary(report)

    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
