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
"""
SuperSonic Data Import for Superset

This module imports SuperSonic demo data into Superset for visualization.
It creates datasets, charts, and dashboards for SuperSonic's demo tables.

Usage:
    from superset.examples import supersonic
    supersonic.load_supersonic_data()

Requirements:
    - SuperSonic demo tables must exist in the database
    - Default database connection must be configured
"""
import logging
import textwrap
from typing import Any

from flask import current_app
from sqlalchemy import String, inspect

import superset.utils.database
from superset import db
from superset.connectors.sqla.models import BaseDatasource, SqlMetric
from superset.examples.helpers import (
    get_slice_json,
    get_table_connector_registry,
    merge_slice,
    update_slice_ids,
)
from superset.models.dashboard import Dashboard
from superset.models.slice import Slice
from superset.sql.parse import Table
from superset.utils import core as utils, json
from superset.utils.core import DatasourceType

logger = logging.getLogger(__name__)

# SuperSonic Demo Tables Configuration
SUPERSONIC_TABLES = {
    # S2VisitsDemo
    "s2_user_department": {
        "description": "User to Department mapping from SuperSonic",
        "main_dttm_col": None,
        "metrics": [],
        "dimensions": ["user_name", "department"],
    },
    "s2_pv_uv_statis": {
        "description": "Page View/UV Statistics from SuperSonic",
        "main_dttm_col": "imp_date",
        "metrics": ["count"],
        "dimensions": ["imp_date", "user_name", "page"],
    },
    "s2_stay_time_statis": {
        "description": "Stay Time Statistics from SuperSonic",
        "main_dttm_col": "imp_date",
        "metrics": ["sum__stay_hours", "avg__stay_hours"],
        "dimensions": ["imp_date", "user_name", "page", "stay_hours"],
    },
    # S2ArtistDemo
    "singer": {
        "description": "Singer information from SuperSonic",
        "main_dttm_col": None,
        "metrics": ["sum__js_play_cnt", "sum__down_cnt", "sum__favor_cnt"],
        "dimensions": ["singer_name", "act_area", "song_name", "genre"],
    },
    "genre": {
        "description": "Music genre information from SuperSonic",
        "main_dttm_col": None,
        "metrics": [],
        "dimensions": ["g_name", "rating", "most_popular_in"],
    },
    # S2CompanyDemo
    "company": {
        "description": "Company information from SuperSonic",
        "main_dttm_col": "company_established_time",
        "metrics": ["sum__annual_turnover", "avg__employee_count"],
        "dimensions": [
            "company_id",
            "company_name",
            "headquarter_address",
            "company_established_time",
            "founder",
            "ceo",
            "annual_turnover",
            "employee_count",
        ],
    },
    "brand": {
        "description": "Brand information from SuperSonic",
        "main_dttm_col": "brand_established_time",
        "metrics": ["sum__registered_capital"],
        "dimensions": [
            "brand_id",
            "brand_name",
            "brand_established_time",
            "company_id",
            "legal_representative",
            "registered_capital",
        ],
    },
    "brand_revenue": {
        "description": "Brand revenue data from SuperSonic",
        "main_dttm_col": "year_time",
        "metrics": [
            "sum__revenue",
            "sum__profit",
            "avg__revenue_growth_year_on_year",
            "avg__profit_growth_year_on_year",
        ],
        "dimensions": [
            "year_time",
            "brand_id",
            "revenue",
            "profit",
            "revenue_growth_year_on_year",
            "profit_growth_year_on_year",
        ],
    },
}

# Dashboard positions for SuperSonic dashboard
SUPERSONIC_DASHBOARD_POSITIONS = {
    "CHART-pv-count": {
        "children": [],
        "id": "CHART-pv-count",
        "meta": {"chartId": 5001, "height": 30, "sliceName": "Total Page Views", "width": 4},
        "type": "CHART",
    },
    "CHART-pv-trend": {
        "children": [],
        "id": "CHART-pv-trend",
        "meta": {"chartId": 5002, "height": 50, "sliceName": "Page Views Trend", "width": 8},
        "type": "CHART",
    },
    "CHART-page-ranking": {
        "children": [],
        "id": "CHART-page-ranking",
        "meta": {
            "chartId": 5003,
            "height": 50,
            "sliceName": "Page Popularity Ranking",
            "width": 6,
        },
        "type": "CHART",
    },
    "CHART-department-pv": {
        "children": [],
        "id": "CHART-department-pv",
        "meta": {"chartId": 5004, "height": 50, "sliceName": "Page Views by Department", "width": 6},
        "type": "CHART",
    },
    "CHART-stay-time": {
        "children": [],
        "id": "CHART-stay-time",
        "meta": {"chartId": 5005, "height": 50, "sliceName": "Average Stay Time", "width": 6},
        "type": "CHART",
    },
    "CHART-singer-stats": {
        "children": [],
        "id": "CHART-singer-stats",
        "meta": {"chartId": 5006, "height": 50, "sliceName": "Singer Play Statistics", "width": 6},
        "type": "CHART",
    },
    "CHART-brand-revenue": {
        "children": [],
        "id": "CHART-brand-revenue",
        "meta": {"chartId": 5007, "height": 50, "sliceName": "Brand Revenue Trend", "width": 12},
        "type": "CHART",
    },
    "CHART-company-revenue": {
        "children": [],
        "id": "CHART-company-revenue",
        "meta": {"chartId": 5008, "height": 50, "sliceName": "Company Revenue Comparison", "width": 6},
        "type": "CHART",
    },
    "CHART-brand-profit": {
        "children": [],
        "id": "CHART-brand-profit",
        "meta": {"chartId": 5009, "height": 50, "sliceName": "Brand Profit Analysis", "width": 6},
        "type": "CHART",
    },
    "DASHBOARD_VERSION_KEY": "v2",
    "GRID_ID": {
        "children": ["ROW-1", "ROW-2", "ROW-3", "ROW-4"],
        "id": "GRID_ID",
        "type": "GRID",
    },
    "HEADER_ID": {
        "id": "HEADER_ID",
        "meta": {"text": "SuperSonic Data Visualization"},
        "type": "HEADER",
    },
    "ROOT_ID": {"children": ["GRID_ID"], "id": "ROOT_ID", "type": "ROOT"},
    "ROW-1": {
        "children": ["CHART-pv-count", "CHART-pv-trend"],
        "id": "ROW-1",
        "meta": {"background": "BACKGROUND_TRANSPARENT"},
        "type": "ROW",
    },
    "ROW-2": {
        "children": ["CHART-page-ranking", "CHART-department-pv"],
        "id": "ROW-2",
        "meta": {"background": "BACKGROUND_TRANSPARENT"},
        "type": "ROW",
    },
    "ROW-3": {
        "children": ["CHART-stay-time", "CHART-singer-stats"],
        "id": "ROW-3",
        "meta": {"background": "BACKGROUND_TRANSPARENT"},
        "type": "ROW",
    },
    "ROW-4": {
        "children": ["CHART-brand-revenue"],
        "id": "ROW-4",
        "meta": {"background": "BACKGROUND_TRANSPARENT"},
        "type": "ROW",
    },
}


def get_database_by_name(database_name: str | None = None):
    """Get database by name or return the example database."""
    from superset.models.core import Database

    if database_name:
        database = db.session.query(Database).filter_by(database_name=database_name).first()
        if database:
            return database
        logger.warning(f"Database '{database_name}' not found, using example database")

    return superset.utils.database.get_example_database()


def load_supersonic_tables(
    database_name: str | None = None, only_metadata: bool = False, force: bool = False
) -> dict[str, Any]:
    """
    Load SuperSonic tables into Superset as datasets.

    Args:
        database_name: Name of the database where SuperSonic tables exist.
                     If None, uses the example database.
        only_metadata: If True, only create metadata without fetching table stats.
        force: If True, refresh metadata even if table already exists.

    Returns:
        Dictionary mapping table names to SqlaTable instances.
    """
    database = get_database_by_name(database_name)
    result: dict[str, Any] = {}

    with database.get_sqla_engine() as engine:
        schema = inspect(engine).default_schema_name

        for tbl_name, config in SUPERSONIC_TABLES.items():
            table = Table(tbl_name, schema)

            # Check if table exists in database
            if not database.has_table(table):
                logger.warning(f"Table '{tbl_name}' not found in database, skipping")
                continue

            logger.debug(f"Creating table reference [{tbl_name}]")
            table_class = get_table_connector_registry()
            tbl = db.session.query(table_class).filter_by(table_name=tbl_name).first()

            if not tbl:
                tbl = table_class(table_name=tbl_name, schema=schema)
                db.session.add(tbl)

            tbl.description = config["description"]
            tbl.main_dttm_col = config["main_dttm_col"]
            tbl.database = database
            tbl.filter_select_enabled = True

            # Create metrics
            for metric in config["metrics"]:
                if metric == "count":
                    metric_name = "count"
                    if not any(col.metric_name == metric_name for col in tbl.metrics):
                        tbl.metrics.append(SqlMetric(metric_name=metric_name, expression="COUNT(*)"))
                elif "__" in metric:
                    if not any(col.metric_name == metric for col in tbl.metrics):
                        aggr_func = metric.split("__")[0]
                        col_name = metric.split("__")[1]
                        from sqlalchemy.sql import column

                        col = str(column(col_name).compile(db.engine))
                        tbl.metrics.append(
                            SqlMetric(metric_name=metric, expression=f"{aggr_func.upper()}({col})")
                        )

            if not only_metadata or force:
                tbl.fetch_metadata()

            db.session.commit()
            result[tbl_name] = tbl

    return result


def create_supersonic_slices(tables: dict[str, Any]) -> list[Slice]:
    """Create chart slices for SuperSonic data visualization."""
    from flask import current_app

    defaults = {
        "row_limit": current_app.config.get("ROW_LIMIT", 50000),
        "groupby": [],
        "metrics": [],
        "time_range": "No filter",
    }

    slices = []

    # Check which tables exist
    pv_table = tables.get("s2_pv_uv_statis")
    stay_table = tables.get("s2_stay_time_statis")
    dept_table = tables.get("s2_user_department")
    singer_table = tables.get("singer")
    brand_revenue_table = tables.get("brand_revenue")
    brand_table = tables.get("brand")
    company_table = tables.get("company")

    if pv_table:
        # Total Page Views - Big Number
        slices.append(
            Slice(
                slice_name="Total Page Views",
                viz_type="big_number",
                datasource_type=DatasourceType.TABLE,
                datasource_id=pv_table.id,
                params=get_slice_json(
                    defaults,
                    viz_type="big_number",
                    metric="count",
                    subheader="Total page views",
                ),
            )
        )

        # Page Views Trend - Line Chart
        slices.append(
            Slice(
                slice_name="Page Views Trend",
                viz_type="echarts_timeseries_line",
                datasource_type=DatasourceType.TABLE,
                datasource_id=pv_table.id,
                params=get_slice_json(
                    defaults,
                    viz_type="echarts_timeseries_line",
                    x_axis="imp_date",
                    time_grain_sqla="day",
                    metrics=["count"],
                    groupby=["imp_date"],
                    time_range="Last 30 days",
                ),
            )
        )

        # Page Popularity Ranking - Bar Chart
        slices.append(
            Slice(
                slice_name="Page Popularity Ranking",
                viz_type="echarts_bar",
                datasource_type=DatasourceType.TABLE,
                datasource_id=pv_table.id,
                params=get_slice_json(
                    defaults,
                    viz_type="echarts_bar",
                    x_axis="page",
                    metrics=["count"],
                    groupby=["page"],
                    row_limit=10,
                    order_desc=True,
                ),
            )
        )

        # Page Views by Department (joined with user_department) - need custom SQL
        # For now, create a simple table view
        slices.append(
            Slice(
                slice_name="Page Views by Department",
                viz_type="table",
                datasource_type=DatasourceType.TABLE,
                datasource_id=pv_table.id,
                params=get_slice_json(
                    defaults,
                    viz_type="table",
                    metrics=["count"],
                    groupby=["user_name"],
                    row_limit=25,
                ),
            )
        )

    if stay_table:
        # Average Stay Time
        slices.append(
            Slice(
                slice_name="Average Stay Time",
                viz_type="big_number",
                datasource_type=DatasourceType.TABLE,
                datasource_id=stay_table.id,
                params=get_slice_json(
                    defaults,
                    viz_type="big_number",
                    metric="avg__stay_hours",
                    subheader="Average hours per visit",
                    time_range="Last 30 days",
                ),
            )
        )

    if singer_table:
        # Singer Play Statistics
        slices.append(
            Slice(
                slice_name="Singer Play Statistics",
                viz_type="echarts_bar",
                datasource_type=DatasourceType.TABLE,
                datasource_id=singer_table.id,
                params=get_slice_json(
                    defaults,
                    viz_type="echarts_bar",
                    x_axis="singer_name",
                    metrics=["sum__js_play_cnt"],
                    groupby=["singer_name"],
                    row_limit=10,
                    order_desc=True,
                ),
            )
        )

    if brand_revenue_table:
        # Brand Revenue Trend
        slices.append(
            Slice(
                slice_name="Brand Revenue Trend",
                viz_type="echarts_timeseries_line",
                datasource_type=DatasourceType.TABLE,
                datasource_id=brand_revenue_table.id,
                params=get_slice_json(
                    defaults,
                    viz_type="echarts_timeseries_line",
                    x_axis="year_time",
                    time_grain_sqla="year",
                    metrics=["sum__revenue", "sum__profit"],
                    groupby=["year_time"],
                ),
            )
        )

    if brand_table:
        # Company Revenue Comparison
        slices.append(
            Slice(
                slice_name="Company Revenue Comparison",
                viz_type="echarts_pie",
                datasource_type=DatasourceType.TABLE,
                datasource_id=brand_table.id,
                params=get_slice_json(
                    defaults,
                    viz_type="echarts_pie",
                    metrics=["sum__registered_capital"],
                    groupby=["brand_name"],
                    row_limit=10,
                ),
            )
        )

    if brand_revenue_table:
        # Brand Profit Analysis
        slices.append(
            Slice(
                slice_name="Brand Profit Analysis",
                viz_type="echarts_area",
                datasource_type=DatasourceType.TABLE,
                datasource_id=brand_revenue_table.id,
                params=get_slice_json(
                    defaults,
                    viz_type="echarts_area",
                    x_axis="year_time",
                    time_grain_sqla="year",
                    metrics=["sum__profit"],
                    groupby=["year_time"],
                ),
            )
        )

    return slices


def load_supersonic_dashboard(
    database_name: str | None = None, only_metadata: bool = False, force: bool = False
) -> None:
    """
    Load SuperSonic data, create charts and dashboard in Superset.

    Args:
        database_name: Name of the database where SuperSonic tables exist.
                     If None, uses the example database.
        only_metadata: If True, only create metadata without charts/dashboard.
        force: If True, refresh all metadata.
    """
    logger.info("Loading SuperSonic data into Superset...")

    # Load tables
    tables = load_supersonic_tables(database_name=database_name, only_metadata=only_metadata, force=force)

    if not tables:
        logger.warning("No SuperSonic tables found in database")
        return

    # Create slices
    slices = create_supersonic_slices(tables)

    # Merge slices into database
    for slc in slices:
        merge_slice(slc)

    # Create dashboard
    dash_slug = "supersonic_data"
    dash = db.session.query(Dashboard).filter_by(slug=dash_slug).first()

    if not dash:
        dash = Dashboard()
        db.session.add(dash)

    dash.published = True
    pos = SUPERSONIC_DASHBOARD_POSITIONS
    updated_slices = update_slice_ids(pos)

    dash.dashboard_title = "SuperSonic Data Visualization"
    dash.position_json = json.dumps(pos, indent=4)
    dash.slug = dash_slug
    dash.slices = updated_slices

    db.session.commit()
    logger.info(f"SuperSonic dashboard loaded successfully at /superset/dashboard/{dash_slug}/")


# Convenience function for easy importing
def load_supersonic_data(database_name: str | None = None) -> None:
    """
    Load SuperSonic demo data into Superset.

    This is the main entry point for importing SuperSonic data.

    Usage:
        from superset.examples import supersonic
        supersonic.load_supersonic_data()

        # Or specify a custom database
        supersonic.load_supersonic_data(database_name="my_super sonic_db")
    """
    load_supersonic_dashboard(database_name=database_name)
