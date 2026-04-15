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
"""Tests for DingTalk and WeChat Work notification plugins."""

from unittest.mock import MagicMock, patch

import pytest

from superset.reports.models import ReportRecipientType
from superset.reports.notifications.dingtalk import DingTalkNotification
from superset.reports.notifications.wechat_work import WeChatWorkNotification
from superset.reports.notifications.base import NotificationContent
from superset.reports.notifications.exceptions import (
    NotificationParamException,
    NotificationUnprocessableException,
)


def _make_recipient(webhook_url: str = "https://example.com/webhook") -> MagicMock:
    """Create a mock ReportRecipients with webhook URL."""
    import json

    recipient = MagicMock()
    recipient.recipient_config_json = json.dumps({"target": webhook_url})
    recipient.type = ReportRecipientType.DINGTALK
    return recipient


def _make_content() -> NotificationContent:
    """Create a basic NotificationContent."""
    return NotificationContent(
        name="Test Alert",
        header_data={"value": "123"},
        description="Test description",
        url="http://localhost:8088/superset/dashboard/1/",
    )


class TestDingTalkNotification:
    @patch("superset.reports.notifications.webhook_base.Session")
    def test_send_success(self, mock_session_cls):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"errcode": 0}
        mock_session_cls.return_value.post.return_value = mock_resp

        notification = DingTalkNotification(_make_recipient(), _make_content())
        notification.send()  # should not raise

        mock_session_cls.return_value.post.assert_called_once()
        call_kwargs = mock_session_cls.return_value.post.call_args[1]
        assert call_kwargs["timeout"] == 10
        payload = call_kwargs["json"]
        assert payload["msgtype"] == "markdown"
        assert "Test Alert" in payload["markdown"]["title"]

    def test_empty_webhook_url_raises(self):
        recipient = _make_recipient("")
        notification = DingTalkNotification(recipient, _make_content())
        with pytest.raises(NotificationParamException):
            notification._get_webhook_url()

    @patch("superset.reports.notifications.webhook_base.Session")
    def test_send_api_error_raises(self, mock_session_cls):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"errcode": 400001, "errmsg": "invalid token"}
        mock_session_cls.return_value.post.return_value = mock_resp

        notification = DingTalkNotification(_make_recipient(), _make_content())
        with pytest.raises(NotificationUnprocessableException, match="invalid token"):
            notification.send()


class TestWeChatWorkNotification:
    @patch("superset.reports.notifications.webhook_base.Session")
    def test_send_success(self, mock_session_cls):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"errcode": 0, "errmsg": "ok"}
        mock_session_cls.return_value.post.return_value = mock_resp

        notification = WeChatWorkNotification(_make_recipient(), _make_content())
        notification.send()  # should not raise

        payload = mock_session_cls.return_value.post.call_args[1]["json"]
        assert payload["msgtype"] == "markdown"
        assert "Test Alert" in payload["markdown"]["content"]

    @patch("superset.reports.notifications.webhook_base.Session")
    def test_payload_uses_content_key(self, mock_session_cls):
        """WeChat Work uses 'content' key, not 'text'."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"errcode": 0}
        mock_session_cls.return_value.post.return_value = mock_resp

        notification = WeChatWorkNotification(_make_recipient(), _make_content())
        notification.send()

        payload = mock_session_cls.return_value.post.call_args[1]["json"]
        assert "content" in payload["markdown"]
        assert "text" not in payload["markdown"]
