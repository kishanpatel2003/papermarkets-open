"""V0 analytics queries."""

import duckdb


def price_chart(
    con: duckdb.DuckDBPyConnection,
    market_slug: str,
) -> duckdb.DuckDBPyRelation:
    """Price history for a market, ordered by time."""
    return con.execute(
        """
        SELECT p.timestamp, p.mid_price, p.token_id
        FROM prices p
        JOIN markets m ON p.market_id = m.market_id
        WHERE m.slug = $slug
        ORDER BY p.timestamp
        """,
        {"slug": market_slug},
    )


def daily_volume(con: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyRelation:
    """Total trade volume across all markets by day."""
    return con.execute("""
        SELECT
            CAST(timestamp AS DATE) AS day,
            SUM(price * size) AS volume,
            COUNT(*) AS trade_count
        FROM trades
        GROUP BY day
        ORDER BY day
    """)


def market_accuracy(con: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyRelation:
    """For resolved markets, compare the final price vs actual outcome.

    Uses the last known price from the prices table for each market and
    compares it to the resolution (1.0 for Yes, 0.0 for No).
    """
    return con.execute("""
        WITH last_price AS (
            SELECT
                p.market_id,
                p.mid_price,
                ROW_NUMBER() OVER (PARTITION BY p.market_id ORDER BY p.timestamp DESC) AS rn
            FROM prices p
        )
        SELECT
            m.market_id,
            m.question,
            m.resolution_outcome,
            lp.mid_price AS final_price,
            CASE WHEN m.resolution_outcome = 'Yes' THEN 1.0 ELSE 0.0 END AS outcome_value,
            ABS(lp.mid_price - CASE WHEN m.resolution_outcome = 'Yes'
                THEN 1.0 ELSE 0.0 END) AS prediction_error
        FROM markets m
        JOIN last_price lp ON m.market_id = lp.market_id AND lp.rn = 1
        WHERE m.resolution_outcome IS NOT NULL
        ORDER BY prediction_error
    """)
