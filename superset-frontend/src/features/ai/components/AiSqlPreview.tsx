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

import { styled, t } from '@superset-ui/core';

interface AiSqlPreviewProps {
  sql: string;
  onCopyToEditor?: (sql: string) => void;
}

const Container = styled.div`
  margin-top: 4px;
  border: 1px solid ${({ theme }) => theme.colorBorderSecondary};
  border-radius: 4px;
  overflow: hidden;
`;

const SqlBlock = styled.pre`
  padding: 8px 12px;
  margin: 0;
  background: ${({ theme }) => theme.colorBgContainer};
  color: ${({ theme }) => theme.colorText};
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 12px;
  line-height: 1.5;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
`;

const Actions = styled.div`
  padding: 4px 8px;
  border-top: 1px solid ${({ theme }) => theme.colorBorderSecondary};
  text-align: right;
`;

const CopyButton = styled.button`
  background: none;
  border: 1px solid ${({ theme }) => theme.colorPrimary};
  color: ${({ theme }) => theme.colorPrimary};
  padding: 2px 8px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 11px;

  &:hover {
    background: ${({ theme }) => theme.colorPrimaryBg};
  }
`;

function extractSqlFromMarkdown(text: string): string | null {
  // Extract SQL from markdown code blocks: ```sql ... ```
  const match = text.match(/```sql\s*\n([\s\S]*?)```/i);
  return match ? match[1].trim() : null;
}

export function AiSqlPreview({ sql, onCopyToEditor }: AiSqlPreviewProps) {
  const extractedSql = extractSqlFromMarkdown(sql);
  if (!extractedSql) return null;

  return (
    <Container>
      <SqlBlock>{extractedSql}</SqlBlock>
      {onCopyToEditor && (
        <Actions>
          <CopyButton onClick={() => onCopyToEditor(extractedSql)}>
            {t('Copy to SQL Lab')}
          </CopyButton>
        </Actions>
      )}
    </Container>
  );
}
