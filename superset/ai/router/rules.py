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
"""Keyword-based routing rules for fast-path intent classification."""

from __future__ import annotations

# Each agent's keywords split into high-certainty and low-certainty tiers.
_RULES: dict[str, dict[str, list[str]]] = {
    "chart": {
        "high": [
            "画一个图", "做一个图", "生成图表", "create chart",
            "折线图", "柱状图", "饼图", "散点图", "漏斗图",
            "echarts", "visualize", "可视化图表",
            # Cross-mode: "基于已有数据画图" intent
            "帮我画", "给我做图", "画图", "做成图表", "可视化一下",
            "图形化", "展示成图", "画出来",
        ],
        "low": [
            "趋势", "分布", "对比图", "图表", "chart",
            "看一下趋势", "展示",
        ],
    },
    "dashboard": {
        "high": [
            "仪表板", "dashboard", "看板", "创建仪表盘",
            "多个图表", "几张图", "综合分析页",
        ],
        "low": [
            "overview", "全景", "汇总页", "总览",
        ],
    },
    # data_assistant is the default — copilot/nl2sql keywords merged here
    # but since data_assistant is the fallback, no explicit keywords needed.
}

# Continuation keywords: when present, reuse the previous agent.
_CONTINUATION_KEYWORDS = [
    "这个", "那个", "它", "再", "继续", "也", "还有", "另外",
    "修改", "改成", "换成", "加上", "去掉",
    "this", "that", "it", "also", "and", "modify",
]


def keyword_route(message: str) -> tuple[str, float]:
    """Score the message against keyword rules.

    Returns ``(agent_type, confidence)``.
    * ``1.0`` — high-certainty match
    * ``0.65`` — low-certainty match
    * ``0.0`` — no match (caller should try LLM)
    """
    msg_lower = message.lower()
    scores: dict[str, float] = {}

    for agent, rule in _RULES.items():
        high_hits = sum(1 for kw in rule["high"] if kw in msg_lower)
        low_hits = sum(1 for kw in rule["low"] if kw in msg_lower)
        if high_hits >= 1:
            scores[agent] = 0.90 + min(high_hits - 1, 2) * 0.02
        elif low_hits >= 2:
            scores[agent] = 0.72
        elif low_hits == 1:
            scores[agent] = 0.60

    if not scores:
        return "data_assistant", 0.0

    best_agent = max(scores, key=lambda a: scores[a])
    return best_agent, scores[best_agent]


def is_continuation(message: str) -> bool:
    """Return True if the message looks like a continuation of a prior turn."""
    msg_lower = message.lower()
    return any(kw in msg_lower for kw in _CONTINUATION_KEYWORDS)
