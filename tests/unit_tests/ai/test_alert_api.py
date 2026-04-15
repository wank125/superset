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
"""Tests for AI alert SQL validation and API helpers."""

import pytest

from superset.ai.alert.api import _is_safe_alert_sql, _extract_table_name


class TestIsSafeAlertSql:
    def test_select_allowed(self):
        assert _is_safe_alert_sql("SELECT SUM(amount) FROM orders")

    def test_select_with_limit(self):
        assert _is_safe_alert_sql(
            "SELECT COUNT(*) FROM users WHERE active = 1 LIMIT 1"
        )

    def test_insert_blocked(self):
        assert not _is_safe_alert_sql(
            "INSERT INTO users (name) VALUES ('evil')"
        )

    def test_delete_blocked(self):
        assert not _is_safe_alert_sql("DELETE FROM users")

    def test_drop_blocked(self):
        assert not _is_safe_alert_sql("DROP TABLE users")

    def test_empty_sql_blocked(self):
        assert not _is_safe_alert_sql("")

    def test_non_select_blocked(self):
        assert not _is_safe_alert_sql("UPDATE users SET admin = true")

    def test_case_insensitive(self):
        assert _is_safe_alert_sql("select count(*) from orders")

    def test_injection_with_semicolon_blocked(self):
        assert not _is_safe_alert_sql(
            "SELECT 1; DROP TABLE users"
        )

    def test_insert_in_subquery_blocked(self):
        assert not _is_safe_alert_sql(
            "SELECT * FROM (INSERT INTO users VALUES (1)) AS x"
        )


class TestExtractTableName:
    def test_string(self):
        assert _extract_table_name("my_table") == "my_table"

    def test_tuple(self):
        assert _extract_table_name(("schema", "my_table")) == "my_table"

    def test_object_with_table_attr(self):
        obj = type("Table", (), {"table": "my_table"})()
        assert _extract_table_name(obj) == "my_table"

    def test_fallback_to_str(self):
        assert _extract_table_name(42) == "42"
