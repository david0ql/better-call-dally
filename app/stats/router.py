from __future__ import annotations

from fastapi import APIRouter, Query

from app.stats.models import StatsResponse
from app.stats.service import StatsService

router = APIRouter(prefix="/stats", tags=["stats"])
service = StatsService()


@router.get("", response_model=StatsResponse)
def get_stats(include_disabled: bool = Query(False)) -> StatsResponse:
    return service.collect(include_disabled=include_disabled)
