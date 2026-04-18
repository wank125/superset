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
"""Tool to execute SQL and analyze result data shape for chart type selection."""

from __future__ import annotations

from typing import Any

import numpy as np

from superset.ai.chart_types.registry import get_chart_registry
from superset.ai.tools.base import BaseTool
from superset.ai.tools.execute_sql import ExecuteSqlTool
from superset.utils import json


class AnalyzeDataTool(BaseTool):
    """Execute SQL and return structured data shape analysis.

    Analyzes the query result to provide column metadata, distinct value
    counts, and chart type recommendations. Use this BEFORE create_chart
    to ensure parameters match actual data.
    """

    name = "analyze_data"
    description = (
        "Execute a SQL query and analyze the result data shape. "
        "Returns column metadata, row count, distinct value counts, "
        "and chart type recommendations. "
        "Use this BEFORE create_chart to ensure params match actual data."
    )

    parameters_schema: dict[str, Any] = {
        "type": "object",
        "required": ["sql"],
        "properties": {
            "sql": {
                "type": "string",
                "description": "SQL query to execute and analyze",
            },
            "database_id": {
                "type": "integer",
                "description": "Optional database ID override",
            },
        },
    }

    def __init__(self, database_id: int, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._database_id = database_id

    def run(self, arguments: dict[str, Any]) -> str:
        sql = arguments.get("sql", "").strip()
        db_id = arguments.get("database_id") or self._database_id

        if not sql:
            return "Error: sql is required"

        # Execute the SQL via existing tool
        exec_tool = ExecuteSqlTool(database_id=db_id)
        raw_result = exec_tool.run({"sql": sql})

        if raw_result.startswith("Error"):
            return raw_result

        # Parse the text table result
        columns, rows = self._parse_text_table(raw_result)
        if not columns:
            return json.dumps(
                {"error": "No data returned", "columns": [], "row_count": 0},
            )

        # Analyze column shapes
        col_analysis = self._analyze_columns(columns, rows)

        # Generate chart recommendations
        recommendations = self._recommend_charts(col_analysis, len(rows))

        # Enrich recommendations with parameter schema from registry
        registry = get_chart_registry()
        for rec in recommendations:
            viz_type = rec.get("viz_type", "")
            desc = registry.get(viz_type)
            if desc:
                rec["params_schema"] = [
                    {
                        "name": p.name,
                        "type": p.type,
                        "required": p.required,
                        "description": p.description,
                    }
                    for p in desc.params
                ]
                rec["example_form_data"] = desc.example_form_data

        result = {
            "columns": col_analysis,
            "row_count": len(rows),
            "chart_recommendations": recommendations,
        }
        return json.dumps(result, indent=2)

    @staticmethod
    def _parse_text_table(
        raw: str,
    ) -> tuple[list[str], list[list[str]]]:
        """Parse text table output from ExecuteSqlTool into columns and rows."""
        lines = raw.strip().split("\n")
        if len(lines) < 2:
            return [], []

        # First line: column headers separated by |
        headers = [h.strip() for h in lines[0].split("|") if h.strip()]
        if not headers:
            return [], []

        rows: list[list[str]] = []
        for line in lines[2:]:  # skip header + separator
            cells = [c.strip() for c in line.split("|")]
            if len(cells) >= len(headers):
                rows.append(cells[: len(headers)])

        return headers, rows

    @staticmethod
    def _analyze_columns(
        columns: list[str], rows: list[list[str]]
    ) -> list[dict[str, Any]]:
        """Analyze each column: type, distinct count, sample values."""
        analysis: list[dict[str, Any]] = []
        for idx, col_name in enumerate(columns):
            values = [row[idx] for row in rows if idx < len(row)]
            non_empty = [v for v in values if v and v != "NULL"]

            # Detect type
            numeric_count = 0
            for v in non_empty[:50]:
                try:
                    float(v.replace(",", ""))
                    numeric_count += 1
                except (ValueError, AttributeError):
                    pass
            is_numeric = numeric_count > len(non_empty) * 0.7 if non_empty else False

            distinct_values = list(dict.fromkeys(non_empty))  # preserve order
            distinct_count = len(distinct_values)

            col_info: dict[str, Any] = {
                "name": col_name,
                "type": "numeric" if is_numeric else "string",
                "distinct_count": distinct_count,
            }

            if is_numeric and non_empty:
                nums = []
                for v in non_empty:
                    try:
                        nums.append(float(v.replace(",", "")))
                    except (ValueError, AttributeError):
                        pass
                if nums:
                    col_info["min"] = min(nums)
                    col_info["max"] = max(nums)
            elif distinct_values:
                col_info["sample_values"] = distinct_values[:10]

            analysis.append(col_info)
        return analysis

    @staticmethod
    def _compute_statistics(
        columns: list[str],
        rows: list[list[str]],
        col_analysis: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Per-column descriptive statistics.

        numeric → mean, median, std, p25, p75, min, max, sum, null_count, null_pct
        string  → null_count, null_pct, distinct_count, top_value
        """
        total_rows = len(rows)
        if total_rows == 0:
            return {}

        result: dict[str, dict[str, Any]] = {}
        for idx, col in enumerate(col_analysis):
            col_name = col["name"]
            values = [row[idx] for row in rows if idx < len(row)]
            non_empty = [v for v in values if v and v != "NULL"]
            null_count = total_rows - len(non_empty)

            if col["type"] == "numeric":
                nums: list[float] = []
                for v in non_empty:
                    try:
                        nums.append(float(v.replace(",", "")))
                    except (ValueError, AttributeError):
                        pass

                if nums:
                    arr = np.array(nums, dtype=float)
                    result[col_name] = {
                        "mean": round(float(np.mean(arr)), 4),
                        "median": round(float(np.median(arr)), 4),
                        "std": round(float(np.std(arr)), 4),
                        "p25": round(float(np.percentile(arr, 25)), 4),
                        "p75": round(float(np.percentile(arr, 75)), 4),
                        "min": round(float(np.min(arr)), 4),
                        "max": round(float(np.max(arr)), 4),
                        "sum": round(float(np.sum(arr)), 4),
                        "null_count": null_count,
                        "null_pct": round(null_count / total_rows, 4),
                    }
                else:
                    result[col_name] = {
                        "null_count": null_count,
                        "null_pct": round(null_count / total_rows, 4),
                    }
            else:
                distinct = list(dict.fromkeys(non_empty))
                # top_value = most frequent
                freq: dict[str, int] = {}
                for v in non_empty:
                    freq[v] = freq.get(v, 0) + 1
                top_val = max(freq, key=freq.get) if freq else None
                result[col_name] = {
                    "null_count": null_count,
                    "null_pct": round(null_count / total_rows, 4),
                    "distinct_count": len(distinct),
                    "top_value": top_val,
                }

        return result

    @staticmethod
    def _detect_trend(
        numeric_values: list[float],
    ) -> dict[str, Any] | None:
        """Detect trend direction via linear regression.

        Returns {"direction": "上升"/"下降"/"平稳", "slope": float,
                 "strength": float} or None if insufficient data.
        """
        n = len(numeric_values)
        if n < 3:
            return None

        arr = np.array(numeric_values, dtype=float)
        mean_val = float(np.mean(arr))
        if mean_val == 0:
            return None

        slope, intercept = np.polyfit(np.arange(n), arr, 1)
        predicted = slope * np.arange(n) + intercept
        ss_res = float(np.sum((arr - predicted) ** 2))
        ss_tot = float(np.sum((arr - mean_val) ** 2))
        r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0

        # Normalized slope: relative change per step
        rel_slope = float(slope) / abs(mean_val)

        if abs(rel_slope) < 0.02 or r_squared < 0.2:
            direction = "平稳"
        elif rel_slope > 0:
            direction = "上升"
        else:
            direction = "下降"

        return {
            "direction": direction,
            "slope": round(float(slope), 4),
            "strength": round(r_squared, 4),
        }

    @staticmethod
    def _recommend_charts(
        columns: list[dict[str, Any]], row_count: int
    ) -> list[dict[str, str]]:
        """Generate chart type recommendations based on data shape."""
        recs: list[dict[str, str]] = []

        string_cols = [c for c in columns if c["type"] == "string"]
        numeric_cols = [c for c in columns if c["type"] == "numeric"]
        has_date_like = any(
            c["type"] == "string"
            and any(
                kw in c["name"].lower()
                for kw in ("date", "time", "year", "month", "day", "dttm")
            )
            for c in columns
        )

        # Rule 1: few categories + 1 metric → pie, bar
        if len(string_cols) == 1 and len(numeric_cols) >= 1:
            distinct = string_cols[0].get("distinct_count", 0)
            if distinct <= 10:
                recs.append({
                    "viz_type": "pie",
                    "confidence": "high",
                    "reason": f"{distinct} 个分类 + 数值指标 → 适合饼图展示占比",
                })
                recs.append({
                    "viz_type": "echarts_timeseries_bar",
                    "confidence": "high",
                    "reason": "分类维度 + 数值指标 → 适合柱状图对比",
                })
            if distinct <= 5:
                recs.append({
                    "viz_type": "funnel",
                    "confidence": "medium",
                    "reason": "少量分类 + 数值 → 可用漏斗图展示转化",
                })

        # Rule 2: date-like column + numeric → timeseries
        if has_date_like and numeric_cols:
            recs.append({
                "viz_type": "echarts_timeseries_line",
                "confidence": "high",
                "reason": "时间维度 + 数值指标 → 适合折线图展示趋势",
            })
            recs.append({
                "viz_type": "echarts_area",
                "confidence": "medium",
                "reason": "时间维度 + 数值指标 → 可用面积图展示趋势",
            })
            recs.append({
                "viz_type": "echarts_timeseries_bar",
                "confidence": "medium",
                "reason": "时间维度 + 数值指标 → 可用柱状图",
            })

        # Rule 3: single numeric, no groupby → big_number_total
        if not string_cols and len(numeric_cols) == 1 and row_count == 1:
            recs.append({
                "viz_type": "big_number_total",
                "confidence": "high",
                "reason": "单个数值 → 适合大数字展示",
            })

        # Rule 4: 2+ numeric cols + 1 string → radar, scatter
        if len(numeric_cols) >= 2 and string_cols:
            recs.append({
                "viz_type": "radar",
                "confidence": "medium",
                "reason": "多维度指标 + 分类 → 适合雷达图",
            })

        # Rule 5: always offer table as fallback
        recs.append({
            "viz_type": "table",
            "confidence": "low",
            "reason": "通用展示，适合所有数据",
        })

        return recs
