"""The import formats of V1 and their detection ("Riconosci automaticamente", mockup 07)."""

from collections.abc import Mapping
from types import MappingProxyType

from portfolio_tracker.importers.app_csv import AppCsvImporter
from portfolio_tracker.importers.base import Importer, ImportFileError
from portfolio_tracker.importers.codes import ImportErrorCode
from portfolio_tracker.importers.directa import DirectaImporter

IMPORTERS: Mapping[str, Importer] = MappingProxyType(
    {importer.source: importer for importer in (DirectaImporter(), AppCsvImporter())}
)
_ZIP_MAGIC = b"PK\x03\x04"  # every .xlsx is a zip archive


def importer_for(data: bytes, source: str | None = None) -> Importer:
    """The chosen format, or detected from the content: an xlsx is a Directa export (the
    only spreadsheet format of V1), anything else is read as the app CSV."""
    if source is not None:
        importer = IMPORTERS.get(source)
        if importer is None:
            raise ImportFileError(ImportErrorCode.IMPORT_FORMAT_UNKNOWN, "unknown import format")
        return importer
    return IMPORTERS["directa-xlsx" if data.startswith(_ZIP_MAGIC) else "app-csv"]
