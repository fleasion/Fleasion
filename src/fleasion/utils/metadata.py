"""Application metadata."""

__all__ = [
    'APP_AUTHOR',
    'APP_CONCEPT',
    'APP_DISCORD',
    'APP_LOGIC',
    'APP_NAME',
    'APP_REPO',
    'APP_VERSION',
]

from importlib.metadata import PackageNotFoundError, metadata as distribution_metadata

from fleasion import __version__

APP_VERSION = __version__


def _project_url(label: str) -> str:
    """Read a labeled project URL from bundled distribution metadata."""
    try:
        values = distribution_metadata('fleasion').get_all('Project-URL') or []
    except PackageNotFoundError:
        return ''

    wanted_label = label.casefold()
    for value in values:
        current_label, separator, url = value.partition(',')
        if separator and current_label.strip().casefold() == wanted_label:
            return url.strip()
    return ''


APP_NAME = 'Fleasion'
APP_AUTHOR = '@8ar__, @dis_spencer, @1_v'
APP_LOGIC = '@blockce, @0100152000022000, @yeha., @emk530'
APP_CONCEPT = '@cro.p'
APP_DISCORD = 'discord.gg/hXyhKehEZF'
APP_REPO = _project_url('Repository')
