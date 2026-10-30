"""Report generation from validation results."""

from __future__ import annotations

from datetime import UTC, datetime

from papermarkets.validation.checks import Issue


def generate_report(issues: list[Issue], stats: dict) -> dict:
    """Produce a structured JSON-serializable quality report."""
    counts = {"error": 0, "warning": 0, "info": 0}
    for issue in issues:
        counts[issue.level] = counts.get(issue.level, 0) + 1

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "total_issues": len(issues),
            "errors": counts["error"],
            "warnings": counts["warning"],
            "info": counts["info"],
            "passed": counts["error"] == 0,
        },
        "stats": stats,
        "issues": [
            {
                "level": i.level,
                "check": i.check_name,
                "message": i.message,
                "details": i.details,
            }
            for i in issues
        ],
    }


def print_summary(report: dict) -> None:
    """Print a human-readable summary to stdout."""
    summary = report["summary"]
    stats = report["stats"]

    status = "PASSED" if summary["passed"] else "FAILED"
    print(f"\nValidation {status}")
    print(
        f"  {summary['errors']} error(s), {summary['warnings']} warning(s), {summary['info']} info"
    )
    print()

    if stats.get("total_markets"):
        print(f"Markets: {stats['total_markets']}")
        print(f"  with trades:     {stats.get('markets_with_trades', 0)}")
        print(f"  with orderbooks: {stats.get('markets_with_orderbooks', 0)}")
        print(f"  with prices:     {stats.get('markets_with_prices', 0)}")
        print()
        print(
            f"Rows: {stats.get('total_trade_rows', 0)} trades, "
            f"{stats.get('total_orderbook_rows', 0)} orderbook snapshots, "
            f"{stats.get('total_price_rows', 0)} prices"
        )

    file_sizes = stats.get("file_sizes", {})
    if file_sizes:
        print()
        print("File sizes:")
        for name, size in sorted(file_sizes.items()):
            if size < 1024:
                print(f"  {name}: {size} B")
            elif size < 1024 * 1024:
                print(f"  {name}: {size / 1024:.1f} KB")
            else:
                print(f"  {name}: {size / (1024 * 1024):.1f} MB")

    if summary["errors"] > 0 or summary["warnings"] > 0:
        print()
        print("Issues:")
        for issue in report["issues"]:
            if issue["level"] == "info":
                continue
            prefix = "ERROR" if issue["level"] == "error" else "WARN"
            print(f"  [{prefix}] {issue['message']}")
