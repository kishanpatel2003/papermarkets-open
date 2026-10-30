"""Tests for report generation."""

from __future__ import annotations

from papermarkets.validation.checks import Issue
from papermarkets.validation.report import generate_report, print_summary


def test_generate_report_structure() -> None:
    issues = [
        Issue("error", "schema", "missing column x"),
        Issue("warning", "schema", "unexpected column y"),
        Issue("info", "completeness", "no orderbooks for 3 markets"),
    ]
    stats = {"total_markets": 10, "file_sizes": {}}
    report = generate_report(issues, stats)

    assert report["summary"]["total_issues"] == 3
    assert report["summary"]["errors"] == 1
    assert report["summary"]["warnings"] == 1
    assert report["summary"]["info"] == 1
    assert report["summary"]["passed"] is False
    assert report["stats"]["total_markets"] == 10
    assert len(report["issues"]) == 3


def test_report_passes_with_no_errors() -> None:
    issues = [Issue("warning", "schema", "extra column")]
    report = generate_report(issues, {})
    assert report["summary"]["passed"] is True


def test_print_summary_runs(capsys) -> None:
    report = generate_report(
        [Issue("error", "schema", "bad column")],
        {
            "total_markets": 5,
            "markets_with_trades": 3,
            "markets_with_orderbooks": 2,
            "markets_with_prices": 4,
            "total_trade_rows": 100,
            "total_orderbook_rows": 50,
            "total_price_rows": 200,
            "file_sizes": {"markets.parquet": 2048},
        },
    )
    print_summary(report)
    output = capsys.readouterr().out
    assert "FAILED" in output
    assert "Markets: 5" in output
    assert "bad column" in output
