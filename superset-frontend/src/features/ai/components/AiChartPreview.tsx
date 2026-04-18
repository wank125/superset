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

import { useState } from 'react';
import { t, styled } from '@superset-ui/core';
import { Card, Button, Tag } from '@superset-ui/core/components';
import type { ChartPreviewData, SqlQueryResult } from '../types';
import { AiInlineChart } from './AiInlineChart';

interface AiChartPreviewProps {
  preview: ChartPreviewData;
  onSuggestQuestion?: (q: string) => void;
  onSave?: (preview: ChartPreviewData) => Promise<void>;
}

const VIZ_TYPE_LABELS: Record<string, string> = {
  echarts_timeseries_line: t('折线图'),
  echarts_timeseries_bar: t('柱状图'),
  pie: t('饼图'),
  echarts_pie: t('饼图'),
  big_number_total: 'KPI',
  big_number: 'KPI',
  table: t('表格'),
  echarts_timeseries_scatter: t('散点图'),
  echarts_area: t('面积图'),
  echarts_timeseries_area: t('面积图'),
  radar: t('雷达图'),
  echarts_radar: t('雷达图'),
  funnel: t('漏斗图'),
  echarts_funnel: t('漏斗图'),
  gauge_chart: t('仪表盘'),
  gauge: t('仪表盘'),
  echarts_timeseries_step: t('阶梯图'),
  echarts_timeseries_smooth: t('平滑线'),
  waterfall: t('瀑布图'),
  treemap_v2: t('矩形树图'),
  sunburst_v2: t('旭日图'),
  histogram_v2: t('直方图'),
  heatmap_v2: t('热力图'),
  box_plot: t('箱线图'),
  echarts_boxplot: t('箱线图'),
  pivot_table_v2: t('透视表'),
  bubble_v2: t('气泡图'),
  sankey_v2: t('桑基图'),
  graph_chart: t('网络图'),
};

type ViewMode = 'chart' | 'table';

export function AiChartPreview({
  preview,
  onSuggestQuestion,
  onSave,
}: AiChartPreviewProps) {
  const [viewMode, setViewMode] = useState<ViewMode>('chart');

  const queryResult: SqlQueryResult | null =
    preview.columns && preview.rows && preview.rows.length > 0
      ? {
          columns: preview.columns,
          rows: preview.rows,
          row_count: preview.row_count ?? preview.rows.length,
          insight: preview.insight,
        }
      : null;

  const hasData = queryResult !== null;
  const chartLabel = VIZ_TYPE_LABELS[preview.vizType] ?? preview.vizType;

  const handleSave = async () => {
    if (!onSave) return;
    await onSave(preview);
  };

  const toggleStyle = (active: boolean): React.CSSProperties => ({
    fontSize: 11,
    cursor: 'pointer',
    userSelect: 'none',
    opacity: active ? 1 : 0.5,
    transition: 'opacity 0.2s',
  });

  return (
    <Card
      size="small"
      title={
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          {preview.sliceName || t('图表预览')}
          <Tag color="warning" style={{ fontSize: 11 }}>
            {t('预览')}
          </Tag>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <Tag
              color={viewMode === 'chart' ? 'blue' : undefined}
              style={toggleStyle(viewMode === 'chart')}
              onClick={() => setViewMode('chart')}
            >
              {chartLabel}
            </Tag>
            <Tag
              color={viewMode === 'table' ? 'blue' : undefined}
              style={toggleStyle(viewMode === 'table')}
              onClick={() => setViewMode('table')}
            >
              {t('表格')}
            </Tag>
          </span>
        </span>
      }
      style={{ marginBottom: 12 }}
      extra={
        onSave && (
          <Button size="small" type="primary" onClick={handleSave}>
            {t('保存到 Superset')}
          </Button>
        )
      }
    >
      {hasData ? (
        viewMode === 'chart' ? (
          <AiInlineChart
            result={queryResult!}
            insight={preview.insight}
            vizTypeHint={preview.vizType}
            suggestQuestions={preview.suggestQuestions}
            onSuggestQuestion={onSuggestQuestion}
            formData={preview.formData}
            datasourceId={preview.datasourceId}
          />
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table
              style={{
                width: '100%',
                borderCollapse: 'collapse',
                fontSize: 13,
              }}
            >
              <thead>
                <tr>
                  {queryResult!.columns.map(col => (
                    <th
                      key={col.name}
                      style={{
                        padding: '8px 12px',
                        textAlign: 'left',
                        borderBottom: '2px solid #e8e8e8',
                        fontWeight: 600,
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {col.name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {queryResult!.rows.map((row, i) => (
                  <tr key={i}>
                    {queryResult!.columns.map(col => (
                      <td
                        key={col.name}
                        style={{
                          padding: '6px 12px',
                          borderBottom: '1px solid #f0f0f0',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {String(row[col.name] ?? '')}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : (
        <div style={{ textAlign: 'center', padding: 24, color: '#aaa' }}>
          {t('暂无预览数据')}
        </div>
      )}
    </Card>
  );
}
