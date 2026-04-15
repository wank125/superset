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
"""Base class for webhook-based notification plugins (DingTalk, WeChat Work, etc.)."""

import logging
from typing import Any

from flask import g
from requests import Session

from superset.reports.notifications.base import BaseNotification
from superset.reports.notifications.exceptions import (
    NotificationMalformedException,
    NotificationParamException,
    NotificationUnprocessableException,
)
from superset.utils import json

logger = logging.getLogger(__name__)

_WEBHOOK_TIMEOUT = 10  # seconds


class WebhookNotification(BaseNotification):
    """Base class for webhook notification plugins.

    Subclasses must set:
      - type: the ReportRecipientType enum value
      - _build_payload(): return the JSON payload dict for the webhook
      - _is_success(): return True if the response indicates success
    """

    def _get_webhook_url(self) -> str:
        """Extract webhook URL from recipient config."""
        try:
            config = json.loads(self._recipient.recipient_config_json)
            url = config.get("target", "").strip()
        except (json.JSONDecodeError, AttributeError):
            raise NotificationParamException(
                f"Invalid {self.type} recipient config"
            )
        if not url:
            raise NotificationParamException(
                f"{self.type} webhook URL is empty"
            )
        return url

    def _build_payload(self) -> dict[str, Any]:
        """Build the webhook JSON payload. Override in subclass."""
        raise NotImplementedError

    def _is_success(self, result: dict[str, Any]) -> bool:
        """Check if webhook response indicates success. Override in subclass."""
        return result.get("errcode") == 0

    def _build_message_lines(self) -> list[str]:
        """Build common markdown message lines from notification content."""
        title = self._content.name or "Superset Alert"
        lines: list[str] = [f"### {title}"]

        if self._content.description:
            lines.append(self._content.description)

        if self._content.header_data:
            header = self._content.header_data
            if isinstance(header, dict):
                for key, value in header.items():
                    lines.append(f"- **{key}**: {value}")

        if self._content.url:
            lines.append(f"\n[查看详情]({self._content.url})")

        if self._content.text:
            lines.append(f"\n{self._content.text}")

        return lines

    def send(self) -> None:
        """Send notification to webhook."""
        global_logs_context = getattr(g, "logs_context", {}) or {}
        webhook_url = self._get_webhook_url()
        payload = self._build_payload()

        try:
            session = Session()
            resp = session.post(
                webhook_url,
                json=payload,
                timeout=_WEBHOOK_TIMEOUT,
            )
            result = resp.json()

            if not self._is_success(result):
                raise NotificationUnprocessableException(
                    f"{self.type} error: {result.get('errmsg', 'unknown')}"
                )

            logger.info(
                "Report sent to %s",
                self.type,
                extra={"execution_id": global_logs_context.get("execution_id")},
            )
        except NotificationParamException:
            raise
        except NotificationUnprocessableException:
            raise
        except Exception as ex:
            raise NotificationMalformedException(str(ex)) from ex
