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
"""Marshmallow schemas for AI Agent API."""

from marshmallow import Schema, fields, validate


class AiChatPostSchema(Schema):
    """Request body for ``POST /api/v1/ai/chat/``."""

    message = fields.String(required=True, validate=validate.Length(min=1, max=2000))
    database_id = fields.Integer(load_default=None)
    schema_name = fields.String(load_default=None)
    agent_type = fields.String(
        load_default="nl2sql",
        validate=validate.OneOf(["nl2sql", "chart", "debug", "dashboard", "copilot"]),
    )
    session_id = fields.String(load_default=None)


class AiEventsGetSchema(Schema):
    """Query params for ``GET /api/v1/ai/events/``."""

    channel_id = fields.String(required=True)
    last_id = fields.String(load_default=None)
