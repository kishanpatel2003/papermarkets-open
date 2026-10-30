"""Parquet writer with predefined schemas for each entity type."""

import hashlib
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

# macOS HFS+/APFS has a 255-byte filename limit. Leave room for ".parquet.tmp" (12 chars).
_MAX_STEM = 200


def safe_filename(slug: str) -> str:
    """Truncate slug to a filesystem-safe length, appending a hash if truncated."""
    if len(slug) <= _MAX_STEM:
        return slug
    digest = hashlib.md5(slug.encode()).hexdigest()[:8]
    return f"{slug[: _MAX_STEM - 9]}_{digest}"


# ── Schema definitions matching the ingestion schema ──

MARKETS_SCHEMA = pa.schema(
    [
        ("market_id", pa.string()),
        ("question", pa.string()),
        ("description", pa.string()),
        ("slug", pa.string()),
        ("category", pa.string()),
        ("tags", pa.string()),  # JSON array
        ("outcome_yes_token_id", pa.string()),
        ("outcome_no_token_id", pa.string()),
        ("neg_risk", pa.bool_()),
        ("volume_total", pa.float64()),
        ("liquidity", pa.float64()),
        ("created_at", pa.timestamp("ms", tz="UTC")),
        ("end_date", pa.timestamp("ms", tz="UTC")),
        ("resolved_at", pa.timestamp("ms", tz="UTC")),
        ("resolution_outcome", pa.string()),
        ("active", pa.bool_()),
        ("closed", pa.bool_()),
        ("event_slug", pa.string()),
        ("spread", pa.float64()),
        ("best_bid", pa.float64()),
        ("best_ask", pa.float64()),
    ]
)

TRADES_SCHEMA = pa.schema(
    [
        ("trade_id", pa.string()),
        ("market_id", pa.string()),
        ("token_id", pa.string()),
        ("token_label", pa.string()),
        ("side", pa.string()),
        ("price", pa.float64()),
        ("size", pa.float64()),
        ("timestamp", pa.timestamp("s", tz="UTC")),
        ("user_address", pa.string()),
        ("taker_address", pa.string()),
        ("tx_hash", pa.string()),
        ("block_number", pa.int64()),
    ]
)

ORDERBOOK_SCHEMA = pa.schema(
    [
        ("market_id", pa.string()),
        ("token_id", pa.string()),
        ("timestamp", pa.timestamp("ms", tz="UTC")),
        ("bids", pa.string()),  # JSON array
        ("asks", pa.string()),  # JSON array
        ("best_bid", pa.float64()),
        ("best_ask", pa.float64()),
        ("spread", pa.float64()),
        ("mid_price", pa.float64()),
        ("bid_depth", pa.int32()),
        ("ask_depth", pa.int32()),
        ("total_bid_size", pa.float64()),
        ("total_ask_size", pa.float64()),
        ("book_hash", pa.string()),
    ]
)

PRICES_SCHEMA = pa.schema(
    [
        ("market_id", pa.string()),
        ("token_id", pa.string()),
        ("timestamp", pa.timestamp("s", tz="UTC")),
        ("mid_price", pa.float64()),
    ]
)


class ParquetWriter:
    """Buffered Parquet file writer.

    Writes to a temporary .tmp file, then atomically renames to the final path
    on close(). This prevents corrupt Parquet files from interrupted writes —
    an incomplete write leaves a .tmp file instead of a broken final file.
    """

    def __init__(self, output_path: Path, schema: pa.Schema):
        self._path = output_path
        self._tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        self._schema = schema
        self._writer: pq.ParquetWriter | None = None
        output_path.parent.mkdir(parents=True, exist_ok=True)

    def write_batch(self, records: list[dict]) -> None:
        if not records:
            return
        # Build columns from the schema field names, filling missing with None
        arrays = {}
        for field in self._schema:
            values = [r.get(field.name) for r in records]
            arrays[field.name] = values
        table = pa.table(arrays, schema=self._schema)
        if self._writer is None:
            self._writer = pq.ParquetWriter(str(self._tmp_path), self._schema)
        self._writer.write_table(table)

    def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            self._writer = None
            self._tmp_path.rename(self._path)
