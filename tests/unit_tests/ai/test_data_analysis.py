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
"""Tests for DataAnalysisTool — date shifting, stats, and helpers."""

from superset.ai.tools.data_analysis import DataAnalysisTool


class TestShiftDateRange:
    """Verify _shift_date_range correctly shifts date literals."""

    def test_shift_day(self):
        sql = "SELECT * FROM t WHERE dt >= '2024-03-15' AND dt < '2024-03-16'"
        result = DataAnalysisTool._shift_date_range(sql, "day", periods=1)
        assert result is not None
        assert "'2024-03-14'" in result
        assert "'2024-03-15'" in result

    def test_shift_week(self):
        sql = "SELECT * FROM t WHERE dt >= '2024-03-15'"
        result = DataAnalysisTool._shift_date_range(sql, "week", periods=1)
        assert result is not None
        assert "'2024-03-08'" in result

    def test_shift_month(self):
        sql = "SELECT * FROM t WHERE dt >= '2024-03-15'"
        result = DataAnalysisTool._shift_date_range(sql, "month", periods=1)
        assert result is not None
        assert "'2024-02-15'" in result

    def test_shift_month_year_boundary(self):
        """January shifted back 1 month should become December of prev year."""
        sql = "SELECT * FROM t WHERE dt >= '2024-01-10'"
        result = DataAnalysisTool._shift_date_range(sql, "month", periods=1)
        assert result is not None
        assert "'2023-12-10'" in result

    def test_shift_quarter(self):
        sql = "SELECT * FROM t WHERE dt >= '2024-04-01'"
        result = DataAnalysisTool._shift_date_range(sql, "quarter", periods=1)
        assert result is not None
        assert "'2024-01-01'" in result

    def test_shift_year(self):
        sql = "SELECT * FROM t WHERE dt >= '2024-06-15'"
        result = DataAnalysisTool._shift_date_range(sql, "year", periods=1)
        assert result is not None
        assert "'2023-06-15'" in result

    def test_shift_yyyy_mm_grain_month(self):
        sql = "SELECT * FROM t WHERE month = '2024-03'"
        result = DataAnalysisTool._shift_date_range(sql, "month", periods=2)
        assert result is not None
        assert "'2024-01'" in result

    def test_shift_yyyy_grain_year(self):
        sql = "SELECT * FROM t WHERE year = '2024'"
        result = DataAnalysisTool._shift_date_range(sql, "year", periods=1)
        assert result is not None
        assert "'2023'" in result

    def test_shift_yyyy_mm_year_boundary(self):
        """Shift Jan back by 1 month → Dec of previous year."""
        sql = "SELECT * FROM t WHERE month = '2024-01'"
        result = DataAnalysisTool._shift_date_range(sql, "month", periods=1)
        assert result is not None
        assert "'2023-12'" in result

    def test_no_date_literals_returns_none(self):
        result = DataAnalysisTool._shift_date_range(
            "SELECT 1", "day", periods=1,
        )
        # No dates to shift — returns original SQL (all re.sub are no-ops)
        assert result is not None
        assert result == "SELECT 1"


class TestComputePctChange:
    def test_positive_change(self):
        assert DataAnalysisTool._compute_pct_change(110, 100) == "+10.0%"

    def test_negative_change(self):
        assert DataAnalysisTool._compute_pct_change(90, 100) == "-10.0%"

    def test_zero_previous(self):
        assert DataAnalysisTool._compute_pct_change(5, 0) == "N/A"

    def test_no_change(self):
        assert DataAnalysisTool._compute_pct_change(100, 100) == "+0.0%"


class TestDetectTimeGrain:
    def test_day_grain(self):
        assert DataAnalysisTool._detect_time_grain(["2024-01-01", "2024-01-02"]) == "day"

    def test_month_grain(self):
        assert DataAnalysisTool._detect_time_grain(["2024-01", "2024-02"]) == "month"

    def test_year_grain(self):
        assert DataAnalysisTool._detect_time_grain(["2024", "2025"]) == "year"

    def test_quarter_grain(self):
        assert DataAnalysisTool._detect_time_grain(["2024-Q1", "2024-Q2"]) == "quarter"

    def test_single_value(self):
        assert DataAnalysisTool._detect_time_grain(["2024-01-01"]) is None

    def test_empty(self):
        assert DataAnalysisTool._detect_time_grain([]) is None
