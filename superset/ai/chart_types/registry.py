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
"""Chart type registry — loads catalog and serves chart type info."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from superset.ai.chart_types.catalog import CATALOG
from superset.ai.chart_types.schema import ChartTypeDescriptor


class ChartTypeRegistry:
    """Loads and serves chart type descriptors for AI agent consumption."""

    def __init__(self) -> None:
        self._types: dict[str, ChartTypeDescriptor] = dict(CATALOG)

    def get(self, viz_type: str) -> ChartTypeDescriptor | None:
        return self._types.get(viz_type)

    def get_supported_types(self) -> set[str]:
        return set(self._types.keys())

    def format_for_prompt(self) -> str:
        """Format all chart types as a compact markdown reference table."""
        lines = [
            "| viz_type | 名称 | 分类 | 适用场景 | 核心参数 |",
            "|---|---|---|---|---|",
        ]
        for desc in self._types.values():
            best = "、".join(desc.best_for[:3])
            key_params = "、".join(
                p.name for p in desc.params if p.required
            )
            lines.append(
                f"| `{desc.viz_type}` | {desc.display_name} | {desc.category} "
                f"| {best} | {key_params} |"
            )
        return "\n".join(lines)

    def format_type_detail(self, viz_type: str) -> str:
        """Return detailed parameter schema for a single chart type."""
        desc = self._types.get(viz_type)
        if not desc:
            return f"Unknown viz_type: {viz_type}"

        lines = [
            f"### {desc.display_name} (`{desc.viz_type}`)",
            f"**{desc.description}**",
            f"- 分类: {desc.category}",
            f"- 适用: {'、'.join(desc.best_for)}",
            f"- 不适用: {'、'.join(desc.not_for)}",
            f"- metric 单数: {'是' if desc.uses_metric_singular else '否（用 metrics 数组）'}",
            f"- 需要时间列: {'是' if desc.requires_time_column else '否'}",
            "",
            "**参数:**",
        ]
        for p in desc.params:
            req = "必填" if p.required else "可选"
            conflict = f"（与 {','.join(p.conflicts_with)} 冲突）" if p.conflicts_with else ""
            default = f"，默认: {p.default}" if p.default is not None else ""
            lines.append(f"- `{p.name}` ({p.type}, {req}{conflict}{default}): {p.description}")

        lines.append("")
        lines.append("**示例 form_data:**")
        lines.append("```json")
        import json
        lines.append(json.dumps(desc.example_form_data, ensure_ascii=False, indent=2))
        lines.append("```")
        return "\n".join(lines)

    def format_all_details(self) -> str:
        """Return detailed schemas for all chart types (for prompt appendix)."""
        return "\n\n".join(
            self.format_type_detail(vt) for vt in self._types
        )

    def validate_form_data(
        self, viz_type: str, form_data: dict[str, Any]
    ) -> list[str]:
        """Validate form_data against the descriptor. Returns list of issues."""
        desc = self._types.get(viz_type)
        if desc is None:
            return [f"Unknown viz_type: {viz_type}"]

        issues: list[str] = []
        for p in desc.params:
            if p.required and p.name not in form_data:
                issues.append(
                    f"Missing required param '{p.name}' for {viz_type}"
                )

        # metric vs metrics check
        if desc.uses_metric_singular:
            if "metrics" in form_data and "metric" not in form_data:
                issues.append(
                    f"'{viz_type}' uses 'metric' (singular), not 'metrics'"
                )

        # conflict checks
        for p in desc.params:
            if p.name in form_data:
                for conflict in p.conflicts_with:
                    if conflict in form_data:
                        issues.append(
                            f"'{p.name}' conflicts with '{conflict}' in {viz_type}"
                        )
        return issues


@lru_cache(maxsize=1)
def get_chart_registry() -> ChartTypeRegistry:
    """Return the singleton chart type registry."""
    return ChartTypeRegistry()
