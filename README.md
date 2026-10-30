# PaperMarkets

A Python prototype for collecting prediction-market data and replaying market orders against historical order books. The core models depth-aware fills instead of assuming every trade executes at the displayed price.

## What is implemented

- Concurrent ingestion clients with retries, resumable progress and Parquet storage
- DuckDB analytical queries and data-quality checks
- Order-book walking, position tracking, simulation metrics and coverage checks
- An experimental live market monitor

## Run an offline example

Python 3.12+ is required. The small fill example uses only the Python standard library:

```sh
PYTHONPATH=src python3 scripts/demo.py
```

For all dependencies and the full synthetic test suite, use [uv](https://docs.astral.sh/uv/):

```sh
uv sync --group dev
PYTHONPATH=src uv run python scripts/demo.py
PYTHONPATH=src uv run pytest -q
```

The example buys 250 synthetic shares across three ask levels. It needs no key or external data. Tests use synthetic fixtures; no private datasets or real wallet fixtures are bundled.

## Architecture

`src/papermarkets/ingest` handles APIs and Parquet writes; `validation` checks stored data; `queries` isolates DuckDB access; `simulator` composes book walking, position accounting and results. `monitor` is an experimental consumer of market feeds.

For optional ingestion, put `DOME_API_KEY` in a local `.env` file. Never commit credentials. Ingestion endpoints and provider availability need verification before a new collection run; the offline demo is the supported first entry point.

## Current limits

This is research software. Historical data coverage was incomplete, and full real-dataset simulation was not verified end to end because of query-performance constraints. Resolution inference and token-price semantics require further validation. Unit tests establish component behavior, not trading profitability or production readiness. No real market dataset or private research notes are included in this source release.

## License

MIT
