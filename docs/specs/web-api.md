# Web API

Prompt 47 adds a loopback-first FastAPI application with generated OpenAPI,
Swagger UI, and ReDoc.

```powershell
pip install -e ".[api]"
python -m acr_runtime.cli --db .acr/acr.db serve
```

The default bind is `127.0.0.1:8000`. Binding to a non-loopback IP is rejected
unless `ACR_API_TOKEN` is set; clients then send that value in
`X-ACR-Token`. CORS is not enabled. Streaming is deliberately deferred until a
bounded event/replay contract exists.

## Endpoints

- `POST /tasks` creates a planned task record; it does not execute a model.
- `GET /tasks/{id}` returns one task.
- `GET /memory?scope=...` returns current public/internal memory in one exact
  scope.
- `POST /memory/search` performs scoped FTS search.
- `GET /skills` and `POST /skills/search` expose registry metadata, not
  instructions.
- `GET /agents` returns bounded agent-spec summaries.
- `GET /models` returns model routing profiles.
- `GET /telemetry` returns aggregates without raw event payloads.
- `GET /health` runs SQLite quick/schema/FTS checks.
- `/memory-inspector/v1/*` provides Prompt 50's exact-scope inspector. Reads
  remain public/internal only. Writes additionally require a server-bound
  operator and an exact `memory.write` capability; see
  `docs/specs/memory-inspector.md`.
- `/skill-lab/v1/*` provides Prompt 51's bounded exact-version workbench.
  Reads expose sanitized registry and retained evidence. Lifecycle, rollback,
  and benchmark writes require a server-bound operator, a one-use idempotency
  key, optimistic revision checks, and exact target grants; see
  `docs/specs/skill-lab.md`.

Personal, confidential, and secret memories are excluded from both memory
endpoints even when the API token is valid. Request models reject unknown
fields and bound text, pagination, budgets, and ports.

Every request opens and closes an independent `AdaptiveRuntime`/SQLite
connection. This avoids sharing Python's connection object across concurrent
request threads. SQLite uses a bounded five-second busy timeout, and lock
failures return a sanitized `503` with `Retry-After`. The app lifespan performs
a schema readiness check before accepting traffic.

## Evidence basis

- FastAPI documents `yield` dependencies for request-scoped resources and
  guaranteed cleanup:
  <https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/>
- FastAPI recommends lifespan context managers for application readiness and
  shutdown:
  <https://fastapi.tiangolo.com/advanced/events/>
- FastAPI response models validate, document, serialize, and filter public
  output:
  <https://fastapi.tiangolo.com/tutorial/response-model/>
- Uvicorn defaults to `127.0.0.1`; `0.0.0.0` exposes the application on the
  local network:
  <https://www.uvicorn.org/settings/>
