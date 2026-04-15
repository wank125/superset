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

interface PeriodCompareItemProps {
  title: string;
  value: string;
}

const Wrapper = styled.div`
  display: flex;
  flex-direction: column;
  gap: 4px;
`;

const Title = styled.div`
  font-size: 12px;
  color: ${({ theme }) => theme.colorTextSecondary};
`;

const Value = styled.div<{ $positive?: boolean }>`
  font-size: 13px;
  font-weight: ${({ theme }) => theme.fontWeightStrong};
  color: ${({ theme, $positive }) =>
    $positive ? '#fc6772' : '#2dca93'};
  display: flex;
  align-items: center;
  gap: 4px;
`;

const Arrow = styled.span`
  font-size: 10px;
`;

/**
 * Displays a single period-over-period comparison metric.
 * Positive change → red (increase), negative → green (decrease).
 * Convention follows Chinese BI norms: 涨红跌绿.
 */
export function PeriodCompareItem({ title, value }: PeriodCompareItemProps) {
  const numVal = parseFloat(value);
  const isPositive = Number.isNaN(numVal) ? true : numVal >= 0;

  return (
    <Wrapper>
      <Title>{title}</Title>
      <Value $positive={isPositive}>
        <Arrow>{isPositive ? '▲' : '▼'}</Arrow>
        {value}
      </Value>
    </Wrapper>
  );
}
