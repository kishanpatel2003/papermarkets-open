"""Simple event bus for decoupling monitor core from UI layers."""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto

logger = logging.getLogger(__name__)


class EventType(Enum):
    TRADE = auto()
    FAST_TRADE = auto()
    ALERT = auto()
    BOOK_UPDATE = auto()
    STATUS = auto()
    WATCHLIST = auto()


@dataclass
class MonitorEvent:
    type: EventType
    data: dict


class EventBus:
    """Thread-safe publish/subscribe event bus."""

    def __init__(self) -> None:
        self._subscribers: dict[EventType, list[Callable[[MonitorEvent], None]]] = {}
        self._lock = threading.Lock()

    def subscribe(self, event_type: EventType, callback: Callable[[MonitorEvent], None]) -> None:
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(callback)

    def publish(self, event: MonitorEvent) -> None:
        with self._lock:
            callbacks = list(self._subscribers.get(event.type, []))
        for callback in callbacks:
            try:
                callback(event)
            except Exception:
                logger.exception("Error in event subscriber for %s", event.type)
