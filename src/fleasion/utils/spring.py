"""A damped spring solved in closed form, so it can be read or retargeted at any time."""

from __future__ import annotations

from math import cos, exp, sin, sqrt
from typing import Final, NamedTuple

# Close enough to the target, and slow enough, to call the spring at rest
_REST_DISTANCE: Final = 0.5
_REST_SPEED: Final = 1.0


class SpringTuning(NamedTuple):
    """How a spring moves.

    Attributes
    ----------
    damping
        The damping ratio: below 1 it overshoots and oscillates, 1 is critically
        damped, and above 1 it creeps into place without overshooting.
    speed
        The undamped angular frequency in radians per second; higher is snappier.
    """

    damping: float
    speed: float


class SpringState(NamedTuple):
    """Where a spring is and how fast it is moving, in units and units a second."""

    position: float
    velocity: float


class Spring:
    """A damped spring heading for a target.

    The motion is solved exactly rather than stepped, so the state at any time
    since the last rebase is a single evaluation, whatever the frame rate.

    Parameters
    ----------
    position
        Where the spring starts, at rest.
    tuning
        How it moves; see SpringTuning.
    """

    def __init__(self, position: float = 0.0, tuning: SpringTuning | None = None) -> None:
        self.tuning = tuning or SpringTuning(damping=1.0, speed=1.0)
        self.target = position
        self._start = SpringState(position, 0.0)

    def evaluate(self, elapsed: float) -> SpringState:
        """The state `elapsed` seconds after the last rebase.

        Parameters
        ----------
        elapsed
            Seconds since the spring was last rebased.

        Returns
        -------
        SpringState
            The position and velocity at that time.
        """
        damping, speed = self.tuning
        time = speed * elapsed
        damping_squared = damping * damping

        # The homogeneous solution, as the cosine-like and sine-like parts of its
        # decay. Each damping regime has its own closed form
        if damping_squared < 1:
            # Underdamped: an oscillation inside a decaying envelope
            root = sqrt(1 - damping_squared)
            decay = exp(-damping * time) / root
            cos_part = decay * cos(root * time)
            sin_part = decay * sin(root * time)
        elif damping_squared == 1:
            # Critically damped: the quickest approach without overshooting
            root = 1.0
            decay = exp(-damping * time)
            cos_part = decay
            sin_part = decay * time
        else:
            # Overdamped: two decaying exponentials, one slow and one fast
            root = sqrt(damping_squared - 1)
            slow = exp((-damping + root) * time) / (2 * root)
            fast = exp((-damping - root) * time) / (2 * root)
            cos_part = slow + fast
            sin_part = slow - fast

        start_position, start_velocity = self._start
        offset = self.target - start_position

        # The share of the starting offset still to cover
        remaining = root * cos_part + damping * sin_part
        position = self.target - remaining * offset + sin_part / speed * start_velocity
        velocity = (
            speed * sin_part * offset + (root * cos_part - damping * sin_part) * start_velocity
        )
        return SpringState(position, velocity)

    def rebase(self, state: SpringState) -> None:
        """Restart the clock from this state, keeping the target and tuning."""
        self._start = state

    def at_rest(self, state: SpringState) -> bool:
        """Whether a state is close enough to the target and slow enough to stop."""
        return (
            abs(self.target - state.position) < _REST_DISTANCE and abs(state.velocity) < _REST_SPEED
        )
