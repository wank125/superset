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
"""Base LLM provider with plugin auto-registration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

from superset.ai.llm.types import LLMMessage, LLMResponse, LLMStreamChunk


class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers.

    Subclasses are automatically registered in ``BaseLLMProvider.plugins``
    via ``__init_subclass__``, mirroring the pattern used by
    ``superset.reports.notifications.base.BaseNotification``.
    """

    plugins: list[type[BaseLLMProvider]] = []
    provider_name: str = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.provider_name:  # only register concrete providers
            cls.plugins.append(cls)

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    @abstractmethod
    def chat(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Send messages and return a complete response."""

    @abstractmethod
    def chat_stream(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[LLMStreamChunk]:
        """Send messages and yield streaming chunks."""
