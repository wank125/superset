# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""HTTP E2E smoke tests for the AI Agent API.

This file is intentionally a standalone script instead of a pytest module.
It exercises a running Superset deployment through the public HTTP API:
login, submit chat requests, poll AI stream events, and evaluate the final
event stream for the expected behavior.
"""

from __future__ import annotations

import argparse
import json  # noqa: TID251 - standalone E2E logs need ensure_ascii support
import re
import sys
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

TERMINAL_EVENTS = {"done", "error"}
DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_POLL_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True)
class Config:
    """Runtime configuration for the E2E test script."""

    base_url: str
    username: str
    password: str
    database_id: int
    schema_name: str | None
    timeout: float
    poll_interval: float
    output_dir: Path
    cases: set[str] | None = None


@dataclass(frozen=True)
class Event:
    """Normalized event returned by ``GET /api/v1/ai/events/``."""

    id: str
    type: str
    data: dict[str, Any]


@dataclass
class TestResult:
    """Result for one E2E test case."""

    name: str
    agent_type: str | None
    message: str
    passed: bool
    latency_seconds: float
    detail: str
    events: list[Event] = field(default_factory=list)
    status_code: int | None = None
    response_body: Any = None


@dataclass(frozen=True)
class TestCase:
    """An executable AI Agent E2E test case."""

    name: str
    agent_type: str | None
    message: str
    evaluator: Callable[[TestResult], tuple[bool, str]]
    expect_status: int = 200
    database_id: int | None = None


class Colors:
    """ANSI colors used for console reporting."""

    green = "\033[32m"
    red = "\033[31m"
    yellow = "\033[33m"
    cyan = "\033[36m"
    bold = "\033[1m"
    reset = "\033[0m"


class AIAgentTestClient:
    """Small HTTP client for the AI Agent API."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def login(self) -> None:
        """Log in to Superset and configure CSRF headers when available."""
        login_url = self._url("/login/")
        login_get = self.session.get(login_url, timeout=30)
        login_get.raise_for_status()

        csrf_token = _extract_csrf_token(login_get.text)
        payload = {
            "username": self.config.username,
            "password": self.config.password,
        }
        if csrf_token:
            payload["csrf_token"] = csrf_token

        login_post = self.session.post(login_url, data=payload, timeout=30)
        login_post.raise_for_status()

        api_csrf = self._fetch_api_csrf_token()
        if api_csrf:
            self.session.headers.update({"X-CSRFToken": api_csrf})

    def run_case(self, case: TestCase) -> TestResult:
        """Run one chat test case and evaluate its event stream."""
        start = time.monotonic()
        status_code: int | None = None
        response_body: Any = None
        events: list[Event] = []

        try:
            response = self._post_chat(case)
            status_code = response.status_code
            response_body = _safe_json(response)
            latency = time.monotonic() - start

            if case.expect_status != 200:
                passed = status_code == case.expect_status
                detail = (
                    f"{status_code} returned"
                    if passed
                    else f"expected {case.expect_status}, got {status_code}"
                )
                return TestResult(
                    name=case.name,
                    agent_type=case.agent_type,
                    message=case.message,
                    passed=passed,
                    latency_seconds=latency,
                    detail=detail,
                    status_code=status_code,
                    response_body=response_body,
                )

            if status_code != 200:
                return TestResult(
                    name=case.name,
                    agent_type=case.agent_type,
                    message=case.message,
                    passed=False,
                    latency_seconds=latency,
                    detail=f"chat API returned {status_code}",
                    status_code=status_code,
                    response_body=response_body,
                )

            channel_id = response_body.get("channel_id")
            if not channel_id:
                return TestResult(
                    name=case.name,
                    agent_type=case.agent_type,
                    message=case.message,
                    passed=False,
                    latency_seconds=latency,
                    detail="chat API did not return channel_id",
                    status_code=status_code,
                    response_body=response_body,
                )

            events = self.poll_events(channel_id)
            latency = time.monotonic() - start
            result = TestResult(
                name=case.name,
                agent_type=case.agent_type,
                message=case.message,
                passed=False,
                latency_seconds=latency,
                detail="not evaluated",
                events=events,
                status_code=status_code,
                response_body=response_body,
            )
            result.passed, result.detail = case.evaluator(result)
            return result
        except requests.RequestException as exc:
            return TestResult(
                name=case.name,
                agent_type=case.agent_type,
                message=case.message,
                passed=False,
                latency_seconds=time.monotonic() - start,
                detail=f"HTTP error: {exc}",
                events=events,
                status_code=status_code,
                response_body=response_body,
            )

    def poll_events(self, channel_id: str) -> list[Event]:
        """Poll the events endpoint until a terminal event or timeout."""
        last_id = "0"
        deadline = time.monotonic() + self.config.timeout
        events: list[Event] = []

        while time.monotonic() < deadline:
            response = self.session.get(
                self._url("/api/v1/ai/events/"),
                params={"channel_id": channel_id, "last_id": last_id},
                timeout=30,
            )
            response.raise_for_status()
            body = response.json()
            last_id = body.get("last_id") or last_id

            for raw_event in body.get("events", []):
                event = Event(
                    id=str(raw_event.get("id", "")),
                    type=str(raw_event.get("type", "")),
                    data=raw_event.get("data") or {},
                )
                events.append(event)
                if event.type in TERMINAL_EVENTS:
                    return events

            time.sleep(self.config.poll_interval)

        events.append(
            Event(
                id="timeout",
                type="error",
                data={
                    "message": (
                        f"Timed out after {self.config.timeout:.0f}s "
                        f"waiting for channel {channel_id}"
                    ),
                },
            )
        )
        return events

    def _post_chat(self, case: TestCase) -> requests.Response:
        """Post one chat request to the AI Agent API."""
        payload: dict[str, Any] = {
            "message": case.message,
            "database_id": (
                case.database_id
                if case.database_id is not None
                else self.config.database_id
            ),
            "session_id": f"e2e-{case.name}-{uuid.uuid4().hex[:8]}",
        }
        if case.agent_type is not None:
            payload["agent_type"] = case.agent_type
        if self.config.schema_name:
            payload["schema_name"] = self.config.schema_name

        return self.session.post(
            self._url("/api/v1/ai/chat/"),
            json=payload,
            timeout=30,
        )

    def _fetch_api_csrf_token(self) -> str | None:
        """Fetch Superset's API CSRF token when the endpoint is available."""
        response = self.session.get(
            self._url("/api/v1/security/csrf_token/"),
            timeout=30,
        )
        if response.status_code >= 400:
            return None
        body = _safe_json(response)
        result = body.get("result")
        return result if isinstance(result, str) else None

    def _url(self, path: str) -> str:
        """Return an absolute URL for a Superset path."""
        return urljoin(self.config.base_url.rstrip("/") + "/", path.lstrip("/"))


def build_test_cases() -> list[TestCase]:
    """Return the default AI Agent E2E test cases."""
    return [
        TestCase(
            name="nl2sql_simple",
            agent_type="nl2sql",
            message="查询birth_names表前10行数据",
            evaluator=evaluate_nl2sql_simple,
        ),
        TestCase(
            name="nl2sql_aggregation",
            agent_type="nl2sql",
            message="统计birth_names每年男孩和女孩的总数",
            evaluator=evaluate_nl2sql_aggregation,
        ),
        TestCase(
            name="nl2sql_error",
            agent_type="nl2sql",
            message="人生的意义是什么？",
            evaluator=evaluate_nl2sql_error,
        ),
        TestCase(
            name="chart_bar",
            agent_type="chart",
            message="用柱状图展示birth_names各性别的出生总数",
            evaluator=evaluate_chart_bar,
        ),
        TestCase(
            name="chart_trend",
            agent_type="chart",
            message="用折线图展示birth_names出生人数的年度趋势",
            evaluator=evaluate_chart_trend,
        ),
        TestCase(
            name="chart_pie",
            agent_type="chart",
            message="用饼图展示birth_names按性别的比例分布",
            evaluator=evaluate_chart_pie,
        ),
        TestCase(
            name="dashboard_multi",
            agent_type="dashboard",
            message=(
                "创建birth_names仪表板：1)性别分布饼图 "
                "2)年度趋势折线图 3)总记录数大数字"
            ),
            evaluator=evaluate_dashboard_multi,
        ),
        TestCase(
            name="debug_fix",
            agent_type="debug",
            message=(
                "SQL报错：column 'gender_typ' does not exist。原SQL："
                "SELECT gender_typ, COUNT(*) FROM birth_names "
                "GROUP BY gender_typ。请修复。"
            ),
            evaluator=evaluate_debug_fix,
        ),
        TestCase(
            name="edge_invalid_type",
            agent_type="invalid_type",
            message="test invalid agent type",
            evaluator=evaluate_status_only,
            expect_status=400,
        ),
        TestCase(
            name="edge_empty_message",
            agent_type="nl2sql",
            message="",
            evaluator=evaluate_status_only,
            expect_status=400,
        ),
    ]


def evaluate_nl2sql_simple(result: TestResult) -> tuple[bool, str]:
    """Evaluate a simple NL2SQL query."""
    if not _has_done(result.events):
        return False, _terminal_detail(result.events)
    sql_text = _sql_text(result.events)
    if "birth_names" not in sql_text.lower():
        return False, "SQL did not reference birth_names"
    if not _tool_succeeded(result.events, "execute_sql"):
        return False, "execute_sql did not succeed"
    return True, "SQL OK, execute OK"


def evaluate_nl2sql_aggregation(result: TestResult) -> tuple[bool, str]:
    """Evaluate an aggregate NL2SQL query."""
    if not _has_done(result.events):
        return False, _terminal_detail(result.events)
    sql_text = _sql_text(result.events)
    if "group by" not in sql_text.lower():
        return False, "GROUP BY missing"
    if "sum" not in sql_text.lower():
        return False, "SUM missing"
    if not _tool_succeeded(result.events, "execute_sql"):
        return False, "execute_sql did not succeed"
    return True, "GROUP BY + SUM OK"


def evaluate_nl2sql_error(result: TestResult) -> tuple[bool, str]:
    """Evaluate graceful handling of a non-SQL request in SQL mode."""
    if not _has_done(result.events):
        return False, _terminal_detail(result.events)
    sql_text = _sql_text(result.events)
    if re.search(r"\bselect\b|\bfrom\b", sql_text, flags=re.IGNORECASE):
        return False, "unexpected SQL generated for non-SQL request"
    if len(_assistant_text(result.events).strip()) < 5:
        return False, "missing textual response"
    return True, "Handled gracefully"


def evaluate_chart_bar(result: TestResult) -> tuple[bool, str]:
    """Evaluate bar chart creation."""
    chart = _first_event_data(result.events, "chart_created")
    if not chart:
        return False, "chart_created missing"
    viz_type = str(chart.get("viz_type", ""))
    if "bar" not in viz_type:
        return False, f"viz: {viz_type or 'missing'} (expected bar)"
    if not chart.get("explore_url"):
        return False, "explore_url missing"
    return True, f"viz: {viz_type}"


def evaluate_chart_trend(result: TestResult) -> tuple[bool, str]:
    """Evaluate line or timeseries trend chart creation."""
    chart = _first_event_data(result.events, "chart_created")
    if not chart:
        return False, "chart_created missing"
    viz_type = str(chart.get("viz_type", ""))
    if "timeseries" not in viz_type and "line" not in viz_type:
        return False, f"viz: {viz_type or 'missing'} (expected timeseries)"
    sql_text = _sql_text(result.events)
    if "group by" not in sql_text.lower():
        return False, "sql_generated missing GROUP BY"
    return True, f"viz: {viz_type}"


def evaluate_chart_pie(result: TestResult) -> tuple[bool, str]:
    """Evaluate pie chart creation."""
    chart = _first_event_data(result.events, "chart_created")
    if not chart:
        return False, "chart_created missing"
    viz_type = str(chart.get("viz_type", ""))
    if viz_type != "pie":
        return False, f"viz: {viz_type or 'missing'} (expected pie)"
    if not _has_event(result.events, "data_analyzed"):
        return False, "data_analyzed missing"
    return True, "viz: pie"


def evaluate_dashboard_multi(result: TestResult) -> tuple[bool, str]:
    """Evaluate multi-chart dashboard creation."""
    charts = [event.data for event in result.events if event.type == "chart_created"]
    dashboard = _first_event_data(result.events, "dashboard_created")
    if len(charts) < 2:
        return False, f"Charts: {len(charts)} (expected >= 2)"
    if not dashboard:
        return False, "dashboard_created missing"
    chart_count = dashboard.get("chart_count")
    if isinstance(chart_count, int) and chart_count < 2:
        return False, f"dashboard chart_count: {chart_count}"
    return True, f"Charts: {len(charts)}, Dashboard OK"


def evaluate_debug_fix(result: TestResult) -> tuple[bool, str]:
    """Evaluate SQL debug repair mode."""
    if not _has_done(result.events):
        return False, _terminal_detail(result.events)
    if not _tool_called(result.events, "get_schema"):
        return False, "get_schema was not called"
    if not _tool_succeeded(result.events, "execute_sql"):
        return False, "execute_sql did not succeed"
    text = _assistant_text(result.events)
    all_text = f"{text}\n{_events_text(result.events)}"
    if "gender_typ" not in all_text and "gender" not in all_text:
        return False, "fix explanation missing gender column context"
    return True, "Fix: gender_typ -> gender"


def evaluate_status_only(result: TestResult) -> tuple[bool, str]:
    """Placeholder evaluator for HTTP status-only cases."""
    return result.passed, result.detail


def print_report(config: Config, results: list[TestResult]) -> None:
    """Print a compact colored console report."""
    width = 77
    print("=" * width)
    print(f"  {Colors.bold}AI Agent E2E API Test Report{Colors.reset}")
    print(
        "  Base URL: "
        f"{config.base_url} | Database: {config.database_id} | "
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    print("=" * width)
    print()

    for index, result in enumerate(results, start=1):
        label = _colored_status(result.passed)
        print(
            f" {index:2d}/{len(results):<2d} "
            f"{result.name:<22s} {label}  "
            f"{result.latency_seconds:6.1f}s  {result.detail}"
        )

    print()
    print("=" * width)
    passed = sum(1 for result in results if result.passed)
    percent = round((passed / len(results)) * 100) if results else 0
    print(f"  PASS {passed}/{len(results)} ({percent}%)")
    print(f"  {_group_summary(results)}")
    print(f"  Avg latency: {_avg_latency(results):.1f}s")
    print("=" * width)


def write_json_log(config: Config, results: list[TestResult]) -> Path:
    """Write full E2E results, including event streams, to JSON."""
    config.output_dir.mkdir(parents=True, exist_ok=True)
    path = config.output_dir / (
        f"test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    payload = {
        "base_url": config.base_url,
        "database_id": config.database_id,
        "schema_name": config.schema_name,
        "timestamp": datetime.now().isoformat(),
        "summary": {
            "total": len(results),
            "passed": sum(1 for result in results if result.passed),
            "avg_latency_seconds": _avg_latency(results),
        },
        "results": [_result_to_json(result) for result in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def parse_args(argv: list[str]) -> Config:
    """Parse CLI arguments into ``Config``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8088")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="admin")
    parser.add_argument("--database-id", type=int, default=1)
    parser.add_argument("--schema-name", default=None)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tests/ai/results"),
    )
    parser.add_argument(
        "--case",
        action="append",
        dest="cases",
        help="Run only the named test case. May be supplied multiple times.",
    )
    args = parser.parse_args(argv)
    return Config(
        base_url=args.base_url,
        username=args.username,
        password=args.password,
        database_id=args.database_id,
        schema_name=args.schema_name,
        timeout=args.timeout,
        poll_interval=args.poll_interval,
        output_dir=args.output_dir,
        cases=set(args.cases) if args.cases else None,
    )


def main(argv: list[str]) -> int:
    """Run all selected AI Agent E2E API tests."""
    config = parse_args(argv)
    client = AIAgentTestClient(config)
    cases = build_test_cases()
    if config.cases:
        cases = [case for case in cases if case.name in config.cases]
        missing = config.cases - {case.name for case in cases}
        if missing:
            print(f"Unknown test case(s): {', '.join(sorted(missing))}")
            return 2

    try:
        client.login()
    except requests.RequestException as exc:
        print(f"Login failed: {exc}")
        return 2

    results = [client.run_case(case) for case in cases]
    print_report(config, results)
    log_path = write_json_log(config, results)
    print(f"\nJSON log: {log_path}")
    return 0 if all(result.passed for result in results) else 1


def _extract_csrf_token(html: str) -> str | None:
    """Extract the Flask-WTF CSRF token from the login page HTML."""
    match = re.search(
        r'name="csrf_token"[^>]*value="([^"]+)"',
        html,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else None


def _safe_json(response: requests.Response) -> dict[str, Any]:
    """Return a JSON object from an HTTP response, or an empty object."""
    try:
        body = response.json()
    except ValueError:
        return {"text": response.text[:500]}
    return body if isinstance(body, dict) else {"value": body}


def _has_done(events: Iterable[Event]) -> bool:
    """Return whether the event stream reached ``done``."""
    return any(event.type == "done" for event in events)


def _has_event(events: Iterable[Event], event_type: str) -> bool:
    """Return whether the event stream contains the given event type."""
    return any(event.type == event_type for event in events)


def _first_event_data(events: Iterable[Event], event_type: str) -> dict[str, Any]:
    """Return the first data payload for an event type."""
    for event in events:
        if event.type == event_type:
            return event.data
    return {}


def _tool_called(events: Iterable[Event], tool_name: str) -> bool:
    """Return whether the named tool was called."""
    return any(
        event.type == "tool_call" and event.data.get("tool") == tool_name
        for event in events
    )


def _tool_succeeded(events: Iterable[Event], tool_name: str) -> bool:
    """Return whether the named tool emitted a non-error result."""
    for event in events:
        if event.type != "tool_result" or event.data.get("tool") != tool_name:
            continue
        result = str(event.data.get("result", ""))
        if not result.lower().startswith("error"):
            return True
    return False


def _sql_text(events: Iterable[Event]) -> str:
    """Collect SQL snippets from generated SQL and tool call arguments."""
    snippets: list[str] = []
    for event in events:
        data = event.data
        if event.type == "sql_generated" and data.get("sql"):
            snippets.append(str(data["sql"]))
        if event.type == "tool_call":
            args = data.get("args")
            if isinstance(args, dict) and args.get("sql"):
                snippets.append(str(args["sql"]))
            elif isinstance(args, str):
                snippets.append(args)
        if event.type == "tool_result" and data.get("tool") == "execute_sql":
            snippets.append(str(data.get("result", "")))
    return "\n".join(snippets)


def _assistant_text(events: Iterable[Event]) -> str:
    """Collect assistant text chunks from the event stream."""
    return "".join(
        str(event.data.get("content", ""))
        for event in events
        if event.type in {"text_chunk", "thinking"}
    )


def _events_text(events: Iterable[Event]) -> str:
    """Serialize event payloads for broad text matching."""
    return json.dumps(
        [asdict(event) for event in events],
        ensure_ascii=False,
        sort_keys=True,
    )


def _terminal_detail(events: list[Event]) -> str:
    """Return a useful detail string for an incomplete or failed event stream."""
    for event in reversed(events):
        if event.type == "error":
            return str(event.data.get("message", "error"))
    if events:
        return f"terminal event missing; last event: {events[-1].type}"
    return "no events received"


def _colored_status(passed: bool) -> str:
    """Return a colored PASS/FAIL label."""
    if not sys.stdout.isatty():
        return "[PASS]" if passed else "[FAIL]"
    if passed:
        return f"{Colors.green}[PASS]{Colors.reset}"
    return f"{Colors.red}[FAIL]{Colors.reset}"


def _group_summary(results: list[TestResult]) -> str:
    """Return per-agent pass totals."""
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for result in results:
        group = result.agent_type or "edge"
        if result.name.startswith("edge_"):
            group = "edge"
        totals[group][1] += 1
        totals[group][0] += 1 if result.passed else 0
    order = ["nl2sql", "chart", "dashboard", "debug", "edge"]
    return "  |  ".join(
        f"{group}: {totals[group][0]}/{totals[group][1]}"
        for group in order
        if totals[group][1]
    )


def _avg_latency(results: list[TestResult]) -> float:
    """Return average latency in seconds."""
    if not results:
        return 0.0
    return sum(result.latency_seconds for result in results) / len(results)


def _result_to_json(result: TestResult) -> dict[str, Any]:
    """Convert ``TestResult`` to JSON-serializable data."""
    data = asdict(result)
    data["events"] = [asdict(event) for event in result.events]
    return data


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
