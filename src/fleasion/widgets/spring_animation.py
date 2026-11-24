"""An animation that follows a damped spring until it comes to rest."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from PySide6.QtCore import QAbstractAnimation, Signal

from fleasion.utils.spring import Spring, SpringState

if TYPE_CHECKING:
    from PySide6.QtCore import QObject

    from fleasion.utils.spring import SpringTuning


class SpringAnimation(QAbstractAnimation):
    """Follows a spring until it comes to rest, reporting each position.

    Retargeting while it runs keeps the current velocity, so reversing midway
    swings back smoothly instead of stopping dead. Qt's animation timer drives
    it, and like any animation it can be stepped with ``setCurrentTime``.
    """

    moved = Signal(float)
    settled = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._spring = Spring()

    @override
    def duration(self) -> int:
        # Runs until the spring comes to rest
        return -1

    def _current(self) -> SpringState:
        return self._spring.evaluate(self.currentTime() / 1000)

    def position(self) -> float:
        if self.state() != QAbstractAnimation.State.Running:
            return self._spring.target
        return self._current().position

    def jump_to(self, position: float) -> None:
        """Stop and rest at a position without animating."""
        self.stop()
        self._spring.target = position
        self._spring.rebase(SpringState(position, 0.0))

    def animate_to(self, target: float, tuning: SpringTuning) -> None:
        """Head for a target from wherever the spring is, at its current velocity.

        Parameters
        ----------
        target
            Where the spring comes to rest.
        tuning
            How it moves on the way there.
        """
        spring = self._spring
        if self.state() == QAbstractAnimation.State.Running:
            spring.rebase(self._current())
        spring.target = target
        spring.tuning = tuning
        self.stop()
        self.start()

    def finish(self) -> None:
        """Skip to the target and come to rest at once."""
        if self.state() != QAbstractAnimation.State.Running:
            return
        self.stop()
        self._spring.rebase(SpringState(self._spring.target, 0.0))
        self.moved.emit(self._spring.target)
        self.settled.emit()

    @override
    def updateCurrentTime(self, currentTime: int, /) -> None:
        state = self._spring.evaluate(currentTime / 1000)
        if currentTime > 0 and self._spring.at_rest(state):
            self.finish()
            return
        self.moved.emit(state.position)
