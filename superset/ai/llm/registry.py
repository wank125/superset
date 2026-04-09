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
"""LLM provider registry and factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from superset.ai.config import get_default_provider_name, get_llm_providers
from superset.ai.llm.base import BaseLLMProvider

# Ensure providers are imported so they register themselves
import superset.ai.llm.openai_provider  # noqa: F401
import superset.ai.llm.anthropic_provider  # noqa: F401
import superset.ai.llm.ollama_provider  # noqa: F401

if TYPE_CHECKING:
    pass


def get_provider(name: str | None = None) -> BaseLLMProvider:
    """Return an initialised LLM provider by name.

    If *name* is ``None`` the default provider from config is used.
    """
    provider_name = name or get_default_provider_name()
    providers_config = get_llm_providers()

    if provider_name not in providers_config:
        raise ValueError(
            f"LLM provider '{provider_name}' not configured. "
            f"Available: {list(providers_config.keys())}"
        )

    for cls in BaseLLMProvider.plugins:
        if cls.provider_name == provider_name:
            return cls(providers_config[provider_name])

    raise ValueError(
        f"No implementation found for LLM provider '{provider_name}'. "
        f"Registered: {[p.provider_name for p in BaseLLMProvider.plugins]}"
    )
