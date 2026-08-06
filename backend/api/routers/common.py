"""Helpers shared by compatibility-backed routers."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from backend.services.legacy_adapter import dispatch_legacy_request


def request_target(request: Request) -> str:
    query = request.url.query
    return request.url.path + (f"?{query}" if query else "")


async def forward_request(request: Request) -> Response:
    body = await request.body()
    result = await run_in_threadpool(
        dispatch_legacy_request,
        request.method,
        request_target(request),
        dict(request.headers),
        body,
    )
    return Response(
        content=result.body,
        status_code=result.status_code,
        headers=result.headers,
    )
