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

import { useEffect, useRef, useState } from 'react';
import { styled, t } from '@superset-ui/core';
import { getChartPermalink } from 'src/utils/urlUtils';

interface IframeChartProps {
  formData: Record<string, unknown>;
  vizType: string;
  datasourceId: number;
  chartId?: number;
  height?: number;
}

const Container = styled.div`
  position: relative;
  width: 100%;
  background: ${({ theme }) => theme.colorBgContainer};
  border-radius: ${({ theme }) => theme.borderRadius}px;
  overflow: hidden;
`;

const LoadingSkeleton = styled.div<{ $height: number }>`
  height: ${({ $height }) => $height}px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: ${({ theme }) => theme.colorTextSecondary};
  font-size: 13px;
  gap: 8px;

  .anticon {
    font-size: 18px;
  }
`;

const ErrorFallback = styled.div<{ $height: number }>`
  height: ${({ $height }) => $height}px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: ${({ theme }) => theme.colorTextSecondary};
  font-size: 13px;
  gap: 4px;
`;

const StyledIframe = styled.iframe<{ $height: number }>`
  width: 100%;
  height: ${({ $height }) => $height}px;
  border: none;
  display: block;
`;

/**
 * Renders complex Superset chart types via iframe permalink.
 * Uses the /api/v1/explore/permalink API to generate a standalone chart URL.
 */
export function IframeChart({
  formData,
  vizType,
  datasourceId,
  chartId,
  height = 400,
}: IframeChartProps) {
  const [iframeSrc, setIframeSrc] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const mountedRef = useRef(true);
  const fetchedRef = useRef('');

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    const key = `${datasourceId}:${vizType}:${JSON.stringify(formData)}`;
    if (fetchedRef.current === key) return;
    fetchedRef.current = key;

    let cancelled = false;

    async function fetchPermalink() {
      setLoading(true);
      setError(false);

      try {
        // If we have a saved chart, use its slice URL directly
        if (chartId) {
          const url = `/explore/?slice_id=${chartId}&standalone=1&height=${height}`;
          if (!cancelled && mountedRef.current) {
            setIframeSrc(url);
          }
          return;
        }

        // Otherwise, generate a permalink from form_data
        const fullFormData = {
          ...formData,
          viz_type: vizType,
          datasource: `${datasourceId}__table`,
        };

        const permalinkUrl = await getChartPermalink(
          fullFormData as Parameters<typeof getChartPermalink>[0],
        );
        const url = `${permalinkUrl}?standalone=1&height=${height}`;

        if (!cancelled && mountedRef.current) {
          setIframeSrc(url);
        }
      } catch {
        if (!cancelled && mountedRef.current) {
          setError(true);
        }
      }
    }

    fetchPermalink();
    return () => {
      cancelled = true;
    };
  }, [formData, vizType, datasourceId, chartId, height]);

  if (error) {
    return (
      <Container>
        <ErrorFallback $height={height}>
          <span>{t('图表预览暂时不可用')}</span>
          <span style={{ fontSize: 12 }}>
            {t('保存图表后可在 Superset 中查看')}
          </span>
        </ErrorFallback>
      </Container>
    );
  }

  return (
    <Container>
      {loading && (
        <LoadingSkeleton $height={height}>
          <span className="anticon anticon-loading" />
          {t('加载图表中...')}
        </LoadingSkeleton>
      )}
      {iframeSrc && (
        <StyledIframe
          $height={height}
          src={iframeSrc}
          title={`Chart: ${vizType}`}
          onLoad={() => setLoading(false)}
          style={loading ? { position: 'absolute', opacity: 0 } : undefined}
        />
      )}
    </Container>
  );
}
