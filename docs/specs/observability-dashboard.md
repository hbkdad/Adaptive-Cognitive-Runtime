# Observability dashboard

Prompt 49 adds a separate local React control center under
`apps/control-center` and a sanitized, read-only FastAPI namespace under
`/dashboard/v1`.

The operations dashboard is the primary interface. It has routes for Overview,
Tasks, Memory, Skills, Agents, Models, Tools, Context, Costs, Benchmarks, and
Security. The cinematic/3D layer remains deferred and outside the initial
bundle, following ADR 0002.

## Truth and privacy boundaries

- Charts use retained runtime IDs and server-computed metrics only.
- Task objectives, memory content, skill instructions, raw telemetry payloads,
  evidence, database paths, credential requirements, and secret references are
  never selected into dashboard projections.
- `available`, `empty`, and `unavailable` are distinct states.
- Model tokens exclude cached tokens and count instrumented input plus output
  tokens once.
- Planned tasks are excluded from the success-rate denominator.
- Skill ROI is labelled approximate and includes its sample count.
- Cost is withheld when pricing coverage cannot be established.
- Memory and token benchmark history are unavailable because their reports are
  not yet persisted in a unified history.

## API

- `GET /dashboard/v1/overview`
- `GET /dashboard/v1/tasks?limit=&cursor=`
- `GET /dashboard/v1/{memory|skills|agents|models|tools|context|costs|benchmarks|security}`
- `GET /dashboard/v1/series/{metric}`

All routes inherit the Prompt 47 token guard and request-scoped SQLite
connection. Collection limits are bounded to 1–100 and task pagination uses an
opaque cursor.

## Frontend

The Vite + React + TypeScript application uses TanStack Query for server state,
Recharts for operational charts, Tailwind's Vite build pipeline, Lucide icons,
and dependency-free responsive layout primitives. It provides:

- exact-value table fallbacks for every chart;
- loading, error, empty, and unavailable states;
- retry controls and abortable fetches;
- keyboard landmarks, skip link, focus styles, and reduced-motion behavior;
- an intentional 390 px mobile layout with an internal-scrolling table region;
- a lazy chart chunk so operational navigation loads independently.

The interface polls every 30 seconds. WebSocket delivery remains deferred until
bounded cursor replay is implemented.

Prompt 51 replaces the generic Skills projection at `/skills` with the Skill
Lab. Its detail and comparison views expose instructions and generated changes
as inert text, while its writes remain behind the separate governed API
contract documented in `skill-lab.md`. The API token is held only in component
memory, and the browser never automatically retries a write.

## Research basis

- React recommends a framework or a build tool such as Vite for new apps:
  <https://react.dev/learn/creating-a-react-app>
- Vite provides the official React TypeScript scaffold and modern production
  build target:
  <https://vite.dev/guide/>
- Tailwind v4 provides a first-party Vite plugin and automatic source
  detection:
  <https://tailwindcss.com/blog/tailwindcss-v4>
- Recharts documents its SPA installation and accessible React chart
  primitives:
  <https://recharts.github.io/en-US/guide/installation/>
