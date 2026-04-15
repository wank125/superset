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
"""Prompt templates for AI alert rule generation and evaluation."""

ALERT_GENERATION_PROMPT = """\
You are an expert SQL alert rule generator for Apache Superset.
Given a natural language monitoring request and the database schema, generate an alert configuration.

Database: {database_name}
Schema:
{schema_text}

User request: {message}

Generate a JSON alert configuration with these fields:
{{
  "name": "short descriptive alert name in Chinese",
  "description": "one-line description of what this alert monitors",
  "sql": "SELECT ... -- must return exactly one row with one numeric column",
  "validator_type": "operator" | "not null" | "AI",
  "validator_config_json": {{}},
  "crontab": "cron expression (e.g. '0 9 * * *' for daily 9am)",
  "database_id": {database_id}
}}

Rules:
- SQL must return exactly ONE row with ONE numeric column
- Use aggregate functions: SUM, COUNT, AVG, MAX, MIN
- For threshold comparisons (e.g. "大于100", "低于50%"), use:
  validator_type="operator", validator_config_json={{"op": ">=", "threshold": 100}}
- For existence checks (e.g. "有异常数据"), use:
  validator_type="not null", validator_config_json={{}}
- For complex semantic conditions that can't be expressed as simple comparisons (e.g. "波动异常", "趋势不对"), use:
  validator_type="AI", validator_config_json={{"prompt": "描述告警条件"}}
- Default schedule: daily at 9am ("0 9 * * *"), adjust based on user intent
- Output ONLY valid JSON, no markdown fences or explanation
"""

ALERT_EVALUATOR_PROMPT = """\
You are an alert condition evaluator. Given SQL query results and a condition, determine if the alert should fire.

SQL Query Result:
{result_text}

User Alert Condition: {prompt}

Analyze the data and condition. Respond with ONLY "true" if the alert should fire, or "false" if it should not.
Do not include any explanation or other text.
"""
