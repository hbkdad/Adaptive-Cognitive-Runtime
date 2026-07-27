# ACR Control Center

Read-only, local operations dashboard for Adaptive Cognitive Runtime. It is a
separate React application over the sanitized FastAPI dashboard contract; a UI
failure cannot stop or mutate the Python runtime.

## Run locally

From the repository root:

```powershell
pip install -e ".[api]"
python -m acr_runtime.cli --db .acr/acr.db serve --port 8011
```

In a second PowerShell window:

```powershell
cd apps/control-center
npm ci
npm run dev -- --host 127.0.0.1 --port 4173
```

Open `http://127.0.0.1:4173/overview`. The Vite development proxy sends only
`/dashboard/*` reads to `http://127.0.0.1:8011`. Override that target for local
development with `ACR_API_ORIGIN`.

## Quality checks

```powershell
npm run lint
npm run typecheck
npm test
npm run build
npm audit
```

The production bundle contains no mock data, API token, analytics, remote
fonts, or write requests. Recharts is lazy-loaded as a separate chart chunk.

## Evidence semantics

Metrics are computed on the server. The UI displays the returned numerator,
sample count, unit, and availability state. It never converts unavailable
evidence into zero. Memory and token benchmark history remain explicitly
unavailable until those reports have a retained runtime representation.
