# PaperMarkets

Collect prediction-market data and replay market orders against historical order books. Depth-aware fills account for available liquidity rather than assuming every trade executes at the displayed price.

## Components

- Concurrent API ingestion with retries, resumable progress, and Parquet storage
- DuckDB queries and data-quality checks
- Order-book walking, position tracking, simulation metrics, and coverage checks
- Live market monitoring

## Getting started

Python 3.12+. Run the standard-library fill example:

```sh
PYTHONPATH=src python3 scripts/order_fill.py
```

It buys 250 example shares across three ask levels. No API key or dataset needed.

Install the full environment and run the tests with [uv](https://docs.astral.sh/uv/):

```sh
uv sync --group dev
PYTHONPATH=src uv run python scripts/order_fill.py
PYTHONPATH=src uv run pytest -q
```

## Architecture

`ingest/` handles APIs and Parquet writes; `validation/` checks stored data; `queries/` isolates DuckDB access; `simulator/` composes book walking, position accounting, and results; `monitor/` consumes market feeds. All modules live under `src/papermarkets/`.

For ingestion, set `DOME_API_KEY` in a local `.env` file and check provider availability before collecting data.

## Current work

Full-dataset replay remains limited by query performance and incomplete historical coverage. Resolution inference and token-price handling still need end-to-end validation.

## License

[MIT](LICENSE)
