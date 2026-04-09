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
"""Tests for agent tools."""

from unittest.mock import MagicMock, patch


class TestExecuteSqlTool:
    """Tests for ExecuteSqlTool."""

    def test_rejects_empty_sql(self):
        from superset.ai.tools.execute_sql import ExecuteSqlTool

        tool = ExecuteSqlTool(database_id=1)
        result = tool.run({"sql": ""})
        assert "Error" in result

    @patch("superset.ai.tools.execute_sql.SQLScript")
    @patch("superset.extensions.security_manager")
    @patch("superset.db")
    def test_rejects_mutating_sql(self, mock_db, mock_sm, mock_script_cls):
        from superset.ai.tools.execute_sql import ExecuteSqlTool

        mock_database = MagicMock()
        mock_database.backend = "sqlite"
        mock_db.session.query.return_value.filter_by.return_value.first.return_value = mock_database

        mock_script = MagicMock()
        mock_script.has_mutation.return_value = True
        mock_script_cls.return_value = mock_script

        tool = ExecuteSqlTool(database_id=1)
        result = tool.run({"sql": "DROP TABLE users"})
        assert "prohibited" in result

    @patch("superset.ai.tools.execute_sql.SQLScript")
    @patch("superset.extensions.security_manager")
    @patch("superset.db")
    def test_executes_select(self, mock_db, mock_sm, mock_script_cls):
        from superset.ai.tools.execute_sql import ExecuteSqlTool

        mock_script = MagicMock()
        mock_script.has_mutation.return_value = False
        mock_script_cls.return_value = mock_script

        # Mock database and query result
        mock_database = MagicMock()
        mock_database.backend = "sqlite"
        mock_db.session.query.return_value.filter_by.return_value.first.return_value = mock_database

        mock_result = MagicMock()
        mock_result.keys.return_value = ["id", "name"]
        mock_result.fetchmany.return_value = [(1, "Alice")]

        # conn.execution_options(max_rows=...).execute(...) → mock_result
        mock_exec_opts = MagicMock()
        mock_exec_opts.execute.return_value = mock_result

        mock_conn = MagicMock()
        mock_conn.execution_options.return_value = mock_exec_opts
        mock_conn.__enter__ = lambda self: self
        mock_conn.__exit__ = MagicMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.connect.return_value = mock_conn
        mock_engine.__enter__ = lambda self: self
        mock_engine.__exit__ = MagicMock(return_value=False)
        mock_database.get_sqla_engine.return_value = mock_engine

        tool = ExecuteSqlTool(database_id=1)
        result = tool.run({"sql": "SELECT id, name FROM users LIMIT 10"})

        # Should contain the column headers
        assert "id" in result or "1" in result

    @patch("superset.ai.tools.execute_sql.SQLScript")
    def test_rejects_unparseable_sql(self, mock_script_cls):
        from superset.ai.tools.execute_sql import ExecuteSqlTool

        mock_script_cls.side_effect = Exception("Parse error")

        tool = ExecuteSqlTool(database_id=1)
        result = tool.run({"sql": "INVALID SQL"})
        assert "Error" in result


class TestGetSchemaTool:
    """Tests for GetSchemaTool."""

    @patch("superset.ai.tools.get_schema.DatabaseDAO")
    def test_returns_error_for_missing_database(self, mock_dao):
        from superset.ai.tools.get_schema import GetSchemaTool

        mock_dao.get_database.return_value = None
        tool = GetSchemaTool(database_id=999)
        result = tool.run({})
        assert "not found" in result

    @patch("superset.ai.tools.get_schema.security_manager")
    @patch("superset.ai.tools.get_schema.DatabaseDAO")
    def test_fetches_table_metadata(self, mock_dao, mock_sm):
        from superset.ai.tools.get_schema import GetSchemaTool

        mock_sm.can_access_database.return_value = True
        mock_db = MagicMock()
        mock_db.database_name = "test_db"

        # Mock inspector context manager
        mock_inspector = MagicMock()
        mock_inspector.get_table_names.return_value = ["users", "orders"]
        mock_inspector.get_columns.side_effect = [
            [{"name": "id", "type": "INTEGER", "nullable": False}],
            [{"name": "id", "type": "INTEGER", "nullable": False}, {"name": "user_id", "type": "INTEGER", "nullable": True}],
        ]

        class FakeCtxMgr:
            def __enter__(self):
                return mock_inspector
            def __exit__(self, *args):
                pass

        mock_db.get_inspector.return_value = FakeCtxMgr()
        mock_dao.get_database.return_value = mock_db

        tool = GetSchemaTool(database_id=1)
        result = tool.run({"schema_name": "public"})

        assert "users" in result
        assert "orders" in result
        assert "test_db" in result
