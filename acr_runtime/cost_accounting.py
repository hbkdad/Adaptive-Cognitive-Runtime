from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping
from urllib.parse import urlsplit

from .autonomous_improvement import digest
from .memory_scope import MemoryScopeKind, MemoryScopeRegistry
from .secret_management import assert_secret_free


IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
RATE_METERS = frozenset(
    {
        "uncached_input_token",
        "cache_read_token",
        "cache_write_token",
        "output_token",
        "tool_call",
    }
)
MAX_SQLITE_INTEGER = 2**63 - 1
SUPPORTED_CURRENCIES_V1 = frozenset({"CAD", "USD"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value: str, name: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _identifier(value: str, name: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} must be a bounded opaque identifier")
    assert_secret_free(value, name)
    return value


def _integer(
    value: object,
    name: str,
    *,
    minimum: int = 0,
    maximum: int = MAX_SQLITE_INTEGER,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ValueError(
            f"{name} must be an integer between {minimum} and {maximum}"
        )
    return value


def _currency(value: str) -> str:
    if not isinstance(value, str) or len(value) != 3 or not value.isalpha():
        raise ValueError("currency_code must be a three-letter code")
    normalized = value.upper()
    if normalized not in SUPPORTED_CURRENCIES_V1:
        raise ValueError("schema 51 supports only CAD and USD")
    return normalized


def _ceil_ratio(numerator: int, denominator: int) -> int:
    if numerator == 0:
        return 0
    result = (numerator + denominator - 1) // denominator
    if result > MAX_SQLITE_INTEGER:
        raise ValueError("calculated amount exceeds SQLite integer capacity")
    return result


def _source_url(value: str) -> str:
    if not isinstance(value, str) or len(value) > 2_000:
        raise ValueError("source_url must be a bounded HTTPS URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "source_url must be HTTPS without credentials, query, or fragment"
        )
    assert_secret_free(value, "source_url")
    return value


@dataclass(frozen=True)
class PriceRate:
    service_kind: str
    provider: str
    sku: str
    operation: str
    meter_kind: str
    currency_code: str
    price_micros: int
    unit_size: int
    effective_from: str
    source_url: str
    source_hash: str
    effective_until: str | None = None
    id: str | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "PriceRate":
        allowed = {
            "id", "service_kind", "provider", "sku", "operation",
            "meter_kind", "currency_code", "price_micros", "unit_size",
            "effective_from", "effective_until", "source_url", "source_hash",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"Unknown price rate fields: {sorted(unknown)}")
        missing = allowed - {"id", "effective_until"} - set(payload)
        if missing:
            raise ValueError(f"Missing price rate fields: {sorted(missing)}")
        return cls(**dict(payload))  # type: ignore[arg-type]

    def validated(self) -> "PriceRate":
        if self.service_kind not in {"model", "tool"}:
            raise ValueError("service_kind must be model or tool")
        if self.meter_kind not in RATE_METERS:
            raise ValueError("unsupported meter_kind")
        if self.service_kind == "model" and self.meter_kind == "tool_call":
            raise ValueError("model rates cannot use tool_call")
        if self.service_kind == "tool" and self.meter_kind != "tool_call":
            raise ValueError("tool rates must use tool_call")
        provider = _identifier(self.provider, "provider")
        sku = _identifier(self.sku, "sku")
        operation = _identifier(self.operation, "operation")
        currency = _currency(self.currency_code)
        price = _integer(
            self.price_micros, "price_micros", maximum=1_000_000_000
        )
        unit = _integer(
            self.unit_size,
            "unit_size",
            minimum=1,
            maximum=1_000_000_000,
        )
        start = _timestamp(self.effective_from, "effective_from")
        end = (
            None
            if self.effective_until is None
            else _timestamp(self.effective_until, "effective_until")
        )
        if end is not None and end <= start:
            raise ValueError("effective_until must be after effective_from")
        source_url = _source_url(self.source_url)
        if (
            not isinstance(self.source_hash, str)
            or len(self.source_hash) != 64
            or set(self.source_hash) - set("0123456789abcdef")
        ):
            raise ValueError("source_hash must be a lowercase SHA-256 digest")
        return PriceRate(
            self.service_kind, provider, sku, operation, self.meter_kind,
            currency, price, unit, start, source_url, self.source_hash,
            end, self.id or str(uuid.uuid4()),
        )


@dataclass(frozen=True)
class LocalCostProfile:
    provider: str
    sku: str
    currency_code: str
    enabled: bool
    power_milliwatts: int
    electricity_micros_per_kwh: int
    hardware_micros_per_hour: int
    effective_from: str
    evidence_hash: str
    effective_until: str | None = None
    id: str | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "LocalCostProfile":
        allowed = {
            "id", "provider", "sku", "currency_code", "enabled",
            "power_milliwatts", "electricity_micros_per_kwh",
            "hardware_micros_per_hour", "effective_from",
            "effective_until", "evidence_hash",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"Unknown local profile fields: {sorted(unknown)}")
        missing = allowed - {"id", "effective_until"} - set(payload)
        if missing:
            raise ValueError(f"Missing local profile fields: {sorted(missing)}")
        return cls(**dict(payload))  # type: ignore[arg-type]

    def validated(self) -> "LocalCostProfile":
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be boolean")
        start = _timestamp(self.effective_from, "effective_from")
        end = (
            None if self.effective_until is None
            else _timestamp(self.effective_until, "effective_until")
        )
        if end is not None and end <= start:
            raise ValueError("effective_until must be after effective_from")
        if (
            not isinstance(self.evidence_hash, str)
            or len(self.evidence_hash) != 64
            or set(self.evidence_hash) - set("0123456789abcdef")
        ):
            raise ValueError("evidence_hash must be a lowercase SHA-256 digest")
        return LocalCostProfile(
            _identifier(self.provider, "provider"),
            _identifier(self.sku, "sku"),
            _currency(self.currency_code),
            self.enabled,
            _integer(self.power_milliwatts, "power_milliwatts"),
            _integer(
                self.electricity_micros_per_kwh,
                "electricity_micros_per_kwh",
            ),
            _integer(
                self.hardware_micros_per_hour,
                "hardware_micros_per_hour",
            ),
            start,
            self.evidence_hash,
            end,
            self.id or str(uuid.uuid4()),
        )


class CostAccounting:
    """Versioned rate catalog and append-only fixed-point cost ledger."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add_rate(self, candidate: PriceRate) -> dict[str, object]:
        rate = candidate.validated()
        overlap = self.connection.execute(
            """
            SELECT id FROM price_rates
            WHERE service_kind=? AND provider=? AND sku=? AND operation=?
              AND meter_kind=?
              AND (effective_until IS NULL OR effective_until > ?)
              AND (? IS NULL OR effective_from < ?)
            LIMIT 1
            """,
            (
                rate.service_kind, rate.provider, rate.sku, rate.operation,
                rate.meter_kind, rate.effective_from,
                rate.effective_until, rate.effective_until,
            ),
        ).fetchone()
        if overlap is not None:
            raise ValueError("price rate interval overlaps an existing rate")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO price_rates(
                    id, service_kind, provider, sku, operation, meter_kind,
                    currency_code, price_micros, unit_size, effective_from,
                    effective_until, source_url, source_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rate.id, rate.service_kind, rate.provider, rate.sku,
                    rate.operation, rate.meter_kind, rate.currency_code,
                    rate.price_micros, rate.unit_size, rate.effective_from,
                    rate.effective_until, rate.source_url, rate.source_hash,
                    _now(),
                ),
            )
        return dict(self.connection.execute(
            "SELECT * FROM price_rates WHERE id=?", (rate.id,)
        ).fetchone())

    def add_local_profile(
        self, candidate: LocalCostProfile
    ) -> dict[str, object]:
        profile = candidate.validated()
        overlap = self.connection.execute(
            """
            SELECT id FROM local_cost_profiles
            WHERE provider=? AND sku=?
              AND (effective_until IS NULL OR effective_until > ?)
              AND (? IS NULL OR effective_from < ?)
            LIMIT 1
            """,
            (
                profile.provider, profile.sku, profile.effective_from,
                profile.effective_until, profile.effective_until,
            ),
        ).fetchone()
        if overlap is not None:
            raise ValueError("local profile interval overlaps an existing profile")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO local_cost_profiles(
                    id, provider, sku, currency_code, enabled,
                    power_milliwatts, electricity_micros_per_kwh,
                    hardware_micros_per_hour, effective_from, effective_until,
                    evidence_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.id, profile.provider, profile.sku,
                    profile.currency_code, int(profile.enabled),
                    profile.power_milliwatts,
                    profile.electricity_micros_per_kwh,
                    profile.hardware_micros_per_hour, profile.effective_from,
                    profile.effective_until, profile.evidence_hash, _now(),
                ),
            )
        return dict(self.connection.execute(
            "SELECT * FROM local_cost_profiles WHERE id=?", (profile.id,)
        ).fetchone())

    def list_rates(self) -> list[dict[str, object]]:
        return [
            dict(row) for row in self.connection.execute(
                """
                SELECT * FROM price_rates
                ORDER BY provider, sku, operation, meter_kind, effective_from
                """
            ).fetchall()
        ]

    def quote_model_upper_bound(
        self,
        *,
        provider: str,
        model: str,
        operation: str,
        input_tokens: int,
        output_tokens: int,
        occurred_at: str | None = None,
    ) -> dict[str, object]:
        """Return a conservative governed-dispatch quote from the rate catalog."""
        provider = _identifier(provider, "provider")
        model = _identifier(model, "model")
        operation = _identifier(operation, "operation")
        input_count = _integer(input_tokens, "input_tokens")
        output_count = _integer(output_tokens, "output_tokens")
        when = _timestamp(occurred_at or _now(), "occurred_at")
        selected: list[sqlite3.Row] = []
        input_rates: list[sqlite3.Row] = []
        if input_count:
            for meter in (
                "uncached_input_token",
                "cache_read_token",
                "cache_write_token",
            ):
                rate = self._rate(
                    service_kind="model",
                    provider=provider,
                    sku=model,
                    operation=operation,
                    meter_kind=meter,
                    occurred_at=when,
                )
                if rate is None:
                    raise LookupError(
                        f"missing governed price rate for {meter}"
                    )
                input_rates.append(rate)
            selected.extend(input_rates)
        output_rate = None
        if output_count:
            output_rate = self._rate(
                service_kind="model",
                provider=provider,
                sku=model,
                operation=operation,
                meter_kind="output_token",
                occurred_at=when,
            )
            if output_rate is None:
                raise LookupError(
                    "missing governed price rate for output_token"
                )
            selected.append(output_rate)
        currencies = {str(row["currency_code"]) for row in selected}
        if len(currencies) > 1:
            raise ValueError("governed quote cannot mix currencies")
        input_amount = 0
        if input_rates:
            input_amount = max(
                _ceil_ratio(
                    input_count * int(row["price_micros"]),
                    int(row["unit_size"]),
                )
                for row in input_rates
            )
        output_amount = (
            0
            if output_rate is None
            else _ceil_ratio(
                output_count * int(output_rate["price_micros"]),
                int(output_rate["unit_size"]),
            )
        )
        total = input_amount + output_amount
        if total > MAX_SQLITE_INTEGER:
            raise ValueError("quote exceeds SQLite integer capacity")
        return {
            "cost_micros": total,
            "currency_code": next(iter(currencies), None),
            "occurred_at": when,
            "rate_ids": sorted({str(row["id"]) for row in selected}),
            "basis": "catalog_upper_bound",
        }

    def local_status(self) -> dict[str, object]:
        rows = self.connection.execute(
            """
            SELECT id, provider, sku, currency_code, enabled, power_milliwatts,
                   electricity_micros_per_kwh, hardware_micros_per_hour,
                   effective_from, effective_until, evidence_hash
            FROM local_cost_profiles ORDER BY created_at DESC
            """
        ).fetchall()
        return {
            "default_enabled": False,
            "profiles": [dict(row) for row in rows],
            "note": "Local estimates are separate from provider API charges.",
        }

    def _task_project(self, task_id: str | None) -> str | None:
        if task_id is None:
            return None
        row = self.connection.execute(
            "SELECT scope FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown task: {task_id}")
        scope = str(row["scope"])
        project = next(
            (
                item.id
                for item in MemoryScopeRegistry(self.connection).ancestors(scope)
                if item.kind is MemoryScopeKind.PROJECT
            ),
            None,
        )
        return project or scope

    def _rate(
        self,
        *,
        service_kind: str,
        provider: str,
        sku: str,
        operation: str,
        meter_kind: str,
        occurred_at: str,
    ) -> sqlite3.Row | None:
        rows = self.connection.execute(
            """
            SELECT * FROM price_rates
            WHERE service_kind=? AND provider=? AND sku=? AND operation=?
              AND meter_kind=? AND effective_from <= ?
              AND (effective_until IS NULL OR effective_until > ?)
            """,
            (
                service_kind, provider, sku, operation, meter_kind,
                occurred_at, occurred_at,
            ),
        ).fetchall()
        if len(rows) > 1:
            raise RuntimeError("ambiguous price rate match")
        return None if not rows else rows[0]

    def _insert_event(
        self,
        *,
        attempt_id: str,
        source_kind: str,
        task_id: str | None,
        provider: str,
        sku: str,
        operation: str,
        call_status: str,
        usage_quality: str,
        occurred_at: str,
        meters: Mapping[str, int],
        skills: Iterable[str],
        local_profile: sqlite3.Row | None = None,
        forced_accounting_status: str | None = None,
    ) -> dict[str, object]:
        attempt = _identifier(attempt_id, "attempt_id")
        provider = _identifier(provider, "provider")
        sku = _identifier(sku, "sku")
        operation = _identifier(operation, "operation")
        if call_status not in {"succeeded", "failed", "partial", "unknown"}:
            raise ValueError("invalid call_status")
        if usage_quality not in {
            "provider_reported", "locally_measured", "estimated", "unknown"
        }:
            raise ValueError("invalid usage_quality")
        when = _timestamp(occurred_at, "occurred_at")
        project = self._task_project(task_id)
        meter_rows: list[tuple[str, int, str, str | None, int, str]] = []
        currencies: set[str] = set()
        if local_profile is not None:
            currencies.add(str(local_profile["currency_code"]))
        missing = 0
        for meter_kind, raw_quantity in meters.items():
            quantity = _integer(
                raw_quantity, meter_kind, maximum=1_000_000_000
            )
            if quantity == 0:
                continue
            if local_profile is not None:
                if meter_kind == "electricity":
                    amount = _ceil_ratio(
                        int(local_profile["power_milliwatts"])
                        * quantity
                        * int(local_profile["electricity_micros_per_kwh"]),
                        3_600_000_000_000,
                    )
                elif meter_kind == "hardware":
                    amount = _ceil_ratio(
                        quantity
                        * int(local_profile["hardware_micros_per_hour"]),
                        3_600_000,
                    )
                else:
                    raise ValueError("invalid local meter")
                currencies.add(str(local_profile["currency_code"]))
                meter_rows.append(
                    (meter_kind, quantity, "millisecond", None, amount,
                     "local_estimate")
                )
                continue
            rate = None if usage_quality == "unknown" else self._rate(
                service_kind=source_kind,
                provider=provider,
                sku=sku,
                operation=operation,
                meter_kind=meter_kind,
                occurred_at=when,
            )
            if rate is None:
                missing += 1
                unit = "call" if meter_kind == "tool_call" else "token"
                meter_rows.append(
                    (meter_kind, quantity, unit, None, 0, "unpriced")
                )
            else:
                currencies.add(str(rate["currency_code"]))
                if len(currencies) > 1:
                    raise ValueError("one event cannot mix currencies")
                amount = _ceil_ratio(
                    quantity * int(rate["price_micros"]),
                    int(rate["unit_size"]),
                )
                unit = "call" if meter_kind == "tool_call" else "token"
                meter_rows.append(
                    (meter_kind, quantity, unit, str(rate["id"]), amount,
                     "priced")
                )
        if forced_accounting_status is not None:
            if forced_accounting_status not in {
                "unpriced", "local_disabled"
            }:
                raise ValueError("invalid forced accounting status")
            accounting_status = forced_accounting_status
        elif local_profile is not None:
            accounting_status = "local_estimate"
        elif not meter_rows or missing == len(meter_rows):
            accounting_status = "unpriced"
        elif missing:
            accounting_status = "partially_priced"
        else:
            accounting_status = "priced"
        currency = next(iter(currencies), None)
        event_id = str(uuid.uuid4())
        unique_skills = tuple(dict.fromkeys(skills))
        if len(unique_skills) > 64:
            raise ValueError("at most 64 skill allocations are supported")
        if unique_skills and task_id is None:
            raise ValueError("skill allocation requires a retained task")
        if unique_skills:
            bound_skills = {
                str(row["source_id"])
                for row in self.connection.execute(
                    """
                    SELECT source_id FROM context_uses
                    WHERE task_id=? AND source_type='skill'
                    """,
                    (task_id,),
                ).fetchall()
            }
            if not set(unique_skills) <= bound_skills:
                raise ValueError(
                    "skill allocation must match task context selection"
                )
        total = sum(row[4] for row in meter_rows)
        if total > MAX_SQLITE_INTEGER:
            raise ValueError("event total exceeds SQLite integer capacity")
        evidence = digest(
            {
                "attempt_id": attempt,
                "source_kind": source_kind,
                "task_id": task_id,
                "project": project,
                "provider": provider,
                "sku": sku,
                "operation": operation,
                "call_status": call_status,
                "usage_quality": usage_quality,
                "accounting_status": accounting_status,
                "currency": currency,
                "meter_lines": meter_rows,
                "skill_ids": unique_skills,
                "local_profile_id": (
                    None if local_profile is None else str(local_profile["id"])
                ),
                "occurred_at": when,
            }
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO cost_events(
                    id, attempt_id, source_kind, task_id, project_scope,
                    provider, sku, operation, call_status, usage_quality,
                    accounting_status, expected_meter_lines,
                    expected_skill_allocations, currency_code,
                    local_profile_id, evidence_hash, occurred_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, attempt, source_kind, task_id, project,
                    provider, sku, operation, call_status, usage_quality,
                    accounting_status, len(meter_rows), len(unique_skills),
                    currency,
                    None if local_profile is None else str(local_profile["id"]),
                    evidence, when, _now(),
                ),
            )
            self.connection.executemany(
                """
                INSERT INTO cost_meter_lines(
                    event_id, meter_kind, quantity, quantity_unit, rate_id,
                    amount_micros, pricing_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ((event_id, *row) for row in meter_rows),
            )
            if unique_skills:
                for skill_id in unique_skills:
                    if self.connection.execute(
                        "SELECT 1 FROM skills WHERE id=?", (skill_id,)
                    ).fetchone() is None:
                        raise LookupError(f"Unknown skill: {skill_id}")
                count = len(unique_skills)
                base_amount, amount_remainder = divmod(total, count)
                base_weight, weight_remainder = divmod(1_000_000, count)
                for index, skill_id in enumerate(unique_skills):
                    self.connection.execute(
                        """
                        INSERT INTO cost_skill_allocations(
                            event_id, skill_id, weight_millionths,
                            allocated_micros, allocation_basis
                        ) VALUES (?, ?, ?, ?, 'equal_share')
                        """,
                        (
                            event_id, skill_id,
                            base_weight + int(index < weight_remainder),
                            base_amount + int(index < amount_remainder),
                        ),
                    )
            self.connection.execute(
                """
                INSERT INTO cost_event_seals(
                    event_id, seal_hash, total_micros,
                    allocated_micros, sealed_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event_id, evidence, total,
                    total if unique_skills else 0, _now(),
                ),
            )
        return self.event(event_id)

    def record_model(
        self,
        *,
        attempt_id: str,
        provider: str,
        model: str,
        operation: str = "chat",
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        task_id: str | None = None,
        call_status: str = "succeeded",
        occurred_at: str | None = None,
        skill_ids: Iterable[str] = (),
    ) -> dict[str, object]:
        return self._record_model(
            attempt_id=attempt_id,
            provider=provider,
            model=model,
            operation=operation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            task_id=task_id,
            call_status=call_status,
            usage_quality="estimated",
            occurred_at=occurred_at,
            skill_ids=skill_ids,
        )

    def record_model_from_adapter(
        self,
        *,
        attempt_id: str,
        provider: str,
        model: str,
        operation: str = "chat",
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        task_id: str | None = None,
        call_status: str = "succeeded",
        usage_quality: str = "provider_reported",
        occurred_at: str | None = None,
        skill_ids: Iterable[str] = (),
    ) -> dict[str, object]:
        return self._record_model(
            attempt_id=attempt_id,
            provider=provider,
            model=model,
            operation=operation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            task_id=task_id,
            call_status=call_status,
            usage_quality=usage_quality,
            occurred_at=occurred_at,
            skill_ids=skill_ids,
        )

    def _record_model(
        self,
        *,
        attempt_id: str,
        provider: str,
        model: str,
        operation: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
        task_id: str | None,
        call_status: str,
        usage_quality: str,
        occurred_at: str | None,
        skill_ids: Iterable[str],
    ) -> dict[str, object]:
        input_count = _integer(input_tokens, "input_tokens")
        output_count = _integer(output_tokens, "output_tokens")
        cache_read = _integer(cache_read_tokens, "cache_read_tokens")
        cache_write = _integer(cache_write_tokens, "cache_write_tokens")
        if cache_read + cache_write > input_count:
            raise ValueError("cache read and write tokens cannot exceed input")
        return self._insert_event(
            attempt_id=attempt_id,
            source_kind="model",
            task_id=task_id,
            provider=provider,
            sku=model,
            operation=operation,
            call_status=call_status,
            usage_quality=usage_quality,
            occurred_at=occurred_at or _now(),
            meters={
                "uncached_input_token": input_count - cache_read - cache_write,
                "cache_read_token": cache_read,
                "cache_write_token": cache_write,
                "output_token": output_count,
            },
            skills=skill_ids,
        )

    def record_tool(
        self,
        *,
        attempt_id: str,
        provider: str,
        tool: str,
        calls: int = 1,
        operation: str = "call",
        task_id: str | None = None,
        call_status: str = "succeeded",
        occurred_at: str | None = None,
        skill_ids: Iterable[str] = (),
    ) -> dict[str, object]:
        return self._insert_event(
            attempt_id=attempt_id,
            source_kind="tool",
            task_id=task_id,
            provider=provider,
            sku=tool,
            operation=operation,
            call_status=call_status,
            usage_quality="estimated",
            occurred_at=occurred_at or _now(),
            meters={"tool_call": _integer(calls, "calls", minimum=1)},
            skills=skill_ids,
        )

    def record_local(
        self,
        *,
        attempt_id: str,
        provider: str,
        model: str,
        duration_ms: int,
        task_id: str | None = None,
        call_status: str = "succeeded",
        occurred_at: str | None = None,
        skill_ids: Iterable[str] = (),
    ) -> dict[str, object]:
        duration = _integer(duration_ms, "duration_ms")
        when = _timestamp(occurred_at or _now(), "occurred_at")
        profiles = self.connection.execute(
            """
            SELECT * FROM local_cost_profiles
            WHERE provider=? AND sku=? AND effective_from <= ?
              AND (effective_until IS NULL OR effective_until > ?)
            """,
            (provider, model, when, when),
        ).fetchall()
        if len(profiles) > 1:
            raise RuntimeError("ambiguous local cost profile")
        profile = profiles[0] if profiles and int(profiles[0]["enabled"]) else None
        if profile is None:
            event = self._insert_event(
                attempt_id=attempt_id,
                source_kind="local",
                task_id=task_id,
                provider=provider,
                sku=model,
                operation="inference",
                call_status=call_status,
                usage_quality="unknown",
                occurred_at=when,
                meters={},
                skills=skill_ids,
                forced_accounting_status="local_disabled",
            )
            return event
        return self._insert_event(
            attempt_id=attempt_id,
            source_kind="local",
            task_id=task_id,
            provider=provider,
            sku=model,
            operation="inference",
            call_status=call_status,
            usage_quality="estimated",
            occurred_at=when,
            meters={"electricity": duration, "hardware": duration},
            skills=skill_ids,
            local_profile=profile,
        )

    def event(self, event_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM cost_events WHERE id=?", (event_id,)
        ).fetchone()
        if row is None:
            raise LookupError(event_id)
        meters = self.connection.execute(
            """
            SELECT meter_kind, quantity, quantity_unit, rate_id,
                   amount_micros, pricing_status
            FROM cost_meter_lines WHERE event_id=? ORDER BY meter_kind
            """,
            (event_id,),
        ).fetchall()
        allocations = self.connection.execute(
            """
            SELECT skill_id, weight_millionths, allocated_micros,
                   allocation_basis
            FROM cost_skill_allocations WHERE event_id=? ORDER BY skill_id
            """,
            (event_id,),
        ).fetchall()
        seal = self.connection.execute(
            """
            SELECT seal_hash, total_micros, allocated_micros, sealed_at
            FROM cost_event_seals WHERE event_id=?
            """,
            (event_id,),
        ).fetchone()
        return {
            **dict(row),
            "sealed": seal is not None,
            "total_micros": (
                sum(int(item["amount_micros"]) for item in meters)
                if seal is None else int(seal["total_micros"])
            ),
            "seal": None if seal is None else dict(seal),
            "meters": [dict(item) for item in meters],
            "skill_allocations": [dict(item) for item in allocations],
        }

    def report(self) -> dict[str, object]:
        currency_rows = self.connection.execute(
            """
            SELECT e.currency_code,
                   SUM(l.amount_micros) AS total_micros,
                   SUM(CASE WHEN e.task_id IS NOT NULL
                            THEN l.amount_micros ELSE 0 END)
                       AS task_bound_micros,
                   SUM(CASE WHEN e.task_id IS NULL
                            THEN l.amount_micros ELSE 0 END)
                       AS unallocated_micros,
                   COUNT(DISTINCT e.id) AS events,
                   COUNT(DISTINCT e.task_id) AS tasks,
                   COUNT(DISTINCT CASE WHEN t.status='succeeded'
                                      THEN e.task_id END) AS successes,
                   SUM(CASE WHEN e.usage_quality='estimated'
                            OR e.accounting_status='local_estimate'
                            THEN l.amount_micros ELSE 0 END) AS estimated_micros
            FROM cost_events e
            JOIN cost_event_seals s ON s.event_id=e.id
            JOIN cost_meter_lines l ON l.event_id=e.id
            LEFT JOIN tasks t ON t.id=e.task_id
            WHERE e.currency_code IS NOT NULL
            GROUP BY e.currency_code ORDER BY e.currency_code
            """
        ).fetchall()
        currencies = []
        for row in currency_rows:
            item = dict(row)
            total = int(item["total_micros"] or 0)
            task_bound = int(item["task_bound_micros"] or 0)
            tasks = int(item["tasks"] or 0)
            successes = int(item["successes"] or 0)
            item["cost_per_task_micros"] = (
                None if tasks == 0 else _ceil_ratio(task_bound, tasks)
            )
            item["cost_per_success_micros"] = (
                None if successes == 0
                else _ceil_ratio(task_bound, successes)
            )
            currencies.append(item)

        def grouped(
            columns: str, group: str, condition: str = "1=1"
        ) -> list[dict[str, object]]:
            return [
                dict(row) for row in self.connection.execute(
                    f"""
                    SELECT {columns}, e.currency_code,
                           COALESCE(SUM(l.amount_micros), 0) AS total_micros,
                           COUNT(DISTINCT e.id) AS events,
                           COUNT(DISTINCT CASE
                               WHEN e.accounting_status='partially_priced'
                               THEN e.id END) AS partial_events,
                           COUNT(DISTINCT CASE
                               WHEN e.accounting_status IN (
                                   'unpriced', 'local_disabled'
                               ) THEN e.id END) AS unpriced_events,
                           COUNT(DISTINCT CASE
                               WHEN e.usage_quality='unknown'
                               THEN e.id END) AS unknown_usage_events
                    FROM cost_events e
                    JOIN cost_event_seals s ON s.event_id=e.id
                    LEFT JOIN cost_meter_lines l ON l.event_id=e.id
                    WHERE {condition}
                    GROUP BY {group}, e.currency_code
                    ORDER BY total_micros DESC
                    """
                ).fetchall()
            ]

        skills = [
            dict(row) for row in self.connection.execute(
                """
                SELECT a.skill_id, e.currency_code,
                       SUM(a.allocated_micros) AS allocated_micros,
                       COUNT(DISTINCT a.event_id) AS events,
                       COUNT(DISTINCT CASE
                           WHEN e.accounting_status='partially_priced'
                           THEN e.id END) AS partial_events,
                       COUNT(DISTINCT CASE
                           WHEN e.accounting_status IN (
                               'unpriced', 'local_disabled'
                           ) THEN e.id END) AS unpriced_events,
                       COUNT(DISTINCT CASE
                           WHEN e.usage_quality='unknown'
                           THEN e.id END) AS unknown_usage_events,
                       CASE WHEN SUM(
                           e.accounting_status IN (
                               'partially_priced', 'unpriced',
                               'local_disabled'
                           ) OR e.usage_quality='unknown'
                       ) = 0 THEN 'complete' ELSE 'partial'
                       END AS coverage_status,
                       'allocation_view_not_additional_spend' AS interpretation
                FROM cost_skill_allocations a
                JOIN cost_events e ON e.id=a.event_id
                JOIN cost_event_seals s ON s.event_id=e.id
                GROUP BY a.skill_id, e.currency_code
                ORDER BY allocated_micros DESC
                """
            ).fetchall()
        ]
        coverage = dict(self.connection.execute(
            """
            SELECT COUNT(*) AS events,
                   COALESCE(SUM(accounting_status='priced'), 0) AS priced,
                   COALESCE(SUM(accounting_status='partially_priced'), 0) AS partial,
                   COALESCE(SUM(accounting_status='unpriced'), 0) AS unpriced,
                   COALESCE(SUM(accounting_status='local_estimate'), 0) AS local_estimates,
                   COALESCE(SUM(accounting_status='local_disabled'), 0) AS local_disabled
            FROM cost_events e
            JOIN cost_event_seals s ON s.event_id=e.id
            """
        ).fetchone())
        complete = (
            int(coverage["partial"] or 0) == 0
            and int(coverage["unpriced"] or 0) == 0
            and int(coverage["local_disabled"] or 0) == 0
        )
        for item in currencies:
            item["coverage_status"] = (
                "complete" if complete else "partial"
            )
            item["priced_cost_per_task_micros"] = item[
                "cost_per_task_micros"
            ]
            item["priced_cost_per_success_micros"] = item[
                "cost_per_success_micros"
            ]
            if not complete:
                item["cost_per_task_micros"] = None
                item["cost_per_success_micros"] = None
        return {
            "as_of": _now(),
            "money_unit": "currency microunits",
            "legacy_telemetry_included": False,
            "coverage": coverage,
            "monetary_totals_complete": complete,
            "currencies": currencies,
            "by_task": grouped("e.task_id", "e.task_id"),
            "by_project": grouped(
                "COALESCE(e.project_scope, 'unallocated') AS project",
                "COALESCE(e.project_scope, 'unallocated')",
            ),
            "by_model": grouped(
                "e.provider, e.sku, e.operation",
                "e.provider, e.sku, e.operation",
                "e.source_kind IN ('model', 'local')",
            ),
            "by_tool": grouped(
                "e.provider, e.sku AS tool, e.operation",
                "e.provider, e.sku, e.operation",
                "e.source_kind='tool'",
            ),
            "by_skill": skills,
        }
