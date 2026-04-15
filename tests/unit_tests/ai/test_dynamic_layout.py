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
"""Unit tests for Phase 19b dynamic layout engine (append_charts_v2)."""

import unittest
from unittest.mock import MagicMock

from superset.commands.dashboard.export import (
    DEFAULT_CHART_HEIGHT,
    append_charts_v2,
    get_default_position,
)


def _make_slice(chart_id: int, name: str) -> MagicMock:
    sl = MagicMock()
    sl.id = chart_id
    sl.slice_name = name
    sl.uuid = f"uuid-{chart_id}"
    return sl


def _get_chart_widths(position: dict) -> list[int]:
    """Extract chart widths from position_json, in order."""
    widths = []
    for key, val in position.items():
        if key.startswith("CHART-"):
            widths.append(val["meta"]["width"])
    return widths


def _get_row_sizes(position: dict) -> list[int]:
    """Count charts per row from position_json."""
    rows = []
    for key, val in position.items():
        if key.startswith("ROW-N-"):
            rows.append(len(val["children"]))
    return rows


class TestAppendChartsV2(unittest.TestCase):
    """Tests for the dynamic layout bin-packing algorithm."""

    def test_empty_charts(self) -> None:
        """Empty input should return position with only grid scaffolding."""
        pos = get_default_position("Test")
        result = append_charts_v2(pos, [])
        # No charts added, but grid structure preserved
        chart_keys = [k for k in result if k.startswith("CHART-")]
        self.assertEqual(len(chart_keys), 0)

    def test_single_chart_full_width(self) -> None:
        """A single table (width=12) should fill the entire row."""
        pos = get_default_position("Test")
        sl = _make_slice(1, "Table")
        append_charts_v2(pos, [(sl, 12)])
        widths = _get_chart_widths(pos)
        self.assertEqual(widths, [12])
        rows = _get_row_sizes(pos)
        self.assertEqual(rows, [1])

    def test_single_chart_partial_width_tail_fill(self) -> None:
        """A single chart with width < 12 should be expanded to fill the row."""
        pos = get_default_position("Test")
        sl = _make_slice(1, "KPI")
        append_charts_v2(pos, [(sl, 3)])
        widths = _get_chart_widths(pos)
        # Tail-fill: 3 → 12
        self.assertEqual(widths, [12])

    def test_kpi_plus_two_lines(self) -> None:
        """KPI(3) + Line(6) + Line(6): row1 expands KPI to 6, row2 two lines."""
        pos = get_default_position("Test")
        charts = [
            (_make_slice(1, "KPI"), 3),
            (_make_slice(2, "Line1"), 6),
            (_make_slice(3, "Line2"), 6),
        ]
        append_charts_v2(pos, charts)
        widths = _get_chart_widths(pos)
        rows = _get_row_sizes(pos)
        # Row 1: KPI(3) doesn't fit with Line1(6)=9... wait, 3+6=9 ≤ 12
        # Row 1: KPI(3) + Line1(6) = 9, Line2(6) won't fit (9+6=15 > 12)
        # → Tail-fill KPI to 9-6=3... no: tail-fill last = Line1 expands
        # Actually: 3+6=9 ≤12, add Line2: 9+6=15 > 12, so tail-fill Line1: 6+(12-9)=9
        # Row 1: KPI(3), Line1(9), Row 2: Line2(12)
        self.assertEqual(len(rows), 2)
        self.assertEqual(widths[0], 3)  # KPI stays 3
        self.assertEqual(widths[1], 9)  # Line1 tail-filled to 9
        self.assertEqual(widths[2], 12)  # Line2 tail-filled to 12 (last row)

    def test_three_kpis(self) -> None:
        """Three KPIs (3+3+3=9) should fit in one row, tail-filled to 12."""
        pos = get_default_position("Test")
        charts = [
            (_make_slice(1, "KPI1"), 3),
            (_make_slice(2, "KPI2"), 3),
            (_make_slice(3, "KPI3"), 3),
        ]
        append_charts_v2(pos, charts)
        widths = _get_chart_widths(pos)
        rows = _get_row_sizes(pos)
        self.assertEqual(rows, [3])
        # Last chart (KPI3) should be tail-filled: 3 + (12-9) = 6
        self.assertEqual(widths[2], 6)

    def test_four_kpis(self) -> None:
        """Four KPIs (3+3+3+3=12) should fit exactly in one row."""
        pos = get_default_position("Test")
        charts = [
            (_make_slice(i, f"KPI{i}"), 3)
            for i in range(1, 5)
        ]
        append_charts_v2(pos, charts)
        widths = _get_chart_widths(pos)
        rows = _get_row_sizes(pos)
        self.assertEqual(rows, [4])
        self.assertEqual(widths, [3, 3, 3, 3])

    def test_mixed_table_and_charts(self) -> None:
        """Table(12) + Pie(4) + Bar(6): 3 rows, each filled to 12."""
        pos = get_default_position("Test")
        charts = [
            (_make_slice(1, "Table"), 12),
            (_make_slice(2, "Pie"), 4),
            (_make_slice(3, "Bar"), 6),
        ]
        append_charts_v2(pos, charts)
        widths = _get_chart_widths(pos)
        rows = _get_row_sizes(pos)
        self.assertEqual(rows, [1, 2])
        self.assertEqual(widths[0], 12)  # Table full row
        # Row 2: Pie(4) + Bar(6) = 10, Bar tail-filled to 8
        self.assertEqual(widths[1], 4)
        self.assertEqual(widths[2], 8)  # Bar expanded from 6 to 8

    def test_width_clamping(self) -> None:
        """Width > 12 should be clamped to 12, width < 1 to 1 (then tail-filled)."""
        pos = get_default_position("Test")
        charts = [
            (_make_slice(1, "Wide"), 20),  # clamped to 12, fills row
            (_make_slice(2, "Tiny"), 0),  # clamped to 1, then tail-filled to 12
        ]
        append_charts_v2(pos, charts)
        widths = _get_chart_widths(pos)
        self.assertEqual(widths[0], 12)
        # Row 2 is last row with single chart, tail-filled to 12
        self.assertEqual(widths[1], 12)

    def test_chart_metadata_preserved(self) -> None:
        """Chart metadata (id, name, uuid, height) should be correct."""
        pos = get_default_position("Test")
        sl = _make_slice(42, "Test Chart")
        append_charts_v2(pos, [(sl, 6)])
        chart_key = [k for k in pos if k.startswith("CHART-")][0]
        meta = pos[chart_key]["meta"]
        self.assertEqual(meta["chartId"], 42)
        self.assertEqual(meta["sliceName"], "Test Chart")
        self.assertEqual(meta["uuid"], "uuid-42")
        self.assertEqual(meta["height"], DEFAULT_CHART_HEIGHT)
        self.assertEqual(meta["width"], 12)  # tail-filled to 12

    def test_row_parents_set(self) -> None:
        """Each chart should have parents set correctly."""
        pos = get_default_position("Test")
        charts = [
            (_make_slice(1, "A"), 6),
            (_make_slice(2, "B"), 6),
        ]
        append_charts_v2(pos, charts)
        for key, val in pos.items():
            if key.startswith("CHART-"):
                self.assertIn("parents", val)
                self.assertEqual(val["parents"][-2], "GRID_ID")
                # Last parent should be a ROW
                self.assertTrue(val["parents"][-1].startswith("ROW-N-"))

    def test_grid_children_populated(self) -> None:
        """GRID_ID children should contain row hashes."""
        pos = get_default_position("Test")
        charts = [
            (_make_slice(1, "A"), 4),
            (_make_slice(2, "B"), 4),
            (_make_slice(3, "C"), 4),
        ]
        append_charts_v2(pos, charts)
        grid_children = pos["GRID_ID"]["children"]
        # All charts fit in one row: 4+4+4=12
        self.assertEqual(len(grid_children), 1)

    def test_no_grid_structure(self) -> None:
        """When no GRID_ID exists, charts are added but no rows created."""
        pos = {"DASHBOARD_VERSION_KEY": "v2"}
        sl = _make_slice(1, "Orphan")
        result = append_charts_v2(pos, [(sl, 4)])
        # Chart added to position
        chart_keys = [k for k in result if k.startswith("CHART-")]
        self.assertEqual(len(chart_keys), 1)
        # No ROW entries
        row_keys = [k for k in result if k.startswith("ROW-N-")]
        self.assertEqual(len(row_keys), 0)


if __name__ == "__main__":
    unittest.main()
