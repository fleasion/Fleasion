"""Fleasion desktop application package."""

from fleasion.app.compatibility import RestartHandoffUncertain
from fleasion.app.core import run_application
from fleasion.app.process_control import kill_other_fleasion_instances
from fleasion.app.restart import restart_fleasion_normally

__all__ = [
    'RestartHandoffUncertain',
    'kill_other_fleasion_instances',
    'restart_fleasion_normally',
    'run_application',
]
