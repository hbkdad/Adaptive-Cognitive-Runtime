from __future__ import annotations

import json
import hashlib
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Iterable, Protocol, Sequence


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskState(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset(
    {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}
)

ALLOWED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.CREATED: frozenset({TaskState.PLANNING, TaskState.CANCELLED}),
    TaskState.PLANNING: frozenset(
        {TaskState.EXECUTING, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.EXECUTING: frozenset(
        {TaskState.VERIFYING, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.VERIFYING: frozenset(
        {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.COMPLETED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


class InvalidTransition(ValueError):
    pass


class PlanningBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class Task:
    objective: str
    constraints: tuple[str, ...] = ()
    requested_output: str = "text"
    parent_task_id: str | None = None
    priority: int = 0
    token_budget: int | None = None
    money_budget: float | None = None
    time_budget_seconds: float | None = None
    permissions: tuple[str, ...] = ()
    scope: str = "global"
    task_class: str = "general"
    strategy: str | None = None
    environment_json: str = "{}"
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.objective.strip():
            raise ValueError("Task objective cannot be empty")
        if self.token_budget is not None and self.token_budget < 1:
            raise ValueError("token_budget must be positive")
        if self.money_budget is not None and self.money_budget < 0:
            raise ValueError("money_budget cannot be negative")
        if self.time_budget_seconds is not None and self.time_budget_seconds <= 0:
            raise ValueError("time_budget_seconds must be positive")
        if not self.scope.strip():
            raise ValueError("scope cannot be empty")
        if not self.task_class.strip():
            raise ValueError("task_class cannot be empty")
        environment = json.loads(self.environment_json)
        if not isinstance(environment, dict):
            raise ValueError("environment_json must be a JSON object")


@dataclass(frozen=True)
class Step:
    sequence: int
    name: str
    operation: str
    input_json: str = "{}"
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True)
class Action:
    step_id: str
    operation: str
    input_json: str
    output_json: str
    status: str
    started_at: str
    completed_at: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True)
class Observation:
    step_id: str
    content: str
    created_at: str = field(default_factory=utc_now)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True)
class Artifact:
    step_id: str
    kind: str
    uri: str
    created_at: str = field(default_factory=utc_now)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True)
class Result:
    content: str
    output_type: str = "text"


@dataclass(frozen=True)
class Failure:
    kind: str
    message: str
    step_id: str | None = None
    retryable: bool = False


@dataclass(frozen=True)
class Evaluation:
    passed: bool
    score: float
    feedback: str
    source: str

    def __post_init__(self) -> None:
        if not 0 <= self.score <= 1:
            raise ValueError("Evaluation score must be between 0 and 1")


@dataclass(frozen=True)
class TaskEvent:
    sequence: int
    task_id: str
    run_id: str
    event_type: str
    state: TaskState
    payload_json: str
    created_at: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class ExecutionOutput:
    content: str
    observation: str | None = None
    artifacts: tuple[Artifact, ...] = ()
    metadata_json: str = "{}"


@dataclass(frozen=True)
class TaskRun:
    id: str
    task_id: str
    state: TaskState
    steps: tuple[Step, ...]
    actions: tuple[Action, ...]
    observations: tuple[Observation, ...]
    artifacts: tuple[Artifact, ...]
    events: tuple[TaskEvent, ...]
    result: Result | None
    failure: Failure | None
    verification: Evaluation | None
    evaluation: Evaluation | None
    started_at: str
    completed_at: str


class Planner(Protocol):
    def plan(self, task: Task) -> Sequence[Step]: ...


@dataclass(frozen=True)
class PlanningAdvice:
    constraints: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    weights: tuple[float, ...] = ()
    blocked: bool = False


class PlanningAdvisor(Protocol):
    def advise(self, task: Task) -> PlanningAdvice: ...


class Executor(Protocol):
    def execute(self, task: Task, step: Step) -> ExecutionOutput: ...


class Verifier(Protocol):
    def verify(
        self, task: Task, result: Result, observations: Sequence[Observation]
    ) -> Evaluation: ...


class Evaluator(Protocol):
    def evaluate(
        self, task: Task, result: Result, verification: Evaluation
    ) -> Evaluation: ...


TaskSubscriber = Callable[[TaskEvent], None]


class TaskEventBus:
    """Small synchronous boundary; telemetry can subscribe without engine imports."""

    def __init__(self) -> None:
        self._subscribers: list[TaskSubscriber] = []

    def subscribe(self, subscriber: TaskSubscriber) -> Callable[[], None]:
        self._subscribers.append(subscriber)

        def unsubscribe() -> None:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

        return unsubscribe

    def publish(self, event: TaskEvent) -> None:
        for subscriber in tuple(self._subscribers):
            subscriber(event)


class CancellationToken:
    def __init__(self) -> None:
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True


class Lifecycle:
    def __init__(self, initial: TaskState = TaskState.CREATED) -> None:
        self.state = initial

    def transition(self, target: TaskState) -> TaskState:
        if target not in ALLOWED_TRANSITIONS[self.state]:
            raise InvalidTransition(f"Cannot transition from {self.state} to {target}")
        self.state = target
        return self.state


class SingleStepPlanner:
    """Deterministic planner used until model-driven planning is introduced."""

    def __init__(self, operation: str = "execute") -> None:
        self.operation = operation

    def plan(self, task: Task) -> Sequence[Step]:
        return (
            Step(
                sequence=1,
                name=task.objective,
                operation=self.operation,
                input_json=json.dumps({"objective": task.objective}),
            ),
        )


class FunctionExecutor:
    """Adapter for deterministic functions keyed by operation name."""

    def __init__(
        self, operations: dict[str, Callable[[Task, Step], ExecutionOutput]]
    ) -> None:
        self.operations = dict(operations)

    def execute(self, task: Task, step: Step) -> ExecutionOutput:
        operation = self.operations.get(step.operation)
        if operation is None:
            raise LookupError(f"No deterministic operation registered: {step.operation}")
        return operation(task, step)


class PassVerifier:
    def verify(
        self, task: Task, result: Result, observations: Sequence[Observation]
    ) -> Evaluation:
        passed = bool(result.content.strip())
        return Evaluation(
            passed=passed,
            score=1.0 if passed else 0.0,
            feedback="Deterministic result is non-empty" if passed else "Result is empty",
            source="deterministic-verifier",
        )


class PassEvaluator:
    def evaluate(
        self, task: Task, result: Result, verification: Evaluation
    ) -> Evaluation:
        return Evaluation(
            passed=verification.passed,
            score=verification.score,
            feedback="Accepted verified deterministic result",
            source="deterministic-evaluator",
        )


class TaskRunner:
    def __init__(
        self,
        *,
        planner: Planner,
        executor: Executor,
        verifier: Verifier,
        evaluator: Evaluator,
        event_bus: TaskEventBus | None = None,
        planning_advisors: Sequence[PlanningAdvisor] = (),
    ) -> None:
        self.planner = planner
        self.executor = executor
        self.verifier = verifier
        self.evaluator = evaluator
        self.event_bus = event_bus or TaskEventBus()
        self.planning_advisors = tuple(planning_advisors)

    def run(
        self, task: Task, *, cancellation: CancellationToken | None = None
    ) -> TaskRun:
        token = cancellation or CancellationToken()
        run_id = str(uuid.uuid4())
        started_at = utc_now()
        lifecycle = Lifecycle()
        steps: list[Step] = []
        actions: list[Action] = []
        observations: list[Observation] = []
        artifacts: list[Artifact] = []
        events: list[TaskEvent] = []
        result: Result | None = None
        failure: Failure | None = None
        verification: Evaluation | None = None
        evaluation: Evaluation | None = None
        effective_task = task

        def emit(event_type: str, payload: dict[str, object] | None = None) -> None:
            event = TaskEvent(
                sequence=len(events) + 1,
                task_id=task.id,
                run_id=run_id,
                event_type=event_type,
                state=lifecycle.state,
                payload_json=json.dumps(payload or {}, sort_keys=True),
            )
            events.append(event)
            self.event_bus.publish(event)

        def transition(target: TaskState) -> None:
            previous = lifecycle.state
            lifecycle.transition(target)
            emit(
                "task.transition",
                {"from": previous.value, "to": lifecycle.state.value},
            )

        emit(
            "task.created",
            {
                "objective_sha256": hashlib.sha256(
                    task.objective.encode("utf-8")
                ).hexdigest(),
                "objective_characters": len(task.objective),
            },
        )
        try:
            if token.cancelled:
                transition(TaskState.CANCELLED)
            else:
                transition(TaskState.PLANNING)
                for advisor in self.planning_advisors:
                    advice = advisor.advise(effective_task)
                    emit(
                        "plan.advice",
                        {
                            "source_ids": advice.source_ids,
                            "weights": advice.weights,
                            "constraint_count": len(advice.constraints),
                            "blocked": advice.blocked,
                        },
                    )
                    if advice.constraints:
                        effective_task = replace(
                            effective_task,
                            constraints=(
                                *effective_task.constraints,
                                *advice.constraints,
                            ),
                        )
                    if advice.blocked:
                        raise PlanningBlocked(
                            "Deterministic failure evidence blocked this strategy"
                        )
                steps.extend(self.planner.plan(effective_task))
                emit("plan.created", {"step_count": len(steps)})
                if not steps:
                    raise ValueError("Planner returned no steps")

            if lifecycle.state not in TERMINAL_STATES:
                if token.cancelled:
                    transition(TaskState.CANCELLED)
                else:
                    transition(TaskState.EXECUTING)

            outputs: list[str] = []
            for step in steps:
                if lifecycle.state in TERMINAL_STATES:
                    break
                if token.cancelled:
                    transition(TaskState.CANCELLED)
                    break
                emit("step.started", {"step_id": step.id, "operation": step.operation})
                action_started = utc_now()
                output = self.executor.execute(effective_task, step)
                action = Action(
                    step_id=step.id,
                    operation=step.operation,
                    input_json=step.input_json,
                    output_json=output.metadata_json,
                    status="completed",
                    started_at=action_started,
                    completed_at=utc_now(),
                )
                actions.append(action)
                outputs.append(output.content)
                if output.observation:
                    observations.append(
                        Observation(step_id=step.id, content=output.observation)
                    )
                artifacts.extend(output.artifacts)
                emit("step.completed", {"step_id": step.id, "action_id": action.id})

            if lifecycle.state not in TERMINAL_STATES:
                result = Result(
                    content="\n".join(part for part in outputs if part),
                    output_type=task.requested_output,
                )
                transition(TaskState.VERIFYING)
                verification = self.verifier.verify(
                    effective_task, result, observations
                )
                emit(
                    "task.verified",
                    {"passed": verification.passed, "score": verification.score},
                )
                evaluation = self.evaluator.evaluate(
                    effective_task, result, verification
                )
                emit(
                    "task.evaluated",
                    {"passed": evaluation.passed, "score": evaluation.score},
                )
                if verification.passed and evaluation.passed:
                    transition(TaskState.COMPLETED)
                else:
                    failure = Failure(
                        kind="EvaluationFailure",
                        message=evaluation.feedback,
                        retryable=True,
                    )
                    transition(TaskState.FAILED)
        except Exception as error:
            failure = Failure(
                kind=type(error).__name__,
                message=str(error),
                step_id=steps[-1].id if steps else None,
                retryable=False,
            )
            if lifecycle.state not in TERMINAL_STATES:
                transition(TaskState.FAILED)
            emit(
                "task.error",
                {"kind": failure.kind, "message": failure.message},
            )

        return TaskRun(
            id=run_id,
            task_id=task.id,
            state=lifecycle.state,
            steps=tuple(steps),
            actions=tuple(actions),
            observations=tuple(observations),
            artifacts=tuple(artifacts),
            events=tuple(events),
            result=result,
            failure=failure,
            verification=verification,
            evaluation=evaluation,
            started_at=started_at,
            completed_at=utc_now(),
        )
