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

import { act, renderHook } from '@testing-library/react-hooks';
import { useAiChat } from '../useAiChat';
import * as aiClient from '../../api/aiClient';
import type { AiEventsResponse } from '../../types';

jest.mock('../../api/aiClient', () => ({
  sendChat: jest.fn(),
  fetchEvents: jest.fn(),
  savePreviewAsChart: jest.fn(),
}));

const mockSendChat = aiClient.sendChat as jest.MockedFunction<
  typeof aiClient.sendChat
>;
const mockFetchEvents = aiClient.fetchEvents as jest.MockedFunction<
  typeof aiClient.fetchEvents
>;

beforeEach(() => {
  jest.useFakeTimers();
  mockSendChat.mockResolvedValue({ channel_id: 'ch-1' });
});
afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

function flushTimers(ms = 500) {
  act(() => {
    jest.advanceTimersByTime(ms);
  });
}

describe('useAiChat — thinking event accumulation', () => {
  it('accumulates multiple thinking fragments into a single step', async () => {
    const poll1: AiEventsResponse = {
      events: [
        { id: '1', type: 'thinking', data: { content: '用户问的是' } },
        { id: '2', type: 'thinking', data: { content: 'birth_names表' } },
        { id: '3', type: 'thinking', data: { content: '有多少条记录' } },
      ],
      last_id: '3',
    };
    const poll2: AiEventsResponse = {
      events: [
        {
          id: '4',
          type: 'thinking',
          data: { content: '。需要用execute_sql。' },
        },
        {
          id: '5',
          type: 'tool_call',
          data: { tool: 'execute_sql', args: { sql: 'SELECT COUNT(*) FROM t' } },
        },
        { id: '6', type: 'done', data: {} },
      ],
      last_id: '6',
    };

    mockFetchEvents
      .mockResolvedValueOnce(poll1)
      .mockResolvedValueOnce(poll2);

    const { result } = renderHook(() => useAiChat(null));

    // Send message — triggers sendChat then first poll
    await act(async () => {
      result.current.sendMessage('test');
    });

    // Flush microtasks so the poll promise chain runs
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    flushTimers();

    // After first poll: exactly ONE thinking step with accumulated detail
    const steps1 = result.current.steps;
    const thinking1 = steps1.filter(s => s.type === 'thinking');
    expect(thinking1).toHaveLength(1);
    expect(thinking1[0].label).toBe('💭 思考中...');
    expect(thinking1[0].detail).toBe(
      '用户问的是birth_names表有多少条记录',
    );

    // Trigger second poll
    flushTimers();
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    const thinking2 = result.current.steps.filter(s => s.type === 'thinking');
    expect(thinking2).toHaveLength(1);
    expect(thinking2[0].detail).toBe(
      '用户问的是birth_names表有多少条记录。需要用execute_sql。',
    );
    expect(
      result.current.steps.some(s => s.label === '调用工具: execute_sql'),
    ).toBe(true);
  });

  it('resets thinking accumulator on new message', async () => {
    const poll1: AiEventsResponse = {
      events: [
        { id: '1', type: 'thinking', data: { content: '第一轮思考内容' } },
        { id: '2', type: 'done', data: {} },
      ],
      last_id: '2',
    };
    const poll2: AiEventsResponse = {
      events: [
        { id: '3', type: 'thinking', data: { content: '第二轮全新思考' } },
        { id: '4', type: 'done', data: {} },
      ],
      last_id: '4',
    };

    mockFetchEvents.mockResolvedValueOnce(poll1).mockResolvedValueOnce(poll2);

    const { result } = renderHook(() => useAiChat(null));

    // First message
    await act(async () => {
      result.current.sendMessage('msg1');
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    flushTimers();

    const thinking1 = result.current.steps.filter(s => s.type === 'thinking');
    expect(thinking1).toHaveLength(1);
    expect(thinking1[0].detail).toBe('第一轮思考内容');

    // Second message — accumulator resets
    await act(async () => {
      result.current.sendMessage('msg2');
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    flushTimers();

    const thinking2 = result.current.steps.filter(s => s.type === 'thinking');
    expect(thinking2).toHaveLength(1);
    expect(thinking2[0].detail).toBe('第二轮全新思考');
  });

  it('does not create a thinking step for empty content', async () => {
    const poll1: AiEventsResponse = {
      events: [
        { id: '1', type: 'thinking', data: { content: '' } },
        { id: '2', type: 'thinking', data: {} },
        { id: '3', type: 'done', data: {} },
      ],
      last_id: '3',
    };

    mockFetchEvents.mockResolvedValueOnce(poll1);

    const { result } = renderHook(() => useAiChat(null));

    await act(async () => {
      result.current.sendMessage('test');
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    flushTimers();

    expect(result.current.loading).toBe(false);
    expect(
      result.current.steps.filter(s => s.type === 'thinking'),
    ).toHaveLength(0);
  });
});
