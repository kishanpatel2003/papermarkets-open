"""Web dashboard for the insider trading monitor."""

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from papermarkets.monitor.config import EventConfig
from papermarkets.monitor.events import EventBus, EventType, MonitorEvent
from papermarkets.monitor.state import MonitorState

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"

app = FastAPI(title="PaperMarkets Monitor")

# These get set by the launcher before starting uvicorn.
_state: MonitorState | None = None
_config: EventConfig | None = None
_event_bus: EventBus | None = None


def init_web(state: MonitorState, config: EventConfig, event_bus: EventBus) -> FastAPI:
    """Initialize the web app with shared monitor state."""
    global _state, _config, _event_bus  # noqa: PLW0603
    _state = state
    _config = config
    _event_bus = event_bus
    return app


def _truncate_address(addr: str) -> str:
    if len(addr) > 10:
        return f"{addr[:6]}...{addr[-4:]}"
    return addr


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    template_path = _TEMPLATE_DIR / "dashboard.html"
    return template_path.read_text()


@app.get("/api/state")
async def get_state() -> dict:
    if _state is None or _config is None:
        return {"error": "Monitor not initialized"}

    outcomes = sorted(_config.active_outcomes, key=lambda o: o.volume, reverse=True)
    dashboard_rows = []
    for o in outcomes:
        price = _state.current_prices.get(o.yes_token_id, o.yes_price or 0)
        high = med = low = 0
        for a in _state.alerts:
            if a.outcome_name == o.name:
                if a.level == "critical":
                    high += 1
                elif a.level == "warning":
                    med += 1
                else:
                    low += 1
        top_wallet = ""
        top_score = 0.0
        for addr, sus in _state.watchlist.items():
            if sus.outcome_name == o.name and sus.score > top_score:
                top_score = sus.score
                top_wallet = _truncate_address(addr)
        dashboard_rows.append(
            {
                "name": o.name,
                "price": round(price, 3),
                "volume": o.volume,
                "high": high,
                "med": med,
                "low": low,
                "top_suspect": f"{top_wallet} ({top_score:.2f})" if top_wallet else None,
            }
        )

    watchlist = [
        {
            "address": _truncate_address(s.address),
            "score": round(s.score, 2),
            "outcome": s.outcome_name,
            "reasons": s.reasons[:5],
        }
        for s in sorted(_state.watchlist.values(), key=lambda s: s.score, reverse=True)
    ]

    alerts = [
        {
            "level": a.level,
            "title": a.title,
            "body": a.body,
            "timestamp": a.timestamp,
            "wallet": _truncate_address(a.wallet) if a.wallet else "",
            "outcome": a.outcome_name,
        }
        for a in reversed(_state.alerts[-50:])
    ]

    return {
        "event_title": _config.event_title,
        "dashboard": dashboard_rows,
        "watchlist": watchlist,
        "alerts": alerts,
    }


@app.get("/api/events")
async def event_stream() -> EventSourceResponse:
    """SSE endpoint for live updates."""
    queue: asyncio.Queue[MonitorEvent] = asyncio.Queue()

    def _enqueue(event: MonitorEvent) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(event)

    if _event_bus is None:

        async def _empty():
            yield {"data": json.dumps({"error": "not initialized"})}

        return EventSourceResponse(_empty())

    for etype in EventType:
        _event_bus.subscribe(etype, _enqueue)

    async def _generate():
        while True:
            event = await queue.get()
            payload = {"type": event.type.name.lower(), "data": event.data}
            yield {"data": json.dumps(payload, default=str)}

    return EventSourceResponse(_generate())


@app.get("/api/health")
async def health() -> dict:
    now = datetime.now(tz=UTC).isoformat()
    return {"status": "ok", "timestamp": now}
