/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */

/**
 * Data format adapter: Superset AI Agent query results → inline chart components.
 *
 * Converts structured SQL results (from `data_analyzed` events) into
 * chart-friendly data shapes. Each column is classified by showType so
 * chart components can auto-select the right visualization.
 */

import type { SqlQueryResult } from '../types';

// Re-export for convenience
export type { SqlQueryResult };

// ── Target types (for inline chart components) ──────────────────────

export type ShowType = 'NUMBER' | 'CATEGORY' | 'DATE';

/** Column descriptor understood by chart components. */
export interface ChartColumn {
  name: string;
  showType: ShowType;
}

/** The normalized data structure consumed by AiInlineChart. */
export interface InlineChartData {
  columns: ChartColumn[];
  rows: Record<string, unknown>[];
  /** Pre-extracted axis columns (may be undefined if not applicable). */
  dateCol?: string;
  categoryCols: string[];
  metricCols: string[];
  /** Period-over-period statistics for KPI cards. */
  statistics?: Record<string, string>;
}

/** Chart type inferred from data shape. */
export type InlineChartType =
  | 'kpi'
  | 'trend'
  | 'bar'
  | 'pie'
  | 'table';

// ── Adapter ─────────────────────────────────────────────────────────

type SourceColumn = SqlQueryResult['columns'][number];

function toShowType(col: SourceColumn): ShowType {
  if (col.is_dttm || col.type === 'DATETIME') return 'DATE';
  if (col.type === 'INTEGER' || col.type === 'FLOAT') return 'NUMBER';
  return 'CATEGORY';
}

/** Convert raw SQL result to chart-friendly structure. */
export function adaptQueryResult(raw: SqlQueryResult): InlineChartData {
  const columns: ChartColumn[] = raw.columns.map(c => ({
    name: c.name,
    showType: toShowType(c),
  }));

  const dateCol = columns.find(c => c.showType === 'DATE')?.name;
  const categoryCols = columns
    .filter(c => c.showType === 'CATEGORY')
    .map(c => c.name);
  const metricCols = columns
    .filter(c => c.showType === 'NUMBER')
    .map(c => c.name);

  return {
    columns,
    rows: raw.rows,
    dateCol,
    categoryCols,
    metricCols,
    statistics: raw.statistics,
  };
}

/** Infer the best chart type from data shape. */
export function inferChartType(data: InlineChartData): InlineChartType {
  const { dateCol, categoryCols, metricCols, rows } = data;

  // Single row + single metric → KPI card
  if (rows.length === 1 && metricCols.length >= 1 && !dateCol) {
    return 'kpi';
  }

  // Has date column → trend chart
  if (dateCol && metricCols.length > 0) {
    return 'trend';
  }

  // Has category column + few rows → bar or pie
  if (categoryCols.length > 0 && metricCols.length > 0) {
    if (rows.length <= 8) return 'pie';
    if (rows.length <= 20) return 'bar';
    return 'table';
  }

  return 'table';
}
