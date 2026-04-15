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
"""Dynamic mapping between Superset table names and SuperSonic model IDs."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from superset.ai.semantic.supersonic_client import SuperSonicClient

logger = logging.getLogger(__name__)

# Cache: table_name → model_id
_table_to_model: dict[str, int] = {}
# Cache: model_id → table_name
_model_to_table: dict[int, str] = {}
_cache_loaded = False


def _load_cache(client: SuperSonicClient, domain_id: int | None) -> None:
    """Populate mapping cache from SuperSonic API."""
    global _cache_loaded
    if _cache_loaded:
        return

    models = client.get_models(domain_id)
    for model in models:
        model_id = model.get("id")
        name = model.get("name", "")
        if model_id is None or not name:
            continue
        _table_to_model[name] = model_id
        _model_to_table[model_id] = name

    _cache_loaded = True
    logger.debug(
        "SuperSonic model cache loaded: %d models", len(_table_to_model)
    )


def get_model_id_for_table(
    table_name: str,
    domain_id: int | None = None,
    client: SuperSonicClient | None = None,
) -> int | None:
    """Map a Superset table name to a SuperSonic model ID.

    Strategy:
    1. Exact match on model name
    2. Prefix match (table starts with model name)
    """
    if client is None:
        return None

    try:
        _load_cache(client, domain_id)
    except Exception:
        logger.debug("Failed to load model cache", exc_info=True)
        return None

    # Exact match
    if table_name in _table_to_model:
        return _table_to_model[table_name]

    # Prefix match: table 's2_pv_uv_statis' matches model 's2_pv_uv_statis'
    # or model name is a prefix of the table name
    for model_name, model_id in _table_to_model.items():
        if table_name.startswith(model_name) or model_name.startswith(table_name):
            return model_id

    return None


def get_table_for_model(model_id: int) -> str | None:
    """Reverse mapping: SuperSonic model ID → Superset table name."""
    return _model_to_table.get(model_id)


def clear_cache() -> None:
    """Clear the mapping cache (useful for testing)."""
    global _cache_loaded
    _table_to_model.clear()
    _model_to_table.clear()
    _cache_loaded = False
