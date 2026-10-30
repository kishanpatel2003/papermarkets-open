"""V1 copy-trading queries."""

from datetime import datetime

import duckdb


def trades_by_wallet(
    con: duckdb.DuckDBPyConnection,
    wallet: str,
    start: datetime | None = None,
    end: datetime | None = None,
) -> duckdb.DuckDBPyRelation:
    """All trades by a wallet address, ordered by timestamp."""
    query = "SELECT * FROM trades WHERE user_address = $wallet"
    params: dict = {"wallet": wallet}
    if start:
        query += " AND timestamp >= $start"
        params["start"] = start
    if end:
        query += " AND timestamp <= $end"
        params["end"] = end
    query += " ORDER BY timestamp"
    return con.execute(query, params)


def trades_by_wallet_market(
    con: duckdb.DuckDBPyConnection,
    wallet: str,
    market_slug: str,
    start: datetime | None = None,
    end: datetime | None = None,
) -> duckdb.DuckDBPyRelation:
    """Trades by a wallet filtered to a specific market slug.

    Joins trades with markets to filter by slug, since trades store market_id
    (condition_id) rather than slug directly.
    """
    query = """
        SELECT t.*
        FROM trades t
        JOIN markets m ON t.market_id = m.market_id
        WHERE t.user_address = $wallet
          AND m.slug = $slug
    """
    params: dict = {"wallet": wallet, "slug": market_slug}
    if start:
        query += " AND t.timestamp >= $start"
        params["start"] = start
    if end:
        query += " AND t.timestamp <= $end"
        params["end"] = end
    query += " ORDER BY t.timestamp"
    return con.execute(query, params)


def orderbook_at_time(
    con: duckdb.DuckDBPyConnection,
    market_slug: str,
    timestamp: datetime,
) -> duckdb.DuckDBPyRelation:
    """Nearest orderbook snapshot to a given timestamp for slippage estimation.

    Returns the single snapshot closest in time (before or after) for the market.
    """
    return con.execute(
        """
        SELECT o.*
        FROM orderbooks o
        JOIN markets m ON o.market_id = m.market_id
        WHERE m.slug = $slug
        ORDER BY abs(epoch_ms(o.timestamp) - epoch_ms($ts::TIMESTAMP))
        LIMIT 1
        """,
        {"slug": market_slug, "ts": timestamp},
    )


def wallet_pnl(
    con: duckdb.DuckDBPyConnection,
    wallet: str,
) -> duckdb.DuckDBPyRelation:
    """P&L for a wallet across all resolved markets.

    For each resolved market the wallet traded in:
    - Computes net shares (buys minus sells) per token
    - Values the position at resolution (1.0 if the token's label matches
      the market's resolution_outcome, 0.0 otherwise)
    - Subtracts cost basis (sum of price * size for buys, minus for sells)
    """
    return con.execute(
        """
        WITH positions AS (
            SELECT
                t.market_id,
                t.token_label,
                SUM(CASE WHEN t.side = 'BUY' THEN t.size ELSE -t.size END) AS net_shares,
                SUM(CASE WHEN t.side = 'BUY' THEN t.price * t.size
                         ELSE -t.price * t.size END) AS cost_basis
            FROM trades t
            WHERE t.user_address = $wallet
            GROUP BY t.market_id, t.token_label
        )
        SELECT
            p.market_id,
            m.question,
            m.resolution_outcome,
            p.token_label,
            p.net_shares,
            p.cost_basis,
            CASE WHEN m.resolution_outcome = p.token_label
                 THEN p.net_shares * 1.0
                 ELSE p.net_shares * 0.0
            END AS payout,
            CASE WHEN m.resolution_outcome = p.token_label
                 THEN p.net_shares * 1.0
                 ELSE p.net_shares * 0.0
            END - p.cost_basis AS pnl
        FROM positions p
        JOIN markets m ON p.market_id = m.market_id
        WHERE m.resolution_outcome IS NOT NULL
        ORDER BY pnl DESC
        """,
        {"wallet": wallet},
    )


def top_wallets(
    con: duckdb.DuckDBPyConnection,
    metric: str = "volume",
    n: int = 20,
) -> duckdb.DuckDBPyRelation:
    """Top N wallets by volume, trade_count, or pnl."""
    if metric == "volume":
        return con.execute(
            """
            SELECT
                user_address,
                SUM(price * size) AS total_volume,
                COUNT(*) AS trade_count
            FROM trades
            GROUP BY user_address
            ORDER BY total_volume DESC
            LIMIT $n
            """,
            {"n": n},
        )
    elif metric == "trade_count":
        return con.execute(
            """
            SELECT
                user_address,
                COUNT(*) AS trade_count,
                SUM(price * size) AS total_volume
            FROM trades
            GROUP BY user_address
            ORDER BY trade_count DESC
            LIMIT $n
            """,
            {"n": n},
        )
    elif metric == "pnl":
        return con.execute(
            """
            WITH positions AS (
                SELECT
                    t.user_address,
                    t.market_id,
                    t.token_label,
                    SUM(CASE WHEN t.side = 'BUY' THEN t.size ELSE -t.size END)
                        AS net_shares,
                    SUM(CASE WHEN t.side = 'BUY' THEN t.price * t.size
                             ELSE -t.price * t.size END) AS cost_basis
                FROM trades t
                GROUP BY t.user_address, t.market_id, t.token_label
            )
            SELECT
                p.user_address,
                SUM(
                    CASE WHEN m.resolution_outcome = p.token_label
                         THEN p.net_shares ELSE 0.0 END
                    - p.cost_basis
                ) AS total_pnl
            FROM positions p
            JOIN markets m ON p.market_id = m.market_id
            WHERE m.resolution_outcome IS NOT NULL
            GROUP BY p.user_address
            ORDER BY total_pnl DESC
            LIMIT $n
            """,
            {"n": n},
        )
    else:
        raise ValueError(f"Unknown metric: {metric!r}. Use 'volume', 'trade_count', or 'pnl'.")


def orderbook_after_time(
    con: duckdb.DuckDBPyConnection,
    market_id: str,
    token_id: str,
    after: datetime,
    max_staleness_ms: int = 600_000,
) -> dict | None:
    """First orderbook snapshot AFTER the given time, within staleness limit.

    Uses a bounded range query (not a full-table sort).
    Returns a dict with the row's columns, or None if no snapshot exists.
    """
    after_ms = int(after.timestamp() * 1000)
    upper_ms = after_ms + max_staleness_ms
    row = con.execute(
        """
        SELECT *
        FROM orderbooks
        WHERE market_id = $market_id
          AND token_id = $token_id
          AND epoch_ms(timestamp) >= $after_ms
          AND epoch_ms(timestamp) <= $upper_ms
        ORDER BY timestamp
        LIMIT 1
        """,
        {
            "market_id": market_id,
            "token_id": token_id,
            "after_ms": after_ms,
            "upper_ms": upper_ms,
        },
    ).fetchone()
    if row is None:
        return None
    columns = [desc[0] for desc in con.description]
    return dict(zip(columns, row, strict=True))


def wallet_market_coverage(
    con: duckdb.DuckDBPyConnection,
    wallet: str,
) -> list[dict]:
    """For each market the wallet traded, report whether orderbook data exists.

    Returns list of dicts: market_id, market_slug, trade_count,
    has_orderbook_data, is_closed.
    """
    rows = con.execute(
        """
        WITH wallet_markets AS (
            SELECT
                t.market_id,
                COUNT(*) AS trade_count
            FROM trades t
            WHERE t.user_address = $wallet
            GROUP BY t.market_id
        )
        SELECT
            wm.market_id,
            m.slug AS market_slug,
            wm.trade_count,
            EXISTS(
                SELECT 1 FROM orderbooks o WHERE o.market_id = wm.market_id
            ) AS has_orderbook_data,
            m.closed AS is_closed
        FROM wallet_markets wm
        JOIN markets m ON wm.market_id = m.market_id
        ORDER BY wm.trade_count DESC
        """,
        {"wallet": wallet},
    ).fetchall()
    columns = ["market_id", "market_slug", "trade_count", "has_orderbook_data", "is_closed"]
    return [dict(zip(columns, row, strict=True)) for row in rows]
