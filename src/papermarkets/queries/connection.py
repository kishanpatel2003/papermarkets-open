"""DuckDB connection factory with Parquet-backed views."""

from pathlib import Path

import duckdb


def get_connection(data_dir: Path) -> duckdb.DuckDBPyConnection:
    """Create a DuckDB connection with views over the Parquet data layout.

    Views created:
        markets — reads markets.parquet
        trades — reads trades/**/*.parquet
        orderbooks — reads orderbook_snapshots/**/*.parquet
        prices — reads prices/**/*.parquet
    """
    con = duckdb.connect()

    markets_path = data_dir / "markets.parquet"
    if markets_path.exists():
        con.execute(f"CREATE VIEW markets AS SELECT * FROM read_parquet('{markets_path}')")

    trades_glob = str(data_dir / "trades" / "**" / "*.parquet")
    if list(data_dir.glob("trades/**/*.parquet")):
        con.execute(
            f"CREATE VIEW trades AS SELECT * FROM read_parquet('{trades_glob}',"
            " union_by_name=true, hive_partitioning=false)"
        )

    ob_glob = str(data_dir / "orderbook_snapshots" / "**" / "*.parquet")
    if list(data_dir.glob("orderbook_snapshots/**/*.parquet")):
        con.execute(
            f"CREATE VIEW orderbooks AS SELECT * FROM read_parquet('{ob_glob}',"
            " union_by_name=true, hive_partitioning=false)"
        )

    prices_glob = str(data_dir / "prices" / "**" / "*.parquet")
    if list(data_dir.glob("prices/**/*.parquet")):
        con.execute(
            f"CREATE VIEW prices AS SELECT * FROM read_parquet('{prices_glob}',"
            " union_by_name=true, hive_partitioning=false)"
        )

    return con
