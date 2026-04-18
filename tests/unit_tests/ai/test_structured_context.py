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
"""Tests for structured AI conversation context protocol."""

from superset.ai.agent.structured_context import (
    build_dataset_context,
    dump_context,
    extract_table_from_sql,
    load_context,
    read_latest_context,
)


def test_dump_and_load_dataset_context():
    content = dump_context(
        build_dataset_context(
            table_name="birth_names",
            sql="SELECT * FROM birth_names",
            database_id=1,
            schema_name="public",
            datasource_id=42,
        )
    )

    parsed = load_context(content, expected_kind="dataset_context")

    assert parsed is not None
    assert parsed["version"] == 1
    assert parsed["kind"] == "dataset_context"
    assert parsed["table_name"] == "birth_names"
    assert parsed["schema_name"] == "public"
    assert parsed["datasource_id"] == 42


def test_load_context_rejects_wrong_kind():
    content = dump_context(
        build_dataset_context(
            table_name="birth_names",
            sql="SELECT * FROM birth_names",
            database_id=1,
            schema_name=None,
        )
    )

    assert load_context(content, expected_kind="query_context") is None


def test_read_latest_context_skips_freeform_summaries():
    older = dump_context(
        build_dataset_context(
            table_name="old_table",
            sql="SELECT * FROM old_table",
            database_id=1,
            schema_name=None,
        )
    )
    newer = dump_context(
        build_dataset_context(
            table_name="new_table",
            sql="SELECT * FROM new_table",
            database_id=1,
            schema_name=None,
        )
    )
    history = [
        {"role": "tool_summary", "tool": "dataset_context", "content": older},
        {"role": "tool_summary", "tool": "execute_sql", "content": "SQL: ..."},
        {"role": "tool_summary", "tool": "dataset_context", "content": newer},
    ]

    parsed = read_latest_context(history, "dataset_context")

    assert parsed is not None
    assert parsed["table_name"] == "new_table"


def test_extract_table_from_sql_handles_schema_and_quotes():
    sql = 'SELECT SUM(num) FROM "public"."birth_names" WHERE gender = \'F\''

    assert extract_table_from_sql(sql) == "birth_names"


def test_extract_table_from_sql_handles_simple_cte():
    sql = """
    WITH base AS (
      SELECT * FROM raw_birth_names
    )
    SELECT gender, SUM(num) FROM birth_names GROUP BY gender
    """

    assert extract_table_from_sql(sql) == "birth_names"
