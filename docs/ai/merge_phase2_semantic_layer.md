# Merge Phase 2: Semantic Layer Service Integration

> Status: PLANNED
> Estimated: 8-9 weeks
> Prerequisite: Phase 1 complete

## Goal

Replace YAML `metric_catalog.yaml` with SuperSonic Headless BI semantic layer, providing metric/dimension management UI and API-driven semantic queries.

## Architecture

```
Superset AI Agent ──HTTP──▶ SuperSonic Java Service
                                │
                    ┌───────────┼───────────┐
                    │           │           │
               Metric API  Dimension API  Chat Parse API
               /semantic/metric  /semantic/dimension  /chat/query
```

## Corrected API Endpoints

| Function | Actual Endpoint | Auth |
|----------|----------------|------|
| List metrics | `GET /api/semantic/metric/getMetricList/{modelId}` | JWT |
| Query metrics | `POST /api/semantic/metric/queryMetric` | JWT |
| Get drill-down dims | `GET /api/semantic/metric/getDrillDownDimension` | JWT |
| List dimensions | `GET /api/semantic/dimension/getDimensionList/{modelId}` | JWT |
| Parse NL query | `POST /api/chat/query/parse` | JWT |
| Execute query | `POST /api/chat/query/execute` | JWT |
| Health check | `GET /actuator/health` (Spring Boot Actuator) | — |

## Key Risks

1. **Authentication**: SuperSonic uses JWT + App-Key header. Need token management or disable auth for dev.
2. **Model ID mapping**: API requires `modelId`, need config mapping Superset dataset → SuperSonic model.
3. **Dual database**: SuperSonic has its own MySQL/PostgreSQL + pgvector.
4. **iframe SSO**: Embedding semantic UI requires shared auth.

## Tasks

### 2.1 SuperSonic Deployment (3 days)
- Add `supersonic` service to `docker-compose.yml`
- Configure JWT / disable auth for dev
- Verify health endpoint

### 2.2 Python Client (2 weeks)
- `superset/ai/semantic/supersonic_client.py`
- `superset/ai/semantic/model_mapping.py` (dataset ↔ model mapping)
- JWT token management
- Fallback to YAML on error

### 2.3 Dual-Path Metric Resolution (5 days)
- Modify `metric_catalog.py` with SuperSonic-first, YAML-fallback
- Inject semantic context into `plan_query` prompt

### 2.4 UI Embedding (2 weeks)
- iframe embed of SuperSonic semantic management
- Config API to pass `SUPERSONIC_BASE_URL` to frontend
- Token passthrough for authenticated embed

## Configuration

```python
# superset/config.py
SUPERSONIC_ENABLED = False
SUPERSONIC_BASE_URL = "http://localhost:9080/api"
SUPERSONIC_TIMEOUT = 5
SUPERSONIC_DOMAIN_ID = None  # None = all domains
SUPERSONIC_AUTH_ENABLED = False
SUPERSONIC_APP_KEY = "supersonic"
SUPERSONIC_APP_SECRET = ""
```

## Acceptance Criteria

- [ ] `SUPERSONIC_ENABLED=True` → metrics from API, not YAML
- [ ] "查 GMV" uses SuperSonic-defined SQL expression
- [ ] Semantic management UI accessible via iframe
- [ ] `SUPERSONIC_ENABLED=False` → fallback to YAML, no regression
