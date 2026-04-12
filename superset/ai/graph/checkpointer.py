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
"""LangGraph checkpointer factory — Redis-backed with MemorySaver fallback."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_checkpointer() -> Any:
    """Return a LangGraph checkpointer.

    Strategy:
    1. If ``AI_AGENT_CHECKPOINTER`` feature flag is disabled → ``MemorySaver``.
    2. Try ``langgraph-checkpoint-redis`` → ``RedisSaver`` with Superset's
       existing Redis connection config.
    3. On any failure → ``MemorySaver`` fallback (same-process only).
    """
    from superset import is_feature_enabled

    if not is_feature_enabled("AI_AGENT_CHECKPOINTER"):
        logger.debug("AI_AGENT_CHECKPOINTER disabled, using MemorySaver")
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()

    try:
        from langgraph.checkpoint.redis import RedisSaver

        redis_conn = _build_redis_client()
        if redis_conn is None:
            raise RuntimeError("Redis client unavailable")

        checkpointer = RedisSaver(redis_conn)
        checkpointer.setup()
        logger.info("LangGraph checkpointer: RedisSaver connected")
        return checkpointer
    except ImportError:
        logger.warning(
            "langgraph-checkpoint-redis not installed, falling back to MemorySaver. "
            "Install with: pip install langgraph-checkpoint-redis"
        )
    except Exception as exc:
        logger.warning(
            "Redis checkpointer failed (%s), falling back to MemorySaver", exc
        )

    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()


def _build_redis_client() -> Any:
    """Build a Redis client from Superset CACHE_CONFIG.

    Uses the same approach as
    :class:`superset.ai.streaming.manager.AiStreamManager`.
    """
    import redis as redis_lib

    from flask import current_app

    cache_config = current_app.config.get("CACHE_CONFIG", {})
    if cache_config.get("CACHE_TYPE") == "NullCache":
        # No real Redis configured in this environment
        return None

    host = cache_config.get("CACHE_REDIS_HOST", "redis")
    port = int(cache_config.get("CACHE_REDIS_PORT", 6379))
    db = int(cache_config.get("CACHE_REDIS_DB", 1))

    return redis_lib.Redis(
        host=host, port=port, db=db, socket_connect_timeout=3,
    )
