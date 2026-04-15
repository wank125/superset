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

import { useMemo } from 'react';
import { styled } from '@superset-ui/core';
import type { InlineChartData } from '../../utils/chatMsgAdapter';

interface DataTableProps {
  data: InlineChartData;
  /** Max rows to show before truncation. */
  maxRows?: number;
  /** Callback when user clicks a category column value to drill down. */
  onDrillDown?: (dimension: string) => void;
}

const TableWrapper = styled.div`
  margin: 8px 0;
  border: 1px solid ${({ theme }) => theme.colorBorderSecondary};
  border-radius: 8px;
  overflow: auto;
  max-height: 360px;
`;

const StyledTable = styled.table`
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;

  th {
    position: sticky;
    top: 0;
    background: ${({ theme }) => theme.colorBgLayout};
    padding: 8px 12px;
    text-align: left;
    font-weight: ${({ theme }) => theme.fontWeightStrong};
    border-bottom: 2px solid ${({ theme }) => theme.colorBorderSecondary};
    white-space: nowrap;
  }

  td {
    padding: 6px 12px;
    border-bottom: 1px solid ${({ theme }) => theme.colorBorderSecondary};
    white-space: nowrap;
  }

  tr:hover td {
    background: ${({ theme }) => theme.colorBgTextHover};
  }

  .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
`;

const MoreRow = styled.div`
  padding: 8px 12px;
  text-align: center;
  color: ${({ theme }) => theme.colorTextSecondary};
  font-size: 12px;
  border-top: 1px solid ${({ theme }) => theme.colorBorderSecondary};
`;

const DrillLink = styled.button`
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  color: ${({ theme }) => theme.colorPrimary};
  font-size: inherit;

  &:hover {
    text-decoration: underline;
  }
`;

export function DataTable({ data, maxRows = 50, onDrillDown }: DataTableProps) {
  const { columns, rows } = data;

  const displayRows = useMemo(
    () => rows.slice(0, maxRows),
    [rows, maxRows],
  );

  const numColSet = useMemo(
    () => new Set(data.metricCols),
    [data.metricCols],
  );

  if (rows.length === 0) return null;

  return (
    <TableWrapper>
      <StyledTable>
        <thead>
          <tr>
            {columns.map(col => (
              <th key={col.name}>{col.name}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {displayRows.map((row, i) => (
            <tr key={i}>
              {columns.map(col => {
                const val = String(row[col.name] ?? '');
                const isCategory = col.showType === 'CATEGORY';
                return (
                  <td key={col.name} className={numColSet.has(col.name) ? 'num' : ''}>
                    {isCategory && onDrillDown ? (
                      <DrillLink
                        type="button"
                        onClick={() => onDrillDown(col.name)}
                        title={`按 ${col.name} 拆分`}
                      >
                        {val}
                      </DrillLink>
                    ) : (
                      val
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </StyledTable>
      {rows.length > maxRows && (
        <MoreRow>
          Showing {maxRows} of {rows.length} rows
        </MoreRow>
      )}
    </TableWrapper>
  );
}
