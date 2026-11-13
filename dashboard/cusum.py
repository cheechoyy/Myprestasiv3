"""Pure Bernoulli CUSUM engine for MyPrestasi shadow monitoring.

The engine is intentionally database-free so the math can be tested with
deterministic sequences. Callers are responsible for turning source records
into an ordered iterable of outcomes:

* ``True``  -> failure (a documentation note completed after the SLA)
* ``False`` -> success (a documentation note completed within the SLA)
"""

from dataclasses import dataclass
import math
from typing import Tuple


DEFAULT_P0 = 0.20
DEFAULT_P1 = 0.35
DEFAULT_H = 4.5

STATE_INSUFFICIENT_DATA = 'INSUFFICIENT_DATA'
STATE_IN_CONTROL = 'IN_CONTROL'
STATE_WATCH = 'WATCH'
STATE_ALERT = 'ALERT'


@dataclass(frozen=True)
class CusumParameters:
    """Parameters for a one-sided Bernoulli CUSUM.

    ``p0`` and ``p1`` are *failure* rates, not success rates. ``p1`` must be
    greater than ``p0`` because the chart detects an increase in failures.
    """

    p0: float = DEFAULT_P0
    p1: float = DEFAULT_P1
    h: float = DEFAULT_H

    def __post_init__(self):
        if not 0.0 < self.p0 < 1.0:
            raise ValueError('p0 mesti antara 0 dan 1.')
        if not 0.0 < self.p1 < 1.0:
            raise ValueError('p1 mesti antara 0 dan 1.')
        if self.p1 <= self.p0:
            raise ValueError('p1 mesti lebih besar daripada p0.')
        if self.h <= 0.0:
            raise ValueError('h mesti lebih besar daripada 0.')

    @property
    def late_increment(self):
        """Log-likelihood increment when a note is late (x=1)."""
        return math.log(self.p1 / self.p0)

    @property
    def on_time_increment(self):
        """Log-likelihood increment when a note is on time (x=0)."""
        return math.log((1.0 - self.p1) / (1.0 - self.p0))

    @property
    def watch_threshold(self):
        return self.h / 2.0


@dataclass(frozen=True)
class CusumStep:
    sequence: int
    is_failure: bool
    increment: float
    cusum_before: float
    cusum_value: float
    state: str
    alert_triggered: bool


@dataclass(frozen=True)
class CusumAlert:
    alert_number: int
    sequence: int
    cusum_value: float
    observation_count: int


@dataclass(frozen=True)
class CusumResult:
    steps: Tuple[CusumStep, ...]
    alerts: Tuple[CusumAlert, ...]
    cusum_value: float
    state: str
    alert_count: int
    observation_count: int


def state_for_value(value, parameters=None):
    """Return the display state for a CUSUM value."""
    parameters = parameters or CusumParameters()
    if value >= parameters.h:
        return STATE_ALERT
    if value >= parameters.watch_threshold:
        return STATE_WATCH
    return STATE_IN_CONTROL


def bernoulli_cusum(outcomes, parameters=None):
    """Calculate a one-sided Bernoulli CUSUM for ordered outcomes.

    The chart resets to zero for the observation *after* an alert. The alert
    observation itself is returned with the value that crossed ``h`` so the
    snapshot can expose the trigger value.
    """
    parameters = parameters or CusumParameters()
    steps = []
    alerts = []
    cusum_value = 0.0
    alert_count = 0
    reset_next = False

    for sequence, outcome in enumerate(outcomes, start=1):
        is_failure = bool(outcome)

        if reset_next:
            cusum_value = 0.0
            reset_next = False

        cusum_before = cusum_value
        increment = (
            parameters.late_increment
            if is_failure
            else parameters.on_time_increment
        )
        cusum_value = max(0.0, cusum_value + increment)
        state = state_for_value(cusum_value, parameters)
        alert_triggered = cusum_value >= parameters.h

        steps.append(CusumStep(
            sequence=sequence,
            is_failure=is_failure,
            increment=increment,
            cusum_before=cusum_before,
            cusum_value=cusum_value,
            state=state,
            alert_triggered=alert_triggered,
        ))

        if alert_triggered:
            alert_count += 1
            alerts.append(CusumAlert(
                alert_number=alert_count,
                sequence=sequence,
                cusum_value=cusum_value,
                observation_count=sequence,
            ))
            reset_next = True

    if not steps:
        return CusumResult(
            steps=(),
            alerts=(),
            cusum_value=0.0,
            state=STATE_INSUFFICIENT_DATA,
            alert_count=0,
            observation_count=0,
        )

    last_step = steps[-1]
    return CusumResult(
        steps=tuple(steps),
        alerts=tuple(alerts),
        cusum_value=last_step.cusum_value,
        state=last_step.state,
        alert_count=alert_count,
        observation_count=len(steps),
    )
