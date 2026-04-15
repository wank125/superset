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
# isort:skip_file

import logging
import random
import string
from typing import Any, Optional, Callable
from collections.abc import Iterator

import yaml

from superset.commands.chart.export import ExportChartsCommand
from superset.commands.tag.export import ExportTagsCommand
from superset.commands.dashboard.exceptions import DashboardNotFoundError
from superset.commands.dashboard.importers.v1.utils import find_chart_uuids
from superset.daos.dashboard import DashboardDAO
from superset.commands.export.models import ExportModelsCommand
from superset.commands.dataset.export import ExportDatasetsCommand
from superset.daos.dataset import DatasetDAO
from superset.models.dashboard import Dashboard
from superset.models.slice import Slice
from superset.tags.models import TagType
from superset.utils.dict_import_export import EXPORT_VERSION
from superset.utils.file import get_filename
from superset.utils import json
from superset.extensions import feature_flag_manager  # Import the feature flag manager

logger = logging.getLogger(__name__)


# keys stored as JSON are loaded and the prefix/suffix removed
JSON_KEYS = {"position_json": "position", "json_metadata": "metadata"}
DEFAULT_CHART_HEIGHT = 50
DEFAULT_CHART_WIDTH = 4


def suffix(length: int = 8) -> str:
    return "".join(
        random.SystemRandom().choice(string.ascii_uppercase + string.digits)
        for _ in range(length)
    )


def get_default_position(title: str) -> dict[str, Any]:
    return {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"children": ["GRID_ID"], "id": "ROOT_ID", "type": "ROOT"},
        "GRID_ID": {
            "children": [],
            "id": "GRID_ID",
            "parents": ["ROOT_ID"],
            "type": "GRID",
        },
        "HEADER_ID": {"id": "HEADER_ID", "meta": {"text": title}, "type": "HEADER"},
    }


def append_charts(position: dict[str, Any], charts: set[Slice]) -> dict[str, Any]:
    chart_list = list(charts)
    chart_hashes = [f"CHART-{suffix()}" for _ in chart_list]
    width_per_chart = DEFAULT_CHART_WIDTH
    max_charts_per_row = 12 // width_per_chart

    has_grid = "ROOT_ID" in position and "GRID_ID" in position["ROOT_ID"]["children"]

    for chart_hash, chart in zip(chart_hashes, chart_list, strict=False):
        position[chart_hash] = {
            "children": [],
            "id": chart_hash,
            "meta": {
                "chartId": chart.id,
                "height": DEFAULT_CHART_HEIGHT,
                "sliceName": chart.slice_name,
                "uuid": str(chart.uuid),
                "width": width_per_chart,
            },
            "type": "CHART",
        }

    if has_grid:
        for i in range(0, len(chart_hashes), max_charts_per_row):
            chunk = chart_hashes[i:i + max_charts_per_row]
            row_hash = f"ROW-N-{suffix()}"
            position["GRID_ID"]["children"].append(row_hash)
            position[row_hash] = {
                "children": chunk,
                "id": row_hash,
                "meta": {"0": "ROOT_ID", "background": "BACKGROUND_TRANSPARENT"},
                "type": "ROW",
                "parents": ["ROOT_ID", "GRID_ID"],
            }
            for chart_hash in chunk:
                position[chart_hash]["parents"] = ["ROOT_ID", "GRID_ID", row_hash]

    return position


def append_charts_v2(
    position: dict[str, Any],
    charts_with_widths: list[tuple[Slice, int]],
) -> dict[str, Any]:
    """Flow-layout bin-packing with variable widths and tail-fill.

    Each chart gets its own width (1-12). Charts are packed into rows of
    total width <= 12. When a row has remaining space and no more charts
    fit, the last chart in that row is expanded to fill the remainder.
    """
    has_grid = "ROOT_ID" in position and "GRID_ID" in position["ROOT_ID"]["children"]

    rows: list[list[tuple[str, int]]] = []  # [(chart_hash, width), ...]
    current_row: list[tuple[str, int]] = []
    current_used = 0

    for slice_obj, width in charts_with_widths:
        width = max(1, min(12, width))
        chart_hash = f"CHART-{suffix()}"
        position[chart_hash] = {
            "children": [],
            "id": chart_hash,
            "meta": {
                "chartId": slice_obj.id,
                "height": DEFAULT_CHART_HEIGHT,
                "sliceName": slice_obj.slice_name,
                "uuid": str(slice_obj.uuid),
                "width": width,
            },
            "type": "CHART",
        }

        if current_used + width > 12:
            rows.append(current_row)
            current_row = [(chart_hash, width)]
            current_used = width
        else:
            current_row.append((chart_hash, width))
            current_used += width

    if current_row:
        rows.append(current_row)

    # Single-pass tail-fill: expand the last chart in every row that
    # doesn't fill all 12 columns. Clamp to 12 to prevent overflow
    # when a single wide chart occupies most of the row.
    for row in rows:
        total = sum(w for _, w in row)
        if total < 12:
            last_hash, last_w = row[-1]
            fill_w = min(12, last_w + (12 - total))
            position[last_hash]["meta"]["width"] = fill_w
            row[-1] = (last_hash, fill_w)

    if has_grid:
        for row_items in rows:
            row_hash = f"ROW-N-{suffix()}"
            position["GRID_ID"]["children"].append(row_hash)
            chunk_hashes = [h for h, _ in row_items]
            position[row_hash] = {
                "children": chunk_hashes,
                "id": row_hash,
                "meta": {"0": "ROOT_ID", "background": "BACKGROUND_TRANSPARENT"},
                "type": "ROW",
                "parents": ["ROOT_ID", "GRID_ID"],
            }
            for chart_hash, _ in row_items:
                position[chart_hash]["parents"] = ["ROOT_ID", "GRID_ID", row_hash]

    return position


class ExportDashboardsCommand(ExportModelsCommand):
    dao = DashboardDAO
    not_found = DashboardNotFoundError

    @staticmethod
    def _file_name(model: Dashboard) -> str:
        file_name = get_filename(model.dashboard_title, model.id)
        return f"dashboards/{file_name}.yaml"

    @staticmethod
    # ruff: noqa: C901
    def _file_content(model: Dashboard) -> str:
        payload = model.export_to_dict(
            recursive=False,
            include_parent_ref=False,
            include_defaults=True,
            export_uuids=True,
        )
        # TODO (betodealmeida): move this logic to export_to_dict once this
        #  becomes the default export endpoint
        for key, new_name in JSON_KEYS.items():
            value: Optional[str] = payload.pop(key, None)
            if value:
                try:
                    payload[new_name] = json.loads(value)
                except (TypeError, json.JSONDecodeError):
                    logger.info("Unable to decode `%s` field: %s", key, value)
                    payload[new_name] = {}

        # Extract all native filter datasets and replace native
        # filter dataset references with uuid
        for native_filter in payload.get("metadata", {}).get(
            "native_filter_configuration", []
        ):
            for target in native_filter.get("targets", []):
                dataset_id = target.pop("datasetId", None)
                if dataset_id is not None:
                    dataset = DatasetDAO.find_by_id(dataset_id)
                    if dataset:
                        target["datasetUuid"] = str(dataset.uuid)

        # the mapping between dashboard -> charts is inferred from the position
        # attribute, so if it's not present we need to add a default config
        if not payload.get("position"):
            payload["position"] = get_default_position(model.dashboard_title)

        # if any charts or not referenced in position, we need to add them
        # in a new row
        referenced_charts = find_chart_uuids(payload["position"])
        orphan_charts = {
            chart for chart in model.slices if str(chart.uuid) not in referenced_charts
        }

        if orphan_charts:
            payload["position"] = append_charts(payload["position"], orphan_charts)

        # Add theme UUID for proper cross-system imports
        payload["theme_uuid"] = str(model.theme.uuid) if model.theme else None

        payload["version"] = EXPORT_VERSION

        # Check if the TAGGING_SYSTEM feature is enabled
        if feature_flag_manager.is_feature_enabled("TAGGING_SYSTEM"):
            tags = model.tags if hasattr(model, "tags") else []
            payload["tags"] = [tag.name for tag in tags if tag.type == TagType.custom]

        file_content = yaml.safe_dump(payload, sort_keys=False)
        return file_content

    @staticmethod
    # ruff: noqa: C901
    def _export(
        model: Dashboard, export_related: bool = True
    ) -> Iterator[tuple[str, Callable[[], str]]]:
        yield (
            ExportDashboardsCommand._file_name(model),
            lambda: ExportDashboardsCommand._file_content(model),
        )

        if export_related:
            chart_ids = [chart.id for chart in model.slices]
            dashboard_ids = model.id
            command = ExportChartsCommand(chart_ids)
            command.disable_tag_export()
            yield from command.run()
            command.enable_tag_export()
            if feature_flag_manager.is_feature_enabled("TAGGING_SYSTEM"):
                yield from ExportTagsCommand.export(
                    dashboard_ids=dashboard_ids, chart_ids=chart_ids
                )

            # Export related theme
            if model.theme:
                from superset.commands.theme.export import ExportThemesCommand

                yield from ExportThemesCommand([model.theme.id]).run()

        payload = model.export_to_dict(
            recursive=False,
            include_parent_ref=False,
            include_defaults=True,
            export_uuids=True,
        )
        # TODO (betodealmeida): move this logic to export_to_dict once this
        #  becomes the default export endpoint
        for key, new_name in JSON_KEYS.items():
            value: Optional[str] = payload.pop(key, None)
            if value:
                try:
                    payload[new_name] = json.loads(value)
                except (TypeError, json.JSONDecodeError):
                    logger.info("Unable to decode `%s` field: %s", key, value)
                    payload[new_name] = {}

        if export_related:
            # Extract all native filter datasets and export referenced datasets
            for native_filter in payload.get("metadata", {}).get(
                "native_filter_configuration", []
            ):
                for target in native_filter.get("targets", []):
                    dataset_id = target.pop("datasetId", None)
                    if dataset_id is not None:
                        dataset = DatasetDAO.find_by_id(dataset_id)
                        if dataset:
                            yield from ExportDatasetsCommand([dataset_id]).run()
