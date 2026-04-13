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

import { Fragment, ReactNode } from 'react';
import { styled } from '@superset-ui/core';

interface AiMarkdownProps {
  content: string;
}

interface TextBlock {
  type: 'text';
  content: string;
}

interface CodeBlock {
  type: 'code';
  content: string;
  language: string;
}

type MarkdownBlock = TextBlock | CodeBlock;

const MarkdownRoot = styled.div`
  max-width: 100%;
  min-width: 0;
  overflow-wrap: anywhere;

  p {
    margin: 0 0 8px;
  }

  p:last-child,
  ul:last-child,
  ol:last-child,
  pre:last-child,
  .ai-markdown-table-scroll:last-child {
    margin-bottom: 0;
  }

  h1,
  h2,
  h3,
  h4 {
    margin: 12px 0 6px;
    font-size: 14px;
    font-weight: ${({ theme }) => theme.fontWeightStrong};
  }

  ul,
  ol {
    margin: 0 0 8px;
    padding-left: 20px;
  }

  li {
    margin: 2px 0;
  }

  code {
    padding: 1px 4px;
    border-radius: 4px;
    background: ${({ theme }) => theme.colorFillSecondary};
    font-family:
      'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
    font-size: 12px;
  }

  pre {
    max-width: 100%;
    margin: 0 0 8px;
    padding: 8px 10px;
    border-radius: 4px;
    background: ${({ theme }) => theme.colorBgContainer};
    overflow-x: auto;
    white-space: pre;
  }

  pre code {
    padding: 0;
    background: transparent;
    white-space: pre;
  }

  .ai-markdown-table-scroll {
    display: block;
    max-width: 100%;
    margin: 0 0 8px;
    overflow-x: auto;
  }

  table {
    width: max-content;
    min-width: 100%;
    border-collapse: collapse;
    background: ${({ theme }) => theme.colorBgContainer};
    font-size: 12px;
  }

  th,
  td {
    padding: 6px 8px;
    border: 1px solid ${({ theme }) => theme.colorBorderSecondary};
    text-align: left;
    white-space: nowrap;
  }

  th {
    font-weight: ${({ theme }) => theme.fontWeightStrong};
    background: ${({ theme }) => theme.colorFillQuaternary};
  }
`;

function splitCodeBlocks(content: string): MarkdownBlock[] {
  const blocks: MarkdownBlock[] = [];
  const codeBlockPattern = /```([a-zA-Z0-9_-]*)\s*\n([\s\S]*?)```/g;
  let lastIndex = 0;
  let match = codeBlockPattern.exec(content);

  while (match) {
    if (match.index > lastIndex) {
      blocks.push({
        type: 'text',
        content: content.slice(lastIndex, match.index),
      });
    }
    blocks.push({
      type: 'code',
      language: match[1],
      content: match[2].trimEnd(),
    });
    lastIndex = match.index + match[0].length;
    match = codeBlockPattern.exec(content);
  }

  if (lastIndex < content.length) {
    blocks.push({ type: 'text', content: content.slice(lastIndex) });
  }

  return blocks;
}

function isTableSeparator(line: string): boolean {
  const cells = splitTableRow(line);
  return (
    cells.length > 1 && cells.every(cell => /^:?-{3,}:?$/.test(cell.trim()))
  );
}

function splitTableRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map(cell => cell.trim());
}

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  // Match inline code, bold, and markdown links [text](url)
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g;
  let lastIndex = 0;
  let match = pattern.exec(text);

  while (match) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }

    const token = match[0];
    const key = `${keyPrefix}-${match.index}`;
    if (token.startsWith('`')) {
      nodes.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith('**')) {
      nodes.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith('[')) {
      // Markdown link: [text](url)
      const linkMatch = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      if (linkMatch) {
        nodes.push(
          <a
            key={key}
            href={linkMatch[2]}
            target="_blank"
            rel="noopener noreferrer"
          >
            {linkMatch[1]}
          </a>,
        );
      }
    }

    lastIndex = match.index + token.length;
    match = pattern.exec(text);
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }

  return nodes;
}

function renderTable(lines: string[], key: string): ReactNode {
  const headers = splitTableRow(lines[0]);
  const rows = lines.slice(2).map(splitTableRow);

  return (
    <div className="ai-markdown-table-scroll" key={key}>
      <table>
        <thead>
          <tr>
            {headers.map((header, cellIndex) => (
              <th key={`${key}-h-${cellIndex}`}>
                {renderInline(header, `${key}-h-${cellIndex}`)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={`${key}-r-${rowIndex}`}>
              {row.map((cell, cellIndex) => (
                <td key={`${key}-r-${rowIndex}-${cellIndex}`}>
                  {renderInline(cell, `${key}-r-${rowIndex}-${cellIndex}`)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function renderTextBlock(content: string, blockIndex: number): ReactNode[] {
  const nodes: ReactNode[] = [];
  const lines = content.replace(/\r\n/g, '\n').split('\n');
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    const key = `text-${blockIndex}-${index}`;

    if (!line.trim()) {
      index += 1;
      continue;
    }

    if (
      index + 1 < lines.length &&
      line.includes('|') &&
      isTableSeparator(lines[index + 1])
    ) {
      const tableLines = [line, lines[index + 1]];
      index += 2;
      while (index < lines.length && lines[index].includes('|')) {
        tableLines.push(lines[index]);
        index += 1;
      }
      nodes.push(renderTable(tableLines, key));
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const HeadingTag = `h${heading[1].length}` as keyof JSX.IntrinsicElements;
      nodes.push(
        <HeadingTag key={key}>{renderInline(heading[2], key)}</HeadingTag>,
      );
      index += 1;
      continue;
    }

    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*[-*]\s+/, ''));
        index += 1;
      }
      nodes.push(
        <ul key={key}>
          {items.map((item, itemIndex) => (
            <li key={`${key}-${itemIndex}`}>
              {renderInline(item, `${key}-${itemIndex}`)}
            </li>
          ))}
        </ul>,
      );
      continue;
    }

    const paragraphLines = [line];
    index += 1;
    while (
      index < lines.length &&
      lines[index].trim() &&
      !/^(#{1,4})\s+/.test(lines[index]) &&
      !/^\s*[-*]\s+/.test(lines[index]) &&
      !(
        index + 1 < lines.length &&
        lines[index].includes('|') &&
        isTableSeparator(lines[index + 1])
      )
    ) {
      paragraphLines.push(lines[index]);
      index += 1;
    }
    nodes.push(
      <p key={key}>
        {paragraphLines.map((paragraphLine, lineIndex) => (
          <Fragment key={`${key}-${lineIndex}`}>
            {lineIndex > 0 && <br />}
            {renderInline(paragraphLine, `${key}-${lineIndex}`)}
          </Fragment>
        ))}
      </p>,
    );
  }

  return nodes;
}

export function AiMarkdown({ content }: AiMarkdownProps) {
  const blocks = splitCodeBlocks(content);

  return (
    <MarkdownRoot>
      {blocks.map((block, blockIndex) =>
        block.type === 'code' ? (
          <pre key={`code-${blockIndex}`}>
            <code>{block.content}</code>
          </pre>
        ) : (
          <Fragment key={`text-${blockIndex}`}>
            {renderTextBlock(block.content, blockIndex)}
          </Fragment>
        ),
      )}
    </MarkdownRoot>
  );
}
