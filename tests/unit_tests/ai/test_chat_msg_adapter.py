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
"""Unit tests for Phase Merge-1: inline chart data adapter and backend helpers."""

import unittest

from superset.ai.graph.runner import _parse_text_table_for_event
from superset.ai.graph.nodes_child import _generate_suggest_questions


class TestParseTextTableForEvent(unittest.TestCase):
    """Tests for the text-table parser in runner.py."""

    def test_basic_table(self) -> None:
        text = (
            "name  | revenue\n"
            "------|--------\n"
            "Alice | 100\n"
            "Bob   | 200\n"
        )
        result = _parse_text_table_for_event(text)
        self.assertIsNotNone(result)
        self.assertEqual(len(result["columns"]), 2)
        self.assertEqual(result["columns"][0]["name"], "name")
        self.assertEqual(result["columns"][1]["type"], "FLOAT")
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["rows"][0]["revenue"], 100)

    def test_empty_text(self) -> None:
        result = _parse_text_table_for_event("")
        self.assertIsNone(result)

    def test_no_separator(self) -> None:
        text = "just a header\nno separator\n"
        result = _parse_text_table_for_event(text)
        self.assertIsNone(result)

    def test_datetime_detection(self) -> None:
        text = (
            "date       | sales\n"
            "-----------|-------\n"
            "2024-01-01 | 100\n"
            "2024-01-02 | 150\n"
        )
        result = _parse_text_table_for_event(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["columns"][0]["type"], "DATETIME")
        self.assertTrue(result["columns"][0]["is_dttm"])

    def test_plus_separator(self) -> None:
        text = (
            "city | count\n"
            "-----+------\n"
            "BJ   | 10\n"
            "SH   | 20\n"
        )
        result = _parse_text_table_for_event(text)
        self.assertIsNotNone(result)
        self.assertEqual(len(result["rows"]), 2)

    def test_negative_numbers(self) -> None:
        text = (
            "metric | value\n"
            "-------|-------\n"
            "A      | -5\n"
            "B      | 3.14\n"
        )
        result = _parse_text_table_for_event(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["rows"][0]["value"], -5)
        self.assertAlmostEqual(result["rows"][1]["value"], 3.14)


class TestGenerateSuggestQuestions(unittest.TestCase):
    """Tests for the suggest question generator in nodes_child.py."""

    def test_with_string_cols(self) -> None:
        questions = _generate_suggest_questions(
            {}, string_cols=["region"], numeric_cols=["sales"], datetime_col=None,
        )
        self.assertTrue(any("region" in q for q in questions))

    def test_with_datetime(self) -> None:
        questions = _generate_suggest_questions(
            {}, string_cols=[], numeric_cols=["sales"], datetime_col="date",
        )
        self.assertTrue(any("同比" in q for q in questions))

    def test_max_three(self) -> None:
        questions = _generate_suggest_questions(
            {}, string_cols=["a", "b"], numeric_cols=["x", "y"], datetime_col="d",
        )
        self.assertLessEqual(len(questions), 3)

    def test_empty_cols(self) -> None:
        questions = _generate_suggest_questions(
            {}, string_cols=[], numeric_cols=[], datetime_col=None,
        )
        # Should still return some default questions
        self.assertGreater(len(questions), 0)


if __name__ == "__main__":
    unittest.main()
