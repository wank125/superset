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
"""Docker-specific Superset config overrides for AI Agent support."""

import os

from celery.schedules import crontab

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
REDIS_CELERY_DB = os.getenv("REDIS_CELERY_DB", "0")
REDIS_RESULTS_DB = os.getenv("REDIS_RESULTS_DB", "1")

# ---------------------------------------------------------------------------
# AI Agent Feature Flags
# ---------------------------------------------------------------------------
FEATURE_FLAGS = {
    "ALERT_REPORTS": True,
    "AI_AGENT": True,
    "AI_AGENT_NL2SQL": True,
}

# ---------------------------------------------------------------------------
# AI LLM Configuration — Local LM Studio (GLM-4.7-Flash)
# ---------------------------------------------------------------------------
AI_LLM_DEFAULT_PROVIDER = "openai"

AI_LLM_PROVIDERS = {
    "openai": {
        "api_key_env": "LM_STUDIO_API_KEY",
        "model": "zai-org/glm-4.7-flash",
        "temperature": 0.0,
        "max_tokens": 4096,
        "base_url": "http://host.docker.internal:1234/v1",
    },
}

AI_AGENT_MAX_TURNS = 10
AI_AGENT_TIMEOUT = 120
AI_AGENT_STREAM_CHANNEL_PREFIX = "ai-agent-"

# ---------------------------------------------------------------------------
# Celery: include AI agent task in imports
# ---------------------------------------------------------------------------


class CeleryConfig:
    broker_url = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_CELERY_DB}"
    imports = (
        "superset.sql_lab",
        "superset.tasks.scheduler",
        "superset.tasks.thumbnails",
        "superset.tasks.cache",
        "superset.ai.tasks",
    )
    result_backend = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_RESULTS_DB}"
    worker_prefetch_multiplier = 1
    task_acks_late = False
    beat_schedule = {
        "reports.scheduler": {
            "task": "reports.scheduler",
            "schedule": crontab(minute="*", hour="*"),
        },
        "reports.prune_log": {
            "task": "reports.prune_log",
            "schedule": crontab(minute=10, hour=0),
        },
    }


CELERY_CONFIG = CeleryConfig
