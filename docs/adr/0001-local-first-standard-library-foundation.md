# ADR 0001: Local-first standard-library foundation

- Status: Accepted
- Date: 2026-07-26

## Context

The prompt roadmap prefers Python 3.12, FastAPI, Pydantic, and an ORM. The
working prototype already runs on Python 3.11 using SQLite and the standard
library. It has no network API or provider payloads that currently require those
dependencies.

## Decision

Support Python 3.11+ and retain the dependency-light implementation through the
foundation milestone. Add typed configuration with dataclasses. Introduce web,
validation, ORM, and provider dependencies only with the first feature that
measurably requires them.

Do not create empty domain packages. Document dependency direction now and add
each package with its first tested behavior.

## Consequences

Local setup remains small and reproducible. Some future migrations may require
adapters when an API or richer schema arrives, but core interfaces remain
provider- and framework-independent.

