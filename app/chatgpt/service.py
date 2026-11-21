"""Official OAuth/OIDC, account selection and renewable ChatGPT-plan credentials.

No API key, Codex login cache, backend-api endpoint, or billing fallback is used here.
Auth/token endpoints and the inference resource are fixed to their documented origins.
"""

import base64
import hashlib
import secrets
import time
import uuid
from urllib.parse import urlencode, urlsplit

import httpx
import jwt

from app.chatgpt.store import CredentialStore
from app.errors import DomainError

ISSUER = "https://auth.openai.com"
AUTHORIZE = ISSUER + "/api/accounts/authorize"
TOKEN = ISSUER + "/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
MANAGE_USAGE = "https://chatgpt.com/settings/usage"
TERMINAL_REFRESH = {
    "invalid_grant",
    "invalid_refresh_token",
    "token_expired",
    "refresh_token_expired",
    "refresh_token_invalidated",
    "refresh_token_reused",
}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def local_only(settings):
    if settings.chatgpt_deployment_mode == "hosted":
        raise DomainError(
            "CHATGPT_HOSTED_APPROVAL_REQUIRED",
            "Hosted commercial ChatGPT-plan usage requires OpenAI preview approval; "
            "no billing fallback is enabled",
            403,
        )


def public_account(account):
    return {
        k: account.get(k)
        for k in ["id", "email", "label", "status", "selected_model", "expires_at"]
    } | {
        "plan_usage_enabled": PLAN_SCOPE in account.get("scopes", [])
        and account.get("status") == "connected",
        "manage_usage_url": MANAGE_USAGE,
    }


class ChatGPTService:
    def __init__(self, settings, store=None, client=None):
        local_only(settings)
        self.settings = settings
        self.store = store or CredentialStore(settings.chatgpt_auth_dir)
        self.client = client

    async def http(self, method, url, **kwargs):
        try:
            if self.client:
                return await self.client.request(method, url, **kwargs)
            async with httpx.AsyncClient(
                timeout=self.settings.chatgpt_http_timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                return await client.request(method, url, **kwargs)
        except httpx.HTTPError as error:
            raise DomainError(
                "CHATGPT_UNAVAILABLE",
                "OpenAI could not be reached; credentials were preserved",
                503,
            ) from error

    def begin(self, consent, account_id=None, reconsent=False, recovery_cookie=None):
        local_only(self.settings)
        if consent is not True:
            raise DomainError(
                "CHATGPT_PLAN_CONSENT_REQUIRED",
                "Explicit consent to use your ChatGPT plan is required",
                400,
            )
        account = self.store.account(account_id) if account_id else None
        state, nonce, verifier, cookie = [secrets.token_urlsafe(32) for _ in range(4)]
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        parameters = {
            "client_id": account["client_id"] if account else "dynamic_agent_client",
            "ext_agent_host_id": self.store.host_id(),
            "response_type": "code",
            "redirect_uri": self.settings.chatgpt_redirect_uri,
            "scope": SCOPES,
            "resource": RESOURCE,
            "state": state,
            "nonce": nonce,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
        if not account:
            parameters["agent_name_hint"] = self.settings.chatgpt_app_name
        if reconsent:
            parameters["prompt"] = (
                "consent"  # force_reconsent requires a separate confirmed rollout.
            )
        if account is None and recovery_cookie:
            recovery = self.store.read("recovery-" + digest(recovery_cookie))
            if recovery and recovery["expires_at"] > time.time():
                parameters["client_id"] = recovery["client_id"]
                parameters.pop("agent_name_hint", None)
        expires_at = time.time() + 600
        self.store.write(
            "pending-" + digest(state),
            {
                "nonce": nonce,
                "verifier": verifier,
                "cookie_hash": digest(cookie),
                "expires_at": expires_at,
                "redirect_uri": self.settings.chatgpt_redirect_uri,
                "account_id": str(account_id) if account else None,
                "client_id": parameters["client_id"]
                if parameters["client_id"] != "dynamic_agent_client"
                else None,
            },
        )
        # ID-token/login hints are optional and deliberately omitted from browser URLs.
        return {
            "authorization_url": AUTHORIZE + "?" + urlencode(parameters),
            "expires_at": expires_at,
        }, cookie

    async def discovery(self):
        response = await self.http("GET", ISSUER + "/.well-known/openid-configuration")
        if response.status_code != 200:
            raise DomainError(
                "CHATGPT_DISCOVERY_FAILED", "OpenAI identity metadata is unavailable", 503
            )
        try:
            data = response.json()
            if data["issuer"] != ISSUER:
                raise ValueError("Issuer mismatch")
            for key in ["jwks_uri", "revocation_endpoint"]:
                parsed = urlsplit(data[key])
                if (
                    parsed.scheme != "https"
                    or parsed.netloc != "auth.openai.com"
                    or parsed.fragment
                ):
                    raise ValueError("Untrusted identity endpoint")
            return data
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise DomainError(
                "CHATGPT_DISCOVERY_INVALID", "OpenAI identity metadata did not validate", 503
            ) from error

    async def identity(self, token, client_id, nonce=None, subject=None):
        metadata = await self.discovery()
        response = await self.http("GET", metadata["jwks_uri"])
        if response.status_code != 200:
            raise DomainError(
                "CHATGPT_IDENTITY_UNAVAILABLE", "OpenAI signing keys are unavailable", 503
            )
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256":
                raise ValueError("Unsupported signing algorithm")
            keys = response.json()["keys"]
            key = next(
                k for k in keys if k.get("kid") == header.get("kid") and k.get("kty") == "RSA"
            )
            claims = jwt.decode(
                token,
                jwt.PyJWK(key),
                algorithms=["RS256"],
                audience=client_id,
                issuer=ISSUER,
                leeway=30,
                options={"require": ["iss", "aud", "sub", "exp", "iat"]},
            )
            if nonce is not None and not secrets.compare_digest(
                str(claims.get("nonce", "")), nonce
            ):
                raise ValueError("Nonce mismatch")
            if subject is not None and claims["sub"] != subject:
                raise ValueError("Account identity mismatch")
            if not isinstance(claims["sub"], str) or not claims["sub"]:
                raise ValueError("Missing subject")
            return claims
        except (
            jwt.PyJWTError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            StopIteration,
        ) as error:
            raise DomainError(
                "CHATGPT_IDENTITY_INVALID",
                "OpenAI identity or selected account did not validate",
                401,
            ) from error

    @staticmethod
    def token_fields(data, old=None):
        old = old or {}
        try:
            scopes = data["scope"].split() if "scope" in data else old.get("scopes", [])
            connected = {PLAN_SCOPE, "resource.invoke", "offline_access"} <= set(scopes)
            if not connected:
                return {
                    "id_token": data.get("id_token") or old.get("id_token"),
                    "scopes": scopes,
                    "status": "plan_disabled",
                    "expires_at": None,
                    "saved_at": time.time(),
                }
            expires = int(data["expires_in"])
            if not 0 < expires <= 86400 or str(data["token_type"]).lower() != "bearer":
                raise ValueError("Invalid token contract")
            for key in ["access_token", "refresh_token"]:
                if not isinstance(data[key], str) or not data[key]:
                    raise ValueError("Missing credential")
            return {
                "access_token": data["access_token"],
                "refresh_token": data["refresh_token"],
                "id_token": data.get("id_token") or old.get("id_token"),
                "scopes": scopes,
                "expires_at": time.time() + expires,
                "saved_at": time.time(),
                "earliest_refresh_at": data.get("earliest_refresh_at"),
                "status": "connected"
                if PLAN_SCOPE in scopes and "resource.invoke" in scopes
                else "plan_disabled",
            }
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise DomainError(
                "CHATGPT_TOKEN_INVALID", "OpenAI token response did not validate", 401
            ) from error

    async def callback(self, state, cookie, code=None, client_id=None, error=None):
        local_only(self.settings)
        pending = self.store.consume_pending("pending-" + digest(state), digest(cookie))
        if error:
            raise DomainError(
                "CHATGPT_CONSENT_DENIED", "Sign-in or ChatGPT-plan consent was declined", 403
            )
        if not code:
            raise DomainError("CHATGPT_CODE_MISSING", "Authorization code is missing", 400)
        issued = pending["client_id"] or client_id
        if (
            not issued
            or issued == "dynamic_agent_client"
            or len(issued) > 300
            or (pending["client_id"] and client_id and client_id != pending["client_id"])
        ):
            raise DomainError(
                "CHATGPT_CLIENT_INVALID", "OAuth client registration did not match", 400
            )
        response = await self.http(
            "POST",
            TOKEN,
            data={
                "grant_type": "authorization_code",
                "client_id": issued,
                "code": code,
                "code_verifier": pending["verifier"],
                "redirect_uri": pending["redirect_uri"],
                "resource": RESOURCE,
            },
        )
        if response.status_code != 200:
            try:
                failure = response.json()
            except ValueError:
                failure = {}
            if isinstance(failure, dict) and failure.get("error") == "invalid_grant":
                self.store.write(
                    "recovery-" + digest(cookie),
                    {"client_id": issued, "expires_at": time.time() + 600},
                )
            raise DomainError(
                "CHATGPT_CODE_EXCHANGE_FAILED",
                "Code exchange failed; retry sign-in in this browser. "
                "Registration is retained briefly.",
                401,
            )
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Invalid token response")
        except ValueError as invalid:
            raise DomainError(
                "CHATGPT_TOKEN_INVALID", "OpenAI token response did not validate", 401
            ) from invalid
        old = self.store.account(pending["account_id"]) if pending["account_id"] else None
        claims = await self.identity(
            data.get("id_token", ""), issued, pending["nonce"], old["subject"] if old else None
        )
        account = {
            **(old or {}),
            "id": old["id"] if old else str(uuid.uuid4()),
            "client_id": issued,
            "subject": claims["sub"],
            "issuer": ISSUER,
            "email": claims.get("email"),
            "label": claims.get("email") or "ChatGPT account",
            "ext_agent_host_id": self.store.host_id(),
            **self.token_fields(data),
        }
        if old is None:
            # A repeated registration is matched by verified subject AND issued client ID,
            # never email; different workspaces/registrations remain separate.
            match = next(
                (
                    a
                    for a in self.store.accounts()
                    if a["subject"] == account["subject"] and a["client_id"] == issued
                ),
                None,
            )
            if match:
                account["id"] = match["id"]
                account["selected_model"] = match.get("selected_model")
        async with self.store.refresh_lock(account["id"]):
            with self.store.lock("account-write-" + account["id"]):
                latest = self.store.read("account-" + account["id"])
                if latest:
                    account["selected_model"] = latest.get("selected_model")
                show_welcome = account["status"] == "connected" and not (latest or {}).get(
                    "plan_welcome_shown"
                )
                account["plan_welcome_shown"] = bool(
                    (latest or {}).get("plan_welcome_shown") or show_welcome
                )
                account["session_version"] = str(uuid.uuid4())
                if account["status"] != "connected":
                    for key in ["access_token", "refresh_token"]:
                        account.pop(key, None)
                self.store.save_account(account)
        # A verified identity without plan scope must not leave another account active
        # and accidentally charge that earlier account on the next default request.
        self.store.select(account["id"])
        return {**public_account(account), "show_plan_welcome": show_welcome}

    def require_account(self, account_id):
        local_only(self.settings)
        account = self.store.account(account_id)
        status = account.get("status")
        if status == "usage_limited":
            raise DomainError(
                "CHATGPT_USAGE_LIMIT",
                "Review ChatGPT usage settings, then explicitly resume",
                429,
                details={"manage_usage_url": MANAGE_USAGE},
            )
        if status != "connected" or PLAN_SCOPE not in account.get("scopes", []):
            raise DomainError(
                "CHATGPT_SIGN_IN_REQUIRED",
                "Connect this ChatGPT account and authorize plan usage",
                401,
            )
        return account

    async def access_token(self, account_id):
        account = self.require_account(account_id)
        if account["expires_at"] > time.time() + 30:
            return account["access_token"]
        async with self.store.refresh_lock(account_id):
            account = self.require_account(account_id)  # Another process may have renewed it.
            if account["expires_at"] > time.time() + 30:
                return account["access_token"]
            earliest = account.get("earliest_refresh_at")
            if isinstance(earliest, (float, int)) and earliest > time.time():
                raise DomainError(
                    "CHATGPT_REFRESH_DEFERRED", "OpenAI has deferred token renewal; try later", 503
                )
            response = await self.http(
                "POST",
                TOKEN,
                data={
                    "grant_type": "refresh_token",
                    "client_id": account["client_id"],
                    "refresh_token": account["refresh_token"],
                    "resource": RESOURCE,
                },
            )
            if response.status_code != 200:
                try:
                    body = response.json()
                    code = body.get("error")
                    code = code.get("code") if isinstance(code, dict) else code
                except (ValueError, AttributeError):
                    code = None
                if code == "invalid_client":
                    raise DomainError(
                        "CHATGPT_CLIENT_INVALID",
                        "OpenAI rejected this OAuth client registration",
                        400,
                    )
                if code in TERMINAL_REFRESH:
                    self.clear(account_id, "needs_sign_in")
                    raise DomainError(
                        "CHATGPT_SESSION_EXPIRED",
                        "Session expired or was revoked; sign in again",
                        401,
                    )
                raise DomainError(
                    "CHATGPT_REFRESH_FAILED",
                    "Token renewal failed; credentials were preserved",
                    503,
                )
            try:
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("Invalid token response")
            except ValueError as error:
                raise DomainError(
                    "CHATGPT_TOKEN_INVALID", "OpenAI token response did not validate", 503
                ) from error
            if data.get("id_token"):
                await self.identity(
                    data["id_token"], account["client_id"], subject=account["subject"]
                )
            with self.store.lock("account-write-" + str(account_id)):
                latest = self.store.account(account_id)
                renewed = {**latest, **self.token_fields(data, account)}
                if renewed["status"] != "connected":
                    for key in ["access_token", "refresh_token"]:
                        renewed.pop(key, None)
                elif latest["status"] != "connected":
                    renewed["status"] = latest["status"]
                self.store.save_account(renewed)  # Access/refresh/expiry/scopes rotate atomically.
            self.require_account(account_id)
            return renewed["access_token"]

    def clear(self, account_id, status):
        with self.store.lock("account-write-" + str(account_id)):
            account = self.store.account(account_id)
            for key in ["access_token", "refresh_token", "id_token"]:
                account.pop(key, None)
            account["status"] = status
            self.store.save_account(account)

    def upstream_error(self, account_id, status, body, request_id=None):
        error = body.get("error", {}) if isinstance(body, dict) else {}
        code = error.get("code") if isinstance(error, dict) else None
        param = error.get("param") if isinstance(error, dict) else None
        mapping = {
            "subscription_sharing_usage_limit_exceeded": (
                "CHATGPT_USAGE_LIMIT",
                429,
                "usage_limited",
            ),
            "subscription_sharing_user_not_eligible": ("CHATGPT_NOT_ELIGIBLE", 403, "ineligible"),
            "subscription_sharing_usage_unavailable": ("CHATGPT_USAGE_UNAVAILABLE", 503, None),
            "subscription_sharing_user_unavailable": ("CHATGPT_USAGE_UNAVAILABLE", 503, None),
            "subscription_sharing_unsupported_capability": (
                "CHATGPT_UNSUPPORTED_CAPABILITY",
                400,
                None,
            ),
            "subscription_sharing_route_not_supported": ("CHATGPT_ROUTE_NOT_SUPPORTED", 403, None),
            "subscription_sharing_invalid_user": ("CHATGPT_SESSION_INVALID", 401, "needs_sign_in"),
            "chatpass_v2_scope_not_authorized": ("CHATGPT_PERMISSION_DENIED", 403, "plan_disabled"),
            "chatpass_v2_invalid_authorization_context": (
                "CHATGPT_PERMISSION_DENIED",
                403,
                "plan_disabled",
            ),
        }
        internal, http_status, account_status = mapping.get(
            code,
            (
                "CHATGPT_SESSION_INVALID" if status == 401 else "CHATGPT_REQUEST_REJECTED",
                status if 400 <= status < 600 else 502,
                "needs_sign_in" if status == 401 else None,
            ),
        )
        if status == 429 and not code:
            internal, http_status, account_status = "CHATGPT_USAGE_LIMIT", 429, "usage_limited"
        if account_status:
            with self.store.lock("account-write-" + str(account_id)):
                account = self.store.account(account_id)
                account["status"] = account_status
                self.store.save_account(account)
        # Preserve shape/code/status/request ID without copying arbitrary sensitive detail text.
        details = {
            "upstream_status": status,
            "upstream_code": code if isinstance(code, str) else None,
            "upstream_param": param if isinstance(param, str) else None,
            "request_id": request_id,
            "upstream_body_shape": "error"
            if error
            else "detail"
            if isinstance(body, dict) and "detail" in body
            else "unknown",
            "manage_usage_url": MANAGE_USAGE,
        }
        return DomainError(
            internal,
            "ChatGPT-plan request stopped; review account access or usage settings",
            http_status,
            details=details,
        )

    async def models(self, account_id):
        token = await self.access_token(account_id)
        response = await self.http(
            "GET", RESOURCE + "/models", headers={"Authorization": "Bearer " + token}
        )
        if response.status_code != 200:
            try:
                body = response.json()
            except ValueError:
                body = {}
            raise self.upstream_error(
                account_id, response.status_code, body, response.headers.get("x-request-id")
            )
        try:
            rows = response.json()["models"]
            if not isinstance(rows, list) or any(not isinstance(m, dict) for m in rows):
                raise ValueError("Invalid model catalog")
            return [
                {"slug": m["slug"], "display_name": m["display_name"]}
                for m in rows
                if m.get("visibility") == "list"
                and isinstance(m.get("slug"), str)
                and isinstance(m.get("display_name"), str)
            ]
        except (KeyError, TypeError, ValueError) as error:
            raise DomainError(
                "CHATGPT_MODELS_INVALID", "Account model catalog did not validate", 502
            ) from error

    async def select_model(self, account_id, model):
        if model not in {m["slug"] for m in await self.models(account_id)}:
            raise DomainError(
                "CHATGPT_MODEL_UNAVAILABLE", "Select a model available to this account", 422
            )
        with self.store.lock("account-write-" + str(account_id)):
            account = self.store.account(account_id)
            account["selected_model"] = model
            self.store.save_account(account)
        return public_account(account)

    async def sign_out(self, account_id):
        local_only(self.settings)
        async with self.store.refresh_lock(account_id):
            account = self.store.account(account_id)
            confirmed = not account.get("refresh_token")
            if not confirmed:
                try:
                    metadata = await self.discovery()
                    response = await self.http(
                        "POST",
                        metadata["revocation_endpoint"],
                        data={
                            "token": account["refresh_token"],
                            "token_type_hint": "refresh_token",
                            "client_id": account["client_id"],
                        },
                    )
                    confirmed = response.status_code == 200
                except DomainError:
                    confirmed = False
            self.clear(account_id, "signed_out")
        return {
            "signed_out": True,
            "remote_revocation_confirmed": confirmed,
            "manage_usage_url": MANAGE_USAGE,
        }
