# Merge Phase 3: Self-Built Capabilities

> Status: IN PROGRESS — Phase 21 Alert Center implemented
> Estimated: 11+ weeks
> Prerequisite: Phase 1 complete (Phase 2 independent)

## Overview

These modules exist in neither SuperSonic nor current Superset AI Agent. Must be built from scratch.

## Phase 20: Anomaly Detection Engine (4 weeks)

### Architecture

```
superset/ai/anomaly/
├── __init__.py
├── detector.py          # Z-score + IQR + YoY deviation
├── attribution.py       # Multi-dimension contribution decomposition
├── agent/
│   └── anomaly_agent.py # LangGraph node: detect → attribute → report
└── tools/
    ├── detect_anomaly.py
    └── attribute_cause.py
```

### V1 Algorithm

- Z-score (threshold 2.5) for point anomalies
- IQR for outlier detection
- Week-over-week deviation for business context
- Seasonal awareness via day-of-week grouping

### V2 (Future)

- STL decomposition for seasonality
- Prophet integration for forecasting
- Context-aware (holidays, promotions)

### Intent Router Extension

Add `anomaly` intent type, triggers: `["为什么", "原因", "异常", "下降", "上升", "波动", "归因"]`

## Phase 21: Proactive Alert Center (3 weeks)

> NOTE: Extend existing `superset/reports/` module, don't rebuild.

### Architecture

```
superset/ai/alert/           # NEW — AI alert rule generation
├── rules.py                 # AI-generated alert rules
├── rule_engine.py           # Bridge to superset/reports/ infrastructure
└── models/
    └── ai_alert_rule.py     # SQLAlchemy model

superset/reports/            # EXISTING — extend
├── commands/alert.py        # Add: anomaly-triggered alerts
└── notifications/           # Add: DingTalk/WeChat webhook
    ├── dingtalk.py
    └── wechat_work.py
```

### Why extend instead of rebuild

- `superset/reports/` already has: `ReportSchedule` model, cron execution, email/Slack notification
- Only need: AI rule generation + DingTalk/WeChat channels

### Frontend

```
superset-frontend/src/features/aiAlert/
├── AlertRuleList.tsx
├── AlertRuleForm.tsx
└── AlertHistory.tsx
```

## Phase 22: Auto Report Generation (4 weeks)

### Architecture

```
superset/ai/report/
├── templates/           # Jinja2 templates (daily/weekly/monthly)
│   ├── daily.md.j2
│   ├── weekly.md.j2
│   └── monthly.md.j2
├── generator.py         # Data pull + LLM summary + chart render
├── exporter.py          # Markdown → HTML → PDF (weasyprint)
└── scheduler.py         # Celery Beat integration

superset-frontend/src/features/aiReport/
├── ReportList.tsx
├── ReportPreview.tsx
└── ReportTemplateForm.tsx
```

### V1 Export: Markdown + HTML only

- Skip PDF (weasyprint needs system deps: cairo, pango)
- Skip Word (python-docx layout complexity)
- Focus on Markdown + clean HTML with embedded charts

### V2 Export: PDF + Word

- WeasyPrint for PDF
- python-docx for Word

## Revised Priority Order

```
Phase 21 (Alert) → Phase 20 (Anomaly) → Phase 22 (Report)
```

Rationale: Alert extends existing infrastructure (low cost), anomaly adds detection capability to alerts, report depends on both.

## Total Estimate

| Phase | Weeks | Dependencies |
|-------|-------|-------------|
| Phase 20 Anomaly | 4 | Phase 1 |
| Phase 21 Alert | 3 | Phase 1 + existing reports/ |
| Phase 22 Report | 4 | Phase 20 + 21 |
| **Total** | **11** | |
