# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""AI Agent stream manager using Redis Streams."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import Any

from flask import current_app

from superset.ai.agent.events import AgentEvent
from superset.ai.config import get_stream_channel_prefix

logger = logging.getLogger(__name__)


def _get_stream_cache() -> Any:
    """Return a Redis client for stream operations.

    Builds a Redis client from Superset's CACHE_CONFIG which is always
    properly configured for the deployment environment (Docker, etc.).
    """
    import redis as redis_lib

    cache_config = current_app.config.get("CACHE_CONFIG", {})
    host = cache_config.get("CACHE_REDIS_HOST", "redis")
    port = int(cache_config.get("CACHE_REDIS_PORT", 6379))
    db = int(cache_config.get("CACHE_REDIS_DB", 1))
    try:
        return redis_lib.Redis(host=host, port=port, db=db, socket_connect_timeout=3)
    except Exception:
        logger.warning("Could not create Redis client for AI streaming")
        return None


class AiStreamManager:
    """Publish and read AI agent events via Redis Streams.

    Uses the same cache backend infrastructure as the Global Async
    Queries (GAQ) system to ensure ``xadd``/``xrange`` are available.
    """

    _STREAM_LIMIT = 1000
    _STREAM_TTL = 3600  # auto-expire stream keys after 1 hour

    def __init__(self) -> None:
        self._cache = _get_stream_cache()
        self._prefix = get_stream_channel_prefix()

    def _stream_name(self, channel_id: str) -> str:
        return f"{self._prefix}{channel_id}"

    def publish_event(self, channel_id: str, event: AgentEvent) -> None:
        """Append an event to the Redis stream for *channel_id*."""
        if self._cache is None:
            logger.warning("No Redis stream cache available; event dropped")
            return
        name = self._stream_name(channel_id)
        payload = {"data": json.dumps(asdict(event))}
        self._cache.xadd(name, payload, "*", self._STREAM_LIMIT)
        # Auto-expire stream key on terminal events to prevent memory leak
        if event.type in ("done", "error"):
            try:
                self._cache.expire(name, self._STREAM_TTL)
            except Exception:
                pass

    def read_events(
        self, channel_id: str, last_id: str | None = None
    ) -> list[tuple[str, AgentEvent]]:
        """Read events from the stream since *last_id*.

        Returns a list of ``(event_id, AgentEvent)`` tuples.
        """
        if self._cache is None:
            return []
        name = self._stream_name(channel_id)
        start = _increment_id(last_id) if last_id else "0-0"
        raw = self._cache.xrange(name, start, "+", 100)
        results: list[tuple[str, AgentEvent]] = []
        for event_id, event_data in raw:
            # Redis may return bytes for both keys and values
            eid = event_id.decode("utf-8") if isinstance(event_id, bytes) else event_id

            # Normalise dict keys/values from bytes to str
            if isinstance(event_data, dict):
                decoded_data: dict[str, Any] = {}
                for k, v in event_data.items():
                    key = k.decode("utf-8") if isinstance(k, bytes) else k
                    val = v.decode("utf-8") if isinstance(v, bytes) else v
                    decoded_data[key] = val
                data_raw = decoded_data.get("data", "{}")
            else:
                data_raw = "{}"
            try:
                parsed = json.loads(data_raw)
                results.append((eid, AgentEvent(**parsed)))
            except (json.JSONDecodeError, TypeError):
                continue
        return results


def _increment_id(event_id: str) -> str:
    """Increment a Redis stream ID to read the next event after it.

    Redis xrange semantics:
    - "-" means the minimum possible ID (start of stream, reads everything)
    - "0-0" is the true minimum stream ID — also reads from the start,
      but expressed as a real ID so callers can advance past it.

    When last_id is "0" (the client's initial sentinel, not a real Redis ID),
    we use "0-0" so the first poll reads from the genuine beginning of the
    stream.  Subsequent polls use the last received event's real ID + 1, so
    events are never re-delivered.
    """
    if event_id in ("0", ""):
        # Initial poll — read from the very beginning of the stream.
        # "0-0" means: deliver from the first real entry onward.
        return "0-0"
    try:
        ts, seq = event_id.split("-")
        return f"{ts}-{int(seq) + 1}"
    except (ValueError, AttributeError):
        # Malformed ID — fall back to stream start (safer than silently dropping).
        return "0-0"
