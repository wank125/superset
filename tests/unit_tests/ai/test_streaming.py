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
"""Tests for AI streaming manager."""

import json
from unittest.mock import MagicMock, patch

from superset.ai.agent.events import AgentEvent


class TestAiStreamManager:
    """Tests for AiStreamManager."""

    @patch("superset.ai.streaming.manager._get_stream_cache")
    @patch("superset.ai.streaming.manager.get_stream_channel_prefix", return_value="ai-agent-")
    def test_publish_event(self, mock_prefix, mock_get_cache):
        from superset.ai.streaming.manager import AiStreamManager

        mock_cache = MagicMock()
        mock_get_cache.return_value = mock_cache

        manager = AiStreamManager()
        event = AgentEvent(type="text_chunk", data={"content": "Hello"})
        manager.publish_event("test-channel", event)

        mock_cache.xadd.assert_called_once()
        call_args = mock_cache.xadd.call_args
        assert call_args[0][0] == "ai-agent-test-channel"

    @patch("superset.ai.streaming.manager._get_stream_cache")
    @patch("superset.ai.streaming.manager.get_stream_channel_prefix", return_value="ai-agent-")
    def test_read_events(self, mock_prefix, mock_get_cache):
        from superset.ai.streaming.manager import AiStreamManager

        mock_cache = MagicMock()
        mock_cache.xrange.return_value = [
            ("12345-0", {
                "data": json.dumps({
                    "type": "text_chunk",
                    "data": {"content": "Hello"},
                }),
            }),
        ]
        mock_get_cache.return_value = mock_cache

        manager = AiStreamManager()
        events = manager.read_events("test-channel", last_id=None)

        assert len(events) == 1
        eid, event = events[0]
        assert eid == "12345-0"
        assert event.type == "text_chunk"
        assert event.data["content"] == "Hello"

    @patch("superset.ai.streaming.manager._get_stream_cache")
    @patch("superset.ai.streaming.manager.get_stream_channel_prefix", return_value="ai-agent-")
    def test_read_events_empty(self, mock_prefix, mock_get_cache):
        from superset.ai.streaming.manager import AiStreamManager

        mock_cache = MagicMock()
        mock_cache.xrange.return_value = []
        mock_get_cache.return_value = mock_cache

        manager = AiStreamManager()
        events = manager.read_events("test-channel", last_id=None)
        assert len(events) == 0

    @patch("superset.ai.streaming.manager._get_stream_cache", return_value=None)
    @patch("superset.ai.streaming.manager.get_stream_channel_prefix", return_value="ai-agent-")
    def test_publish_event_no_cache(self, mock_prefix, mock_get_cache):
        from superset.ai.streaming.manager import AiStreamManager

        manager = AiStreamManager()
        event = AgentEvent(type="text_chunk", data={"content": "Hello"})
        # Should not raise, just log a warning
        manager.publish_event("test-channel", event)

    @patch("superset.ai.streaming.manager._get_stream_cache", return_value=None)
    @patch("superset.ai.streaming.manager.get_stream_channel_prefix", return_value="ai-agent-")
    def test_read_events_no_cache(self, mock_prefix, mock_get_cache):
        from superset.ai.streaming.manager import AiStreamManager

        manager = AiStreamManager()
        events = manager.read_events("test-channel")
        assert events == []

    def test_increment_id(self):
        from superset.ai.streaming.manager import _increment_id

        assert _increment_id("12345-0") == "12345-1"
        assert _increment_id("12345-99") == "12345-100"
        assert _increment_id("invalid") == "-"
