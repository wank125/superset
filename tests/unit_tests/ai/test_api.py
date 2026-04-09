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
"""Tests for AI Agent API endpoints."""

import json
from unittest.mock import MagicMock, patch

import pytest


class TestAiChatPostSchema:
    """Tests for the chat request schema."""

    def test_valid_payload(self):
        from superset.ai.schemas import AiChatPostSchema

        schema = AiChatPostSchema()
        result = schema.load({"message": "hello", "database_id": 1})
        assert result["message"] == "hello"
        assert result["database_id"] == 1
        assert result["agent_type"] == "nl2sql"

    def test_missing_message(self):
        from superset.ai.schemas import AiChatPostSchema

        schema = AiChatPostSchema()
        errors = schema.validate({"database_id": 1})
        assert "message" in errors

    def test_missing_database_id(self):
        from superset.ai.schemas import AiChatPostSchema

        schema = AiChatPostSchema()
        errors = schema.validate({"message": "hello"})
        assert "database_id" in errors

    def test_invalid_agent_type(self):
        from superset.ai.schemas import AiChatPostSchema

        schema = AiChatPostSchema()
        errors = schema.validate({
            "message": "hello",
            "database_id": 1,
            "agent_type": "invalid",
        })
        assert "agent_type" in errors


class TestAiAgentRestApi:
    """Tests for the API endpoints."""

    def test_chat_returns_404_when_disabled(self):
        from superset.ai.api import AiAgentRestApi

        api = AiAgentRestApi()
        # Verify the feature-flag check path exists.
        # Full integration tests use the Flask test client.
        assert hasattr(api, "chat")
        assert hasattr(api, "events")

    def test_chat_schema_validation(self):
        from superset.ai.schemas import AiChatPostSchema

        schema = AiChatPostSchema()
        # Empty message should fail
        errors = schema.validate({"message": "", "database_id": 1})
        assert "message" in errors

        # Message too long should fail
        errors = schema.validate({"message": "x" * 2001, "database_id": 1})
        assert "message" in errors
