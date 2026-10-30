"""Tests for the DuckDB connection factory."""

from pathlib import Path

from papermarkets.queries.connection import get_connection


class TestGetConnection:
    def test_creates_views_from_parquet(self, data_dir: Path):
        con = get_connection(data_dir)
        views = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        assert "markets" in views
        assert "trades" in views
        assert "orderbooks" in views
        assert "prices" in views

    def test_skips_missing_files(self, tmp_path: Path):
        # Empty directory — no Parquet files at all
        con = get_connection(tmp_path)
        views = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        assert len(views) == 0

    def test_markets_view_returns_rows(self, con):
        rows = con.execute("SELECT COUNT(*) FROM markets").fetchone()
        assert rows[0] == 3

    def test_trades_view_globs_across_files(self, con):
        rows = con.execute("SELECT COUNT(*) FROM trades").fetchone()
        assert rows[0] == 6  # 4 btc + 2 eth
