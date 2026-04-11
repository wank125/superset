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
"""REST API for AI Agent interactions."""

from __future__ import annotations

import logging
from typing import Any

from flask import request
from flask_appbuilder.api import expose, protect, rison, safe

from superset.views.base_api import BaseSupersetApi

from superset.ai.schemas import AiChatPostSchema, AiEventsGetSchema
from superset.ai.streaming.manager import AiStreamManager
from superset.ai.tasks import run_agent_task
from superset.utils import json

logger = logging.getLogger(__name__)

_chat_schema = AiChatPostSchema()
_events_schema = AiEventsGetSchema()


class AiAgentRestApi(BaseSupersetApi):
    """API endpoints for AI Agent chat and event streaming."""

    resource_name = "ai"
    class_permission_name = "AI Agent"
    openapi_spec_tag = "AI Agent"

    @expose("/chat/", methods=["POST"])
    @protect(allow_browser_login=True)
    @safe
    def chat(self) -> Any:
        """Start an AI agent conversation.
        ---
        post:
          summary: Send a message to the AI agent
          requestBody:
            required: true
            content:
              application/json:
                schema:
                  type: object
                  properties:
                    message:
                      type: string
                      description: Natural language message
                    database_id:
                      type: integer
                      description: Target database ID
                    agent_type:
                      type: string
                      description: Agent type (default "nl2sql")
                    session_id:
                      type: string
                      description: Conversation session ID
          responses:
            200:
              description: Channel ID for event streaming
              content:
                application/json:
                  schema:
                    type: object
                    properties:
                      channel_id:
                        type: string
            400:
              description: Invalid request
            404:
              description: AI Agent feature disabled
        """
        from flask import g
        from superset import is_feature_enabled

        if not is_feature_enabled("AI_AGENT"):
            return self.response_404()

        body = request.get_json(silent=True) or {}
        errors = _chat_schema.validate(body)
        if errors:
            return self.response_400(message=str(errors))

        # Enforce per-agent feature flags
        agent_type = body.get("agent_type", "nl2sql")
        if agent_type == "chart" and not is_feature_enabled(
            "AI_AGENT_CHART"
        ):
            return self.response_400(
                message="Chart agent is not enabled. Enable AI_AGENT_CHART feature flag."
            )
        if agent_type == "debug" and not is_feature_enabled(
            "AI_AGENT_DEBUG"
        ):
            return self.response_400(
                message="Debug agent is not enabled. Enable AI_AGENT_DEBUG feature flag."
            )
        if agent_type == "dashboard" and not is_feature_enabled(
            "AI_AGENT_DASHBOARD"
        ):
            return self.response_400(
                message="Dashboard agent is not enabled. Enable AI_AGENT_DASHBOARD feature flag."
            )

        import uuid

        channel_id = uuid.uuid4().hex
        run_agent_task.delay(
            {
                "channel_id": channel_id,
                "user_id": g.user.id if g.user else None,
                "message": body["message"],
                "database_id": body["database_id"],
                "schema_name": body.get("schema_name"),
                "agent_type": body.get("agent_type", "nl2sql"),
                "session_id": body.get("session_id"),
            }
        )

        return self.response(200, channel_id=channel_id)

    @expose("/events/", methods=["GET"])
    @protect(allow_browser_login=True)
    @safe
    def events(self) -> Any:
        """Poll for AI agent events.
        ---
        get:
          summary: Poll for streaming events
          parameters:
            - in: query
              name: channel_id
              schema:
                type: string
              required: true
            - in: query
              name: last_id
              schema:
                type: string
          responses:
            200:
              description: List of events since last_id
        """
        from superset import is_feature_enabled

        if not is_feature_enabled("AI_AGENT"):
            return self.response_404()

        args = {"channel_id": request.args.get("channel_id", "")}
        if request.args.get("last_id"):
            args["last_id"] = request.args.get("last_id")

        errors = _events_schema.validate(args)
        if errors:
            return self.response_400(message=str(errors))

        stream = AiStreamManager()
        events = stream.read_events(
            channel_id=args["channel_id"],
            last_id=args.get("last_id"),
        )

        result = [
            {
                "id": eid,
                "type": event.type,
                "data": event.data,
            }
            for eid, event in events
        ]
        last_id = events[-1][0] if events else args.get("last_id", "0")

        return self.response(200, events=result, last_id=last_id)
