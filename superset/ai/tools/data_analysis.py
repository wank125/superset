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
"""Tool to execute SQL and produce structured data analysis with insights."""

from __future__ import annotations

import logging
import re
from typing import Any

from superset.ai.tools.analyze_data import AnalyzeDataTool
from superset.ai.tools.base import BaseTool
from superset.ai.tools.execute_sql import ExecuteSqlTool
from superset.utils import json

logger = logging.getLogger(__name__)


class DataAnalysisTool(BaseTool):
    """Execute SQL and return structured analysis with insights and statistics.

    Unlike ``execute_sql`` which returns raw results, this tool analyses the
    data shape (column types, cardinality, suitability flags), generates an
    LLM-powered insight sentence and basic comparison statistics, and suggests
    follow-up questions.  Designed for the *data_assistant* agent.
    """

    name = "analyze_data"
    description = (
        "Execute a SQL query and perform structured data analysis. "
        "Returns column types, row count, suitability flags (trend / KPI / "
        "composition), a one-line insight, comparison statistics (环比/同比), "
        "and suggested follow-up questions. "
        "Use this when the user asks for analysis, trends, comparisons, or insights."
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

        # 1. Execute SQL
        exec_tool = ExecuteSqlTool(database_id=db_id)
        raw_result = exec_tool.run({"sql": sql})

        if raw_result.startswith("Error"):
            return raw_result

        # 2. Parse result
        columns, rows = AnalyzeDataTool._parse_text_table(raw_result)
        if not columns:
            return json.dumps(
                {"error": "No data returned", "columns": [], "row_count": 0},
            )

        # 3. Analyze columns
        col_analysis = AnalyzeDataTool._analyze_columns(columns, rows)
        row_count = len(rows)

        # 4. Compute statistics (Phase 1)
        col_stats = AnalyzeDataTool._compute_statistics(columns, rows, col_analysis)

        # 5. Classify columns
        datetime_col = self._detect_datetime_col(col_analysis)
        numeric_cols = [c["name"] for c in col_analysis if c["type"] == "numeric"]
        string_cols = [c["name"] for c in col_analysis if c["type"] == "string"]
        low_card_cols = [
            c["name"]
            for c in col_analysis
            if c["type"] == "string" and c.get("distinct_count", 999) < 20
        ]
        dt_cardinality = 0
        if datetime_col:
            dt_info = next(
                (c for c in col_analysis if c["name"] == datetime_col), {}
            )
            dt_cardinality = dt_info.get("distinct_count", 0)

        # 6. Suitability flags
        suitability = {
            "good_for_trend": bool(
                datetime_col and numeric_cols and dt_cardinality > 3
            ),
            "good_for_composition": bool(low_card_cols and numeric_cols),
            "good_for_kpi": row_count == 1 and len(numeric_cols) == 1,
            "good_for_distribution": (
                row_count > 10 and len(numeric_cols) == 1 and not string_cols
            ),
            "good_for_comparison": bool(low_card_cols and numeric_cols),
            "good_for_table": True,
        }

        # 7. Trend detection (Phase 3)
        trend: dict[str, Any] | None = None
        if suitability["good_for_trend"] and datetime_col:
            dt_idx = columns.index(datetime_col) if datetime_col in columns else -1
            if dt_idx >= 0:
                # Use first numeric column for trend
                num_idx = columns.index(numeric_cols[0])
                num_vals: list[float] = []
                for row in rows:
                    try:
                        num_vals.append(
                            float(row[num_idx].replace(",", ""))
                            if row[num_idx] and row[num_idx] != "NULL"
                            else 0.0
                        )
                    except (ValueError, IndexError):
                        num_vals.append(0.0)
                trend = AnalyzeDataTool._detect_trend(num_vals)

        # 8. Real period comparison (Phase 2, KPI only)
        period_comparison: dict[str, str] = {}
        is_kpi = suitability["good_for_kpi"]
        if is_kpi and datetime_col:
            period_comparison = self._compute_period_comparison(
                sql, columns, rows, col_analysis, datetime_col,
                numeric_cols, db_id,
            )

        # 9. LLM insight with statistical context (Phase 4)
        insight: str | None = None
        statistics: dict[str, str] = {}
        if row_count > 0 and numeric_cols:
            insight, statistics = self._generate_insight(
                rows, numeric_cols, string_cols, datetime_col,
                col_stats=col_stats,
                period_comparison=period_comparison or None,
                trend=trend,
            )

        # Merge real period comparison into statistics if available
        if period_comparison and not statistics:
            statistics = period_comparison
        elif period_comparison and statistics:
            # Prefer real computed values over LLM guesses
            for key in ("环比", "同比"):
                if key in period_comparison:
                    statistics[key] = period_comparison[key]

        # 10. Follow-up questions (Phase 5)
        suggest_questions = self._generate_questions(
            string_cols, numeric_cols, datetime_col,
            col_stats=col_stats, trend=trend, insight=insight,
        )

        # 11. Build result payload
        result = {
            "row_count": row_count,
            "columns": _columns_for_event(col_analysis, datetime_col),
            "rows": _rows_to_dicts(columns, rows, col_analysis),
            "suitability": suitability,
            "col_stats": col_stats,
            "trend": trend,
            "insight": insight,
            "statistics": statistics,
            "suggest_questions": suggest_questions,
        }
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Period comparison executor
    # ------------------------------------------------------------------

    def _compute_period_comparison(
        self,
        original_sql: str,
        columns: list[str],
        rows: list[list[str]],
        col_analysis: list[dict[str, Any]],
        datetime_col: str,
        numeric_cols: list[str],
        db_id: int,
    ) -> dict[str, str]:
        """Execute comparison SQL to compute real 环比/同比 for KPI results."""
        result: dict[str, str] = {}
        try:
            dt_idx = columns.index(datetime_col)
            dt_values = list({
                row[dt_idx]
                for row in rows
                if dt_idx < len(row) and row[dt_idx] and row[dt_idx] != "NULL"
            })
            grain = self._detect_time_grain(dt_values)
            if not grain:
                return result

            # Current numeric values
            num_idx = columns.index(numeric_cols[0])
            current_val = float(rows[0][num_idx].replace(",", ""))

            exec_tool = ExecuteSqlTool(database_id=db_id)

            # 环比: shift 1 period
            prev_sql = self._shift_date_range(original_sql, grain, periods=1)
            if prev_sql:
                prev_raw = exec_tool.run({"sql": prev_sql})
                if not prev_raw.startswith("Error"):
                    prev_cols, prev_rows = AnalyzeDataTool._parse_text_table(
                        prev_raw
                    )
                    if prev_rows:
                        prev_num_idx = prev_cols.index(numeric_cols[0])
                        prev_val = float(
                            prev_rows[0][prev_num_idx].replace(",", "")
                        )
                        result["环比"] = self._compute_pct_change(
                            current_val, prev_val
                        )

            # 同比: shift 1 year (12 months / 4 quarters / 365 days)
            year_shifts = {
                "day": 365, "week": 52, "month": 12,
                "quarter": 4, "year": 1,
            }
            yoy_periods = year_shifts.get(grain, 12)
            yoy_sql = self._shift_date_range(
                original_sql, grain, periods=yoy_periods
            )
            if yoy_sql:
                yoy_raw = exec_tool.run({"sql": yoy_sql})
                if not yoy_raw.startswith("Error"):
                    yoy_cols, yoy_rows = AnalyzeDataTool._parse_text_table(
                        yoy_raw
                    )
                    if yoy_rows:
                        yoy_num_idx = yoy_cols.index(numeric_cols[0])
                        yoy_val = float(
                            yoy_rows[0][yoy_num_idx].replace(",", "")
                        )
                        result["同比"] = self._compute_pct_change(
                            current_val, yoy_val
                        )

        except Exception as exc:
            logger.debug("Period comparison failed: %s", exc)

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_datetime_col(col_analysis: list[dict[str, Any]]) -> str | None:
        return next(
            (
                c["name"]
                for c in col_analysis
                if any(
                    kw in c["name"].lower()
                    for kw in ("date", "ds", "time", "year", "month", "day", "dttm")
                )
            ),
            None,
        )

    # ------------------------------------------------------------------
    # Period comparison helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_time_grain(dt_values: list[str]) -> str | None:
        """Infer time grain from distinct datetime string values."""
        if not dt_values or len(dt_values) < 2:
            return None

        # Try to detect YYYY-MM-DD vs YYYY-MM vs YYYY patterns
        sample = dt_values[0].strip()
        if re.match(r"^\d{4}$", sample):
            return "year"
        if re.match(r"^\d{4}-Q\d$", sample):
            return "quarter"
        if re.match(r"^\d{4}-\d{2}$", sample):
            return "month"
        if re.match(r"^\d{4}-\d{2}-\d{2}$", sample):
            return "day"
        return None

    @staticmethod
    def _shift_date_range(sql: str, grain: str, periods: int = 1) -> str | None:
        """Shift date literals in WHERE clause by N periods.

        Supports: YYYY-MM-DD, YYYY-MM, YYYY patterns.
        Returns modified SQL or None if no date pattern found.
        """
        try:
            import datetime

            def _shift_months(d: datetime.datetime, delta: int) -> datetime.datetime:
                """Shift a datetime by *delta* months (negative = earlier)."""
                import calendar

                total = d.year * 12 + (d.month - 1) + delta
                year, rem = divmod(total, 12)
                month = rem + 1
                max_day = calendar.monthrange(year, month)[1]
                return d.replace(year=year, month=month, day=min(d.day, max_day))

            def _shift_yyyy_mm_dd(m: re.Match) -> str:
                d = datetime.datetime.strptime(m.group(0), "%Y-%m-%d")
                if grain == "day":
                    d -= datetime.timedelta(days=periods)
                elif grain == "week":
                    d -= datetime.timedelta(weeks=periods)
                elif grain == "month":
                    d = _shift_months(d, -periods)
                elif grain == "quarter":
                    d = _shift_months(d, -3 * periods)
                elif grain == "year":
                    d = d.replace(year=d.year - periods)
                return d.strftime("'%Y-%m-%d'")

            def _shift_yyyy_mm(m: re.Match) -> str:
                d = datetime.datetime.strptime(m.group(0), "%Y-%m")
                if grain == "month":
                    d = _shift_months(d, -periods)
                elif grain == "quarter":
                    d = _shift_months(d, -3 * periods)
                elif grain == "year":
                    d = d.replace(year=d.year - periods)
                return d.strftime("'%Y-%m'")

            def _shift_yyyy(m: re.Match) -> str:
                year = int(m.group(0)) - periods
                return f"'{year}'"

            result = sql
            # Order matters: longest patterns first
            result = re.sub(
                r"'(\d{4}-\d{2}-\d{2})'", _shift_yyyy_mm_dd, result
            )
            result = re.sub(r"'(\d{4}-\d{2})'", _shift_yyyy_mm, result)
            result = re.sub(r"'(\d{4})'", _shift_yyyy, result)
            return result
        except Exception:
            return None

    @staticmethod
    def _compute_pct_change(current: float, previous: float) -> str:
        """Compute percentage change. Returns '+5.2%' / '-3.1%' / 'N/A'."""
        if previous == 0:
            return "N/A"
        pct = (current - previous) / abs(previous) * 100
        sign = "+" if pct >= 0 else ""
        return f"{sign}{pct:.1f}%"

    @staticmethod
    def _generate_insight(
        rows: list[list[str]],
        numeric_cols: list[str],
        string_cols: list[str],
        datetime_col: str | None,
        col_stats: dict[str, dict[str, Any]] | None = None,
        period_comparison: dict[str, str] | None = None,
        trend: dict[str, Any] | None = None,
    ) -> tuple[str | None, dict[str, str]]:
        from superset.ai.insight import generate_llm_insight

        return generate_llm_insight(
            row_count=len(rows),
            sample_rows=rows[:3],
            numeric_cols=numeric_cols,
            string_cols=string_cols,
            datetime_col=datetime_col,
            col_stats=col_stats,
            trend=trend,
            period_comparison=period_comparison,
        )

    @staticmethod
    def _generate_questions(
        string_cols: list[str],
        numeric_cols: list[str],
        datetime_col: str | None,
        col_stats: dict[str, dict[str, Any]] | None = None,
        trend: dict[str, Any] | None = None,
        insight: str | None = None,
    ) -> list[str]:
        """Generate follow-up questions via LLM (with template fallback)."""
        # Fallback templates
        fallback: list[str] = []
        if string_cols:
            fallback.append(f"按 {string_cols[0]} 拆分分析")
        if datetime_col:
            fallback.append("同比上周如何")
        if numeric_cols:
            fallback.append("哪个维度贡献最大")
        if not fallback:
            fallback = ["查看趋势变化", "导出详细数据"]

        try:
            from superset.ai.graph.llm_helpers import _get_llm_response
            from superset.utils import json as superset_json

            stats_summary = ""
            if col_stats:
                parts = []
                for col_name, stats in col_stats.items():
                    if "mean" in stats:
                        parts.append(
                            f"  {col_name}: mean={stats['mean']}, "
                            f"std={stats['std']}"
                        )
                if parts:
                    stats_summary = "\n".join(parts)

            trend_str = (
                f"{trend['direction']}" if trend else "无"
            )
            prompt = (
                f"基于以下数据分析结果，推荐3个用户最可能想追问的问题:\n"
                f"  指标列: {numeric_cols[:3]}\n"
                f"  维度列: {string_cols[:3]}\n"
                f"  时间列: {datetime_col}\n"
                f"  统计摘要:\n{stats_summary}\n"
                f"  趋势: {trend_str}\n"
                f"  洞察: {insight}\n\n"
                f"输出合法JSON: {{\"questions\": [\"追问1\", \"追问2\", \"追问3\"]}}\n"
                f"规则: 每个问题不超过20字，基于数据特征提出具体下钻方向。\n"
                f"输出 ONLY JSON，无其他内容。"
            )

            raw = _get_llm_response(prompt).strip()
            if raw:
                parsed = superset_json.loads(raw)
                if isinstance(parsed, dict) and "questions" in parsed:
                    qs = parsed["questions"]
                    if isinstance(qs, list) and len(qs) >= 2:
                        return [str(q) for q in qs[:3]]
        except Exception as exc:
            logger.debug("LLM questions fallback: %s", exc)

        return fallback[:3]


# ------------------------------------------------------------------
# Module-level helpers (shared with runner event emission)
# ------------------------------------------------------------------


def _rows_to_dicts(
    columns: list[str],
    rows: list[list[str]],
    col_analysis: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert text-table rows to list-of-dicts with type coercion."""
    numeric_idx = {
        i for i, c in enumerate(col_analysis) if c["type"] == "numeric"
    }
    result: list[dict[str, Any]] = []
    for row in rows[:100]:
        d: dict[str, Any] = {}
        for i, col in enumerate(columns):
            val = row[i] if i < len(row) else None
            if val is None or val == "NULL":
                d[col] = None
            elif i in numeric_idx:
                try:
                    d[col] = int(val.replace(",", ""))
                except ValueError:
                    try:
                        d[col] = float(val.replace(",", ""))
                    except ValueError:
                        d[col] = val
            else:
                d[col] = val
        result.append(d)
    return result


def _columns_for_event(
    col_analysis: list[dict[str, Any]],
    datetime_col: str | None,
) -> list[dict[str, Any]]:
    """Format column metadata for the frontend ``data_analyzed`` event."""
    result: list[dict[str, Any]] = []
    for c in col_analysis:
        result.append({
            "name": c["name"],
            "type": "FLOAT" if c["type"] == "numeric" else "STRING",
            "is_dttm": c["name"] == datetime_col,
        })
    return result
