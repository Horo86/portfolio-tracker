"""Reference lists the screens need (M5): the same lists the API validates against."""

from fastapi import APIRouter

from portfolio_tracker.api.deps import CurrentAuth
from portfolio_tracker.currencies import ISO_4217

router = APIRouter(tags=["reference"])


@router.get("/currencies")
def list_currencies(_auth: CurrentAuth) -> list[str]:
    """Every ISO 4217 code accepted for an account, an asset quote or the reporting currency,
    sorted. Only the codes: the browser shows their names in the user's language."""
    return sorted(ISO_4217)
