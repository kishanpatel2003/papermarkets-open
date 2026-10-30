"""Shared configuration for the ingestion pipeline."""

import os
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[3]  # repo root
load_dotenv(_ROOT / ".env")

DOME_API_KEY: str = os.environ.get("DOME_API_KEY", "")
DOME_BASE_URL: str = "https://api.domeapi.io/v1"
GAMMA_BASE_URL: str = "https://gamma-api.polymarket.com"
CLOB_BASE_URL: str = "https://clob.polymarket.com"

OUTPUT_DIR: Path = _ROOT / "data" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
