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
"""WeChat Work (企业微信) webhook notification for Superset alerts and reports."""

from typing import Any

from superset.reports.models import ReportRecipientType
from superset.reports.notifications.webhook_base import WebhookNotification


class WeChatWorkNotification(WebhookNotification):
    """Send alert/report notifications via WeChat Work group robot webhook."""

    type = ReportRecipientType.WECHAT_WORK

    def _build_payload(self) -> dict[str, Any]:
        """Build WeChat Work markdown message payload."""
        lines = self._build_message_lines()
        return {
            "msgtype": "markdown",
            "markdown": {
                "content": "\n".join(lines),
            },
        }
