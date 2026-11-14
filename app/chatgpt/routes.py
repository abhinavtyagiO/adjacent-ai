"""Local consent screen, OAuth callback and non-secret account/model management APIs."""

import secrets
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, StrictBool

from app.api.routes import authorize
from app.chatgpt.service import MANAGE_USAGE, ChatGPTService, local_only, public_account
from app.config import get_settings
from app.errors import DomainError

router = APIRouter(prefix="/v1/chatgpt", tags=["ChatGPT plan"])
COOKIE = "adjacent_chatgpt_flow"


@lru_cache
def get_chatgpt_service():
    return ChatGPTService(get_settings())


Service = Annotated[ChatGPTService, Depends(get_chatgpt_service)]


class SignInRequest(BaseModel):
    plan_usage_consent: StrictBool
    account_id: uuid.UUID | None = None
    request_plan_consent_again: bool = False


class ModelSelection(BaseModel):
    model: str = Field(min_length=1, max_length=200)


def same_origin(request: Request):
    """Reject browser-origin CSRF/DNS-rebinding against the local management surface."""
    settings = get_settings()
    local_only(settings)
    configured = urlsplit(settings.chatgpt_redirect_uri)
    origin = request.headers.get("origin")
    allowed = f"{configured.scheme}://{configured.netloc}"
    if origin and origin != allowed:
        raise DomainError(
            "CHATGPT_ORIGIN_INVALID", "Use the configured loopback origin for ChatGPT sign-in", 403
        )
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise DomainError(
            "CHATGPT_ORIGIN_INVALID", "Cross-site ChatGPT account changes are not allowed", 403
        )
    # Test clients/internal reverse proxies may differ from callback host; browser
    # requests must be served on the fixed loopback host and origin.
    if request.headers.get("sec-fetch-site") and request.headers.get("host") != configured.netloc:
        raise DomainError(
            "CHATGPT_ORIGIN_INVALID", "Open the configured loopback sign-in address", 403
        )


@router.post("/authorizations", dependencies=[Depends(authorize), Depends(same_origin)])
def begin_sign_in(data: SignInRequest, response: Response, service: Service, request: Request):
    result, cookie = service.begin(
        data.plan_usage_consent,
        data.account_id,
        data.request_plan_consent_again,
        request.cookies.get(COOKIE),
    )
    response.set_cookie(
        COOKIE, cookie, max_age=600, httponly=True, samesite="lax", path="/v1/chatgpt"
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return result


@router.get("/callback")
async def callback(request: Request, service: Service):
    # This one endpoint is authorized by the one-use OAuth state + browser cookie,
    # not by a bearer header the OpenAI browser redirect cannot supply.
    values = request.query_params
    if any(len(values.getlist(k)) > 1 for k in ["state", "code", "client_id", "error"]):
        raise DomainError("CHATGPT_OAUTH_STATE_INVALID", "Duplicate OAuth callback parameters", 400)
    state, cookie = values.get("state"), request.cookies.get(COOKIE)
    if not state or not cookie:
        raise DomainError(
            "CHATGPT_OAUTH_STATE_INVALID", "Return to the browser that started sign-in", 400
        )
    account = await service.callback(
        state, cookie, values.get("code"), values.get("client_id"), values.get("error")
    )
    enabled = account["plan_usage_enabled"]
    message = (
        (
            "You’re using your ChatGPT plan. Eligible requests use your plan or credits."
            if account.get("show_plan_welcome")
            else "ChatGPT account reconnected."
        )
        if enabled
        else "Signed in, but ChatGPT plan use is not enabled. Inference remains blocked."
    )
    response = HTMLResponse(f'''<!doctype html><html lang="en"><meta charset="utf-8">
    <title>ChatGPT connection</title><main><h1>ChatGPT connection</h1><p>{message}</p>
    <p>No API-key billing fallback is enabled.</p><a href="/v1/chatgpt/connect">Got it</a>
    <p><a href="{MANAGE_USAGE}">Manage ChatGPT usage</a></p></main></html>''')
    response.delete_cookie(COOKIE, path="/v1/chatgpt")
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"
    )
    return response


@router.get("/accounts", dependencies=[Depends(authorize)])
def accounts(service: Service):
    local_only(service.settings)
    return {
        "items": [public_account(a) for a in service.store.accounts()],
        "active_account_id": service.store.selected_id(),
        "manage_usage_url": MANAGE_USAGE,
    }


@router.post(
    "/accounts/{account_id}/select", dependencies=[Depends(authorize), Depends(same_origin)]
)
def select_account(account_id: uuid.UUID, service: Service):
    account = service.require_account(account_id)
    service.store.select(account_id)
    return public_account(account)


@router.get("/accounts/{account_id}/models", dependencies=[Depends(authorize)])
async def models(account_id: uuid.UUID, service: Service):
    return {
        "items": await service.models(account_id),
        "selected_model": service.store.account(account_id).get("selected_model"),
    }


@router.put("/accounts/{account_id}/model", dependencies=[Depends(authorize), Depends(same_origin)])
async def select_model(account_id: uuid.UUID, data: ModelSelection, service: Service):
    return await service.select_model(account_id, data.model)


@router.post(
    "/accounts/{account_id}/resume", dependencies=[Depends(authorize), Depends(same_origin)]
)
def resume(account_id: uuid.UUID, service: Service):
    local_only(service.settings)
    with service.store.lock("account-write-" + str(account_id)):
        account = service.store.account(account_id)
        if account.get("status") != "usage_limited":
            raise DomainError(
                "CHATGPT_NOT_PAUSED", "Only a usage-limited account can be resumed", 409
            )
        account["status"] = "connected"
        service.store.save_account(account)
    return public_account(account)


@router.delete(
    "/accounts/{account_id}/session", dependencies=[Depends(authorize), Depends(same_origin)]
)
async def sign_out(account_id: uuid.UUID, service: Service):
    return await service.sign_out(account_id)


@router.get("/connect", dependencies=[Depends(same_origin)], response_class=HTMLResponse)
def connect_page(service: Service):
    """Minimal local UI; identity/credentials remain on the server, never browser storage."""
    nonce = secrets.token_urlsafe(24)
    html = Path(__file__).with_name("connect.html").read_text()
    response = HTMLResponse(html.replace("SCRIPT_NONCE", nonce).replace("MANAGE_URL", MANAGE_USAGE))
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; connect-src 'self'; script-src 'nonce-"
        + nonce
        + "'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"
    )
    return response
