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

import { SupersetClient } from '@superset-ui/core';
import type {
  AiChatRequest,
  AiChatResponse,
  AiEventsResponse,
  AiAlertConfigResponse,
  ChartPreviewData,
  ChartResult,
} from '../types';

export function sendChat(payload: AiChatRequest): Promise<AiChatResponse> {
  return SupersetClient.post({
    endpoint: '/api/v1/ai/chat/',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(({ json }) => json as AiChatResponse);
}

export function fetchEvents(
  channelId: string,
  lastId: string,
): Promise<AiEventsResponse> {
  return SupersetClient.get({
    endpoint: `/api/v1/ai/events/?channel_id=${channelId}&last_id=${lastId}`,
  }).then(({ json }) => json as AiEventsResponse);
}

export function generateAlertConfig(payload: {
  message: string;
  database_id: number;
  schema_name?: string;
}): Promise<AiAlertConfigResponse> {
  return SupersetClient.post({
    endpoint: '/api/v1/ai/alert/generate/',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(({ json }) => json as AiAlertConfigResponse);
}

/** Ensure every adhoc metric has a `label` field (required by buildQuery). */
function ensureMetricLabels(
  metrics: unknown[],
): unknown[] {
  return metrics.map(m => {
    if (typeof m === 'string') return m;
    if (typeof m === 'object' && m !== null) {
      const metric = { ...(m as Record<string, unknown>) };
      if (!metric.label) {
        const agg = (metric.aggregate as string) || 'SUM';
        const col =
          (metric.column as Record<string, unknown>)?.column_name ||
          'value';
        metric.label = `${agg}(${col})`;
      }
      return metric;
    }
    return m;
  });
}

/**
 * Viz types that use `metric` (singular) in buildQuery rather than `metrics` (plural).
 * Must stay in sync with catalog.py `uses_metric_singular=True` entries.
 */
const SINGULAR_METRIC_VIZ = new Set([
  'pie',
  'echarts_pie',
  'big_number_total',
  'big_number',
]);

/** Strip form_data fields that are incompatible with the target viz_type. */
function cleanFormData(
  raw: Record<string, unknown>,
  vizType: string,
): Record<string, unknown> {
  const fd = { ...raw, viz_type: vizType };

  // Convert plural metrics → singular metric for viz types that expect it
  if (SINGULAR_METRIC_VIZ.has(vizType) && Array.isArray(fd.metrics) && !fd.metric) {
    const labelled = ensureMetricLabels(fd.metrics);
    // Pie/KPI use metric (singular) — this conversion is needed for the save API
    // eslint-disable-next-line @superset-ui/core/no-singleton-metric-access
    fd.metric = labelled[0] ?? null;
    delete fd.metrics;
  } else if (Array.isArray(fd.metrics)) {
    fd.metrics = ensureMetricLabels(fd.metrics);
  }

  // Remove viz-type-incompatible fields
  if (vizType === 'pie' || vizType === 'echarts_pie') {
    delete fd.x_axis;
    delete fd.time_grain_sqla;
  }
  if (vizType === 'big_number_total' || vizType === 'big_number') {
    delete fd.x_axis;
    delete fd.groupby;
  }
  if (vizType === 'table') {
    delete fd.x_axis;
  }

  return fd;
}

export async function savePreviewAsChart(
  preview: ChartPreviewData,
): Promise<ChartResult> {
  const formData = cleanFormData(preview.formData || {}, preview.vizType);
  const payload = {
    slice_name: preview.sliceName || 'AI Chart',
    viz_type: preview.vizType,
    datasource_id: preview.datasourceId,
    datasource_type: 'table',
    params: JSON.stringify(formData),
  };
  const { json } = await SupersetClient.post({
    endpoint: '/api/v1/chart/',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const result = (json as { result: { id: number; slice_name: string; viz_type: string } }).result;
  return {
    chartId: result.id,
    sliceName: result.slice_name,
    vizType: result.viz_type,
    exploreUrl: `/explore/?slice_id=${result.id}`,
  };
}
