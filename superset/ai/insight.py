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
"""Shared LLM insight generation for data analysis results."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_stats_summary(
    col_stats: dict[str, dict[str, Any]] | None,
) -> str:
    """Format per-column statistics into a prompt-friendly string."""
    if not col_stats:
        return ""
    parts: list[str] = []
    for col_name, stats in col_stats.items():
        if isinstance(stats, dict) and "mean" in stats:
            parts.append(
                f"  {col_name}: mean={stats['mean']}, "
                f"median={stats['median']}, std={stats['std']}, "
                f"min={stats['min']}, max={stats['max']}"
            )
    return "\n".join(parts)


def build_trend_str(trend: dict[str, Any] | None) -> str:
    """Format trend dict into a human-readable string."""
    if trend:
        return f"{trend['direction']} (强度={trend['strength']})"
    return "无趋势数据"


def generate_llm_insight(
    *,
    row_count: int,
    sample_rows: list[list[str]],
    numeric_cols: list[str],
    string_cols: list[str],
    datetime_col: str | None,
    col_stats: dict[str, dict[str, Any]] | None = None,
    trend: dict[str, Any] | None = None,
    period_comparison: dict[str, str] | None = None,
) -> tuple[str | None, dict[str, str]]:
    """Generate insight + statistics via LLM.

    Returns (insight_text, statistics_dict).  Both are best-effort;
    callers receive (None, {}) on any failure.
    """
    try:
        from superset.ai.graph.llm_helpers import _get_llm_response
        from superset.utils import json as superset_json

        stats_summary = build_stats_summary(col_stats)
        is_kpi = row_count == 1 and len(numeric_cols) == 1

        if is_kpi:
            period_str = (
                ", ".join(f"{k}: {v}" for k, v in period_comparison.items())
                if period_comparison
                else "无"
            )
            prompt = (
                f"这是查询结果（单行KPI数据）:\n"
                f"  数据行: {sample_rows}\n"
                f"  指标列: {numeric_cols}\n"
                f"  维度列: {string_cols}\n"
                f"  时间列: {datetime_col}\n"
                f"  统计摘要:\n{stats_summary}\n"
                f"  环比/同比: {period_str}\n\n"
                f"请输出合法JSON，格式如下：\n"
                f'{{"insight": "一句话洞察(30字内)", '
                f'"statistics": {{"环比": "+X.X%", "同比": "+X.X%"}}}}\n\n'
                f"注意：\n"
                f"- insight 是一句话关键发现，必须基于统计摘要和环比/同比数据\n"
                f"- statistics 中的环比/同比请直接使用上面提供的计算结果\n"
                f"- 如果没有环比/同比数据，statistics 置为空对象 {{}}\n"
                f"输出 ONLY JSON，无其他内容。"
            )
        else:
            trend_str = build_trend_str(trend)
            prompt = (
                f"数据查询结果分析:\n"
                f"  行数: {row_count}\n"
                f"  指标列: {numeric_cols[:3]}\n"
                f"  时间列: {datetime_col}\n"
                f"  维度列: {string_cols[:3]}\n"
                f"  统计摘要:\n{stats_summary}\n"
                f"  趋势方向: {trend_str}\n"
                f"  样本(前3行): {sample_rows}\n\n"
                f"请用中文写一句话（不超过30字）描述关键发现，"
                f"必须基于统计摘要和趋势方向。\n"
                f"输出 ONLY 洞察句子，无其他内容。"
            )

        raw_response = _get_llm_response(prompt).strip()
        if not raw_response:
            return None, {}

        if is_kpi:
            try:
                parsed = superset_json.loads(raw_response)
                if isinstance(parsed, dict):
                    ins = parsed.get("insight")
                    stats = parsed.get("statistics", {})
                    return (
                        str(ins)[:200] if ins else None,
                        stats if isinstance(stats, dict) else {},
                    )
            except (ValueError, KeyError):
                return raw_response[:200], {}

        return raw_response[:200], {}

    except Exception as exc:
        logger.warning("LLM insight generation failed: %s", exc)
        return None, {}
