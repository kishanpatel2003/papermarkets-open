"""Tests for the ParquetWriter."""

from datetime import UTC, datetime

import pyarrow.parquet as pq

from papermarkets.ingest.writer import MARKETS_SCHEMA, PRICES_SCHEMA, ParquetWriter


def test_write_and_read_markets(tmp_path):
    path = tmp_path / "markets.parquet"
    writer = ParquetWriter(path, MARKETS_SCHEMA)
    writer.write_batch(
        [
            {
                "market_id": "0xabc",
                "question": "Will it rain?",
                "description": "Test market",
                "slug": "will-it-rain",
                "category": "weather",
                "tags": '["weather"]',
                "outcome_yes_token_id": "token1",
                "outcome_no_token_id": "token2",
                "neg_risk": False,
                "volume_total": 1234.56,
                "liquidity": 100.0,
                "created_at": datetime(2025, 1, 1, tzinfo=UTC),
                "end_date": datetime(2025, 12, 31, tzinfo=UTC),
                "resolved_at": None,
                "resolution_outcome": None,
                "active": True,
                "closed": False,
                "event_slug": "rain-event",
                "spread": 0.02,
                "best_bid": 0.49,
                "best_ask": 0.51,
            }
        ]
    )
    writer.close()

    table = pq.read_table(path)
    assert table.num_rows == 1
    assert table.column("market_id").to_pylist() == ["0xabc"]
    assert table.column("volume_total").to_pylist() == [1234.56]


def test_write_multiple_batches(tmp_path):
    path = tmp_path / "prices.parquet"
    writer = ParquetWriter(path, PRICES_SCHEMA)
    for i in range(3):
        writer.write_batch(
            [
                {
                    "market_id": "0xabc",
                    "token_id": "tok1",
                    "timestamp": datetime(2025, 1, 1, i, 0, tzinfo=UTC),
                    "mid_price": 0.5 + i * 0.01,
                }
            ]
        )
    writer.close()

    table = pq.read_table(path)
    assert table.num_rows == 3


def test_empty_batch_is_noop(tmp_path):
    path = tmp_path / "empty.parquet"
    writer = ParquetWriter(path, MARKETS_SCHEMA)
    writer.write_batch([])
    writer.close()
    assert not path.exists()  # Nothing written, no file created


def test_writes_to_tmp_then_renames(tmp_path):
    """Verify the writer uses a .tmp file during writes, then renames to final path on close."""
    path = tmp_path / "output.parquet"
    tmp_file = path.with_suffix(".parquet.tmp")
    writer = ParquetWriter(path, PRICES_SCHEMA)
    writer.write_batch(
        [
            {
                "market_id": "0xabc",
                "token_id": "tok1",
                "timestamp": datetime(2025, 1, 1, tzinfo=UTC),
                "mid_price": 0.5,
            }
        ]
    )
    # Before close: tmp file exists, final path does not
    assert tmp_file.exists()
    assert not path.exists()

    writer.close()
    # After close: final path exists, tmp file does not
    assert path.exists()
    assert not tmp_file.exists()

    table = pq.read_table(path)
    assert table.num_rows == 1


def test_interrupted_write_leaves_no_corrupt_final_file(tmp_path):
    """If the writer is never closed (simulating interruption), the final file is not created."""
    path = tmp_path / "output.parquet"
    writer = ParquetWriter(path, PRICES_SCHEMA)
    writer.write_batch(
        [
            {
                "market_id": "0xabc",
                "token_id": "tok1",
                "timestamp": datetime(2025, 1, 1, tzinfo=UTC),
                "mid_price": 0.5,
            }
        ]
    )
    # Simulate interruption — never call close()
    # The final path should NOT exist (only .tmp exists)
    assert not path.exists()
