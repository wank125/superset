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
"""Data classes for chart type descriptors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParamDescriptor:
    """A single form_data parameter for a visualization type."""

    name: str  # e.g. "x_axis", "metrics", "groupby"
    type: str  # "string" | "string_array" | "metric" | "metric_array" | "integer" | "boolean"
    required: bool
    description: str
    default: Any = None
    conflicts_with: list[str] = field(default_factory=list)


@dataclass
class ChartTypeDescriptor:
    """Complete description of one visualization type."""

    viz_type: str  # e.g. "pie"
    display_name: str  # e.g. "饼图/环形图"
    category: str  # timeseries | categorical | kpi | distribution | relationship
    description: str
    best_for: list[str]  # 适用场景
    not_for: list[str]  # 不适用场景
    params: list[ParamDescriptor]
    example_form_data: dict[str, Any]
    uses_metric_singular: bool  # True for pie, big_number_total, gauge
    requires_time_column: bool  # True for timeseries types
    max_groupby_dimensions: int  # suggested max, 0 = unlimited
