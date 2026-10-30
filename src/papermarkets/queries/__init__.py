"""DuckDB-backed query library for Polymarket data."""

from papermarkets.queries.connection import get_connection
from papermarkets.queries.v0_queries import daily_volume, market_accuracy, price_chart
from papermarkets.queries.v1_queries import (
    orderbook_at_time,
    top_wallets,
    trades_by_wallet,
    trades_by_wallet_market,
    wallet_pnl,
)

__all__ = [
    "get_connection",
    "daily_volume",
    "market_accuracy",
    "price_chart",
    "orderbook_at_time",
    "top_wallets",
    "trades_by_wallet",
    "trades_by_wallet_market",
    "wallet_pnl",
]
