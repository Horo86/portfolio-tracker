"""The Directa Excel importer (`docs/import/directa.md`): reader + mapping."""

from portfolio_tracker.importers.base import ParsedImport
from portfolio_tracker.importers.directa_mapping import SOURCE, map_directa
from portfolio_tracker.importers.directa_reader import read_directa


class DirectaImporter:
    source = SOURCE

    def parse(self, data: bytes, *, move_cash: bool = True) -> ParsedImport:
        return map_directa(read_directa(data), move_cash=move_cash)
