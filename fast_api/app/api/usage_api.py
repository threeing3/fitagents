"""Authenticated quota visibility for browsers and API clients."""

from fastapi import APIRouter, Depends

from fast_api.app.core.auth import get_current_user
from fast_api.app.db import models
from fast_api.app.services.usage_quota import UsageQuotaService

usage_router = APIRouter(prefix="/v1/usage", tags=["usage"])


@usage_router.get("/summary")
def usage_summary(current_user: models.User = Depends(get_current_user)) -> dict:
    return UsageQuotaService().snapshot(current_user.id).as_dict()
