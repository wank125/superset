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

import { styled } from '@superset-ui/core';
import { AiMarkdown } from './AiMarkdown';

interface AiStreamingTextProps {
  text: string;
}

const StreamingContainer = styled.div`
  padding: 0;
  border-radius: 8px;
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
  margin-bottom: 8px;
  background: ${({ theme }) => theme.colorBgLayout};
  color: ${({ theme }) => theme.colorText};
  word-break: break-word;
  font-size: 13px;
  line-height: 1.5;
  min-height: 20px;

  > div > p,
  > div > ul,
  > div > ol,
  > div > h1,
  > div > h2,
  > div > h3,
  > div > h4 {
    padding-left: 12px;
    padding-right: 12px;
  }

  &::after {
    content: '▌';
    animation: blink 1s infinite;
  }

  @keyframes blink {
    0%,
    50% {
      opacity: 1;
    }
    51%,
    100% {
      opacity: 0;
    }
  }
`;

export function AiStreamingText({ text }: AiStreamingTextProps) {
  if (!text) return null;
  return (
    <StreamingContainer>
      <AiMarkdown content={text} />
    </StreamingContainer>
  );
}
