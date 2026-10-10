"""Portfolio Tracker backend."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("portfolio-tracker")
except PackageNotFoundError:  # pragma: no cover - only when running from an uninstalled tree
    __version__ = "0.0.0"
