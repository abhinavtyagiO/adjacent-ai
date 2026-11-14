"""OAuth and streamed inference contracts: generated test keys/MockTransport only."""

import asyncio
import base64
import hashlib
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from app.chatgpt.service import ISSUER, RESOURCE, SCOPES, ChatGPTService
from app.chatgpt.store import CredentialStore
from app.errors import DomainError
from app.intelligence import metrics, run_pipeline
from app.llm.base import build_provider
from app.llm.chatgpt_provider import ChatGPTProvider
from app.schemas import FactualOffering, FactualProfile, FactualResult, MarketingResult


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
async def chatgpt(settings, tmp_path, signing_key, extraction_result):
    settings.chatgpt_auth_dir = tmp_path / "credentials"
    settings.llm_provider = "openai_chatgpt"
    settings.api_access_token = SecretStr("local-access-test-only")
    settings.factual_model = settings.marketing_model = None
    public = jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    public["kid"] = "test-key"
    h = SimpleNamespace(
        settings=settings,
        store=CredentialStore(settings.chatgpt_auth_dir),
        calls=[],
        nonce="",
        subject="subject-a",
        issued="oaiapp_test_a",
        scope=SCOPES,
        claims={},
        token_error=None,
        delay=False,
        token_calls=0,
        revoke_status=200,
        token_value="test-access-a",
        catalog=None,
        stream_error=None,
        stream_status=200,
        end="response.completed",
        error_stage=None,
        malformed=False,
        invalid_signature=False,
    )
    data = {
        k: v
        for k, v in extraction_result.profile.model_dump().items()
        if k in FactualProfile.model_fields
    }
    data["offerings"] = [
        {k: v for k, v in o.items() if k in FactualOffering.model_fields} for o in data["offerings"]
    ]
    h.facts = FactualResult(profile=data, evidence=extraction_result.evidence)
    h.marketing = MarketingResult(
        creative_context={"creative_angles": ["Show a team handoff"]},
        evidence=[
            {
                "path": "/creative_context/creative_angles/0",
                "origin": "INFERRED",
                "reasoning": "Collaboration context supports a tentative creative idea.",
                "citations": [
                    {
                        "source_url": "https://acme.example/",
                        "quote": "Acme makes collaboration software.",
                    }
                ],
            }
        ],
    )

    def identity():
        claims = {
            "iss": ISSUER,
            "aud": h.issued,
            "sub": h.subject,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
            "nonce": h.nonce,
            "email": "same@example.test",
            **h.claims,
        }
        return jwt.encode(
            claims,
            rsa.generate_private_key(public_exponent=65537, key_size=2048)
            if h.invalid_signature
            else signing_key,
            algorithm="RS256",
            headers={"kid": "test-key"},
        )

    async def handler(request):
        h.calls.append(request)
        url = str(request.url)
        if url == ISSUER + "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "jwks_uri": ISSUER + "/keys",
                    "revocation_endpoint": ISSUER + "/revoke",
                },
            )
        if url == ISSUER + "/keys":
            return httpx.Response(200, json={"keys": [public]})
        if url == ISSUER + "/api/accounts/oauth/token":
            h.token_calls += 1
            form = parse_qs(request.content.decode())
            assert form["client_id"] == [h.issued] and form["resource"] == [RESOURCE]
            assert "client_secret" not in form
            if form["grant_type"] == ["refresh_token"]:
                assert "scope" not in form
                if h.delay:
                    await asyncio.sleep(0.05)
            if h.token_error:
                return httpx.Response(400, json={"error": h.token_error})
            return httpx.Response(
                200,
                json={
                    "id_token": identity(),
                    "access_token": h.token_value,
                    "refresh_token": "test-refresh-" + str(h.token_calls),
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "scope": h.scope,
                },
            )
        if url == ISSUER + "/revoke":
            return httpx.Response(h.revoke_status)
        if url == RESOURCE + "/models":
            return httpx.Response(
                200,
                json={
                    "models": h.catalog
                    or [
                        {
                            "slug": "account-model",
                            "display_name": "Account model",
                            "visibility": "list",
                        },
                        {"slug": "hidden-model", "display_name": "Hidden", "visibility": "hide"},
                    ]
                },
            )
        if url == RESOURCE + "/responses":
            if h.stream_status != 200:
                return httpx.Response(
                    h.stream_status,
                    json={"detail": "PRIVATE_DETAIL"},
                    headers={"x-request-id": "req_mock"},
                )
            body = json.loads(request.content)
            value = h.facts if body["text"]["format"]["name"] == "FactualResult" else h.marketing
            text = "{broken" if h.malformed else value.model_dump_json()
            events = [
                {"type": "response.reasoning_text.delta", "delta": "PRIVATE_REASONING"},
                {"type": "response.output_text.delta", "delta": text},
            ]
            if h.stream_error and (
                not h.error_stage or body["text"]["format"]["name"] == h.error_stage
            ):
                events.append(
                    {
                        "type": "response.failed",
                        "response": {
                            "status": "failed",
                            "error": {"code": h.stream_error, "param": "model"},
                        },
                    }
                )
            elif h.end:
                events.append(
                    {
                        "type": h.end,
                        "response": {
                            "status": "completed"
                            if h.end == "response.completed"
                            else "incomplete",
                            "usage": {
                                "input_tokens": 100,
                                "output_tokens": 30,
                                "input_tokens_details": {"cached_tokens": 5},
                            },
                        },
                    }
                )
            return httpx.Response(
                200,
                text="".join("data: " + json.dumps(e) + "\n\n" for e in events),
                headers={"content-type": "text/event-stream", "x-request-id": "req_mock"},
            )
        raise AssertionError("Unexpected outbound URL: " + url)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        h.service = ChatGPTService(settings, h.store, client)

        async def connect(account_id=None):
            result, cookie = h.service.begin(True, account_id)
            query = parse_qs(urlsplit(result["authorization_url"]).query)
            h.nonce = query["nonce"][0]
            h.pending = h.store.read(
                "pending-" + hashlib.sha256(query["state"][0].encode()).hexdigest()
            )
            account = await h.service.callback(query["state"][0], cookie, "test-code", h.issued)
            return account, query

        h.connect = connect

        async def provider():
            account, _ = await connect()
            await h.service.select_model(account["id"], "account-model")
            return ChatGPTProvider(settings, h.service).for_job(account["id"]), account

        h.provider = provider
        yield h


async def test_pkce_dynamic_registration_reauthorization_and_identity(chatgpt):
    account, q = await chatgpt.connect()
    assert q["client_id"] == ["dynamic_agent_client"] and q["agent_name_hint"] == ["Adjacent AI"]
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(chatgpt.pending["verifier"].encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert q["code_challenge"] == [expected] and q["code_challenge_method"] == ["S256"]
    assert q["ext_agent_host_id"] == [chatgpt.store.host_id()]
    again, q = await chatgpt.connect(account["id"])
    assert again["id"] == account["id"] and q["client_id"] == [chatgpt.issued]
    assert "agent_name_hint" not in q and "id_token_hint" not in q
    assert account["plan_usage_enabled"] and "access_token" not in account
    assert account["show_plan_welcome"] and not again["show_plan_welcome"]


async def test_explicit_consent_and_one_use_browser_state(chatgpt):
    with pytest.raises(DomainError):
        chatgpt.service.begin(False)
    result, cookie = chatgpt.service.begin(True)
    state = parse_qs(urlsplit(result["authorization_url"]).query)["state"][0]
    with pytest.raises(DomainError):
        await chatgpt.service.callback(state, "wrong", "code", chatgpt.issued)
    assert not chatgpt.calls
    with pytest.raises(DomainError) as denied:
        await chatgpt.service.callback(state, cookie, error="access_denied")
    assert denied.value.code == "CHATGPT_CONSENT_DENIED"
    with pytest.raises(DomainError):
        await chatgpt.service.callback(state, cookie, "code", chatgpt.issued)
    assert not chatgpt.calls


@pytest.mark.parametrize(
    "claims",
    [
        {"nonce": "wrong"},
        {"iss": "https://evil.example"},
        {"aud": "other"},
        {"exp": 1},
        {"sub": ""},
    ],
)
async def test_id_token_validation_rejects_wrong_identity(chatgpt, claims):
    chatgpt.claims = claims
    with pytest.raises(DomainError) as error:
        await chatgpt.connect()
    assert error.value.code == "CHATGPT_IDENTITY_INVALID" and not chatgpt.store.accounts()


async def test_identity_only_permission_never_enables_inference(chatgpt):
    chatgpt.scope = "openid profile email"
    account, _ = await chatgpt.connect()
    assert not account["plan_usage_enabled"] and chatgpt.store.selected_id() == account["id"]
    with pytest.raises(DomainError):
        await chatgpt.service.models(account["id"])


async def test_client_binding_and_distinct_same_email_registrations(chatgpt):
    account, _ = await chatgpt.connect()
    chatgpt.claims = {"sub": "other-user"}
    with pytest.raises(DomainError):
        await chatgpt.connect(account["id"])
    assert chatgpt.store.account(account["id"])["subject"] == "subject-a"
    chatgpt.claims = {}
    chatgpt.issued = "oaiapp_other_workspace"
    second, _ = await chatgpt.connect()
    assert second["id"] != account["id"] and second["email"] == account["email"]


async def test_owner_only_encryption_and_stable_host(chatgpt):
    account, _ = await chatgpt.connect()
    assert chatgpt.store.root.stat().st_mode & 0o777 == 0o700
    for path in chatgpt.store.root.glob("*"):
        assert path.stat().st_mode & 0o777 == 0o600
        assert b"test-access-a" not in path.read_bytes()
    reloaded = CredentialStore(chatgpt.store.root)
    assert reloaded.host_id() == chatgpt.store.host_id()
    assert reloaded.account(account["id"])["access_token"] == "test-access-a"
    path = chatgpt.store.root / ("account-" + account["id"] + ".enc")
    path.chmod(0o644)
    with pytest.raises(DomainError):
        reloaded.account(account["id"])


async def test_concurrent_rotation_uses_one_refresh(chatgpt):
    account, _ = await chatgpt.connect()
    a = chatgpt.store.account(account["id"])
    a["expires_at"] = 0
    chatgpt.store.save_account(a)
    chatgpt.token_value = "renewed"
    chatgpt.delay = True
    other = ChatGPTService(
        chatgpt.settings, CredentialStore(chatgpt.store.root), chatgpt.service.client
    )
    tokens = await asyncio.gather(
        chatgpt.service.access_token(account["id"]), other.access_token(account["id"])
    )
    assert tokens == ["renewed"] * 2 and chatgpt.token_calls == 2
    assert chatgpt.store.account(account["id"])["refresh_token"] == "test-refresh-2"


@pytest.mark.parametrize("code", ["invalid_grant", "refresh_token_reused", "refresh_token_expired"])
async def test_terminal_refresh_preserves_registration_and_clears_tokens(chatgpt, code):
    account, _ = await chatgpt.connect()
    a = chatgpt.store.account(account["id"])
    a["expires_at"] = 0
    chatgpt.store.save_account(a)
    chatgpt.token_error = code
    with pytest.raises(DomainError) as error:
        await chatgpt.service.access_token(account["id"])
    assert error.value.code == "CHATGPT_SESSION_EXPIRED"
    a = chatgpt.store.account(account["id"])
    assert a["client_id"] == chatgpt.issued and "refresh_token" not in a


async def test_account_specific_catalog_filters_hidden_models(chatgpt):
    account, _ = await chatgpt.connect()
    assert await chatgpt.service.models(account["id"]) == [
        {"slug": "account-model", "display_name": "Account model"}
    ]
    assert chatgpt.calls[-1].headers["authorization"] == "Bearer test-access-a"
    with pytest.raises(DomainError):
        await chatgpt.service.select_model(account["id"], "hidden-model")


async def test_preview_stream_contract_and_private_reasoning_not_logged(chatgpt, bundle, caplog):
    provider, _ = await chatgpt.provider()
    result = await run_pipeline(provider, chatgpt.settings, bundle.documents[0].url, bundle)
    calls = [r for r in chatgpt.calls if r.url.path == "/v1/responses"]
    assert len(calls) == 2
    assert (
        result.profile.brand_identity.name == "Acme"
        and result.metadata["stages"]["marketing"] == "SUCCEEDED"
    )
    for r in calls:
        body = json.loads(r.content)
        assert body["stream"] is True and body["store"] is False and body["instructions"]
        assert (
            not {
                "max_output_tokens",
                "background",
                "metadata",
                "previous_response_id",
                "conversation",
                "temperature",
                "user",
            }
            & body.keys()
        )
        assert all(i["role"] != "system" for i in body["input"])
        assert r.headers["authorization"] == "Bearer test-access-a"
    assert "PRIVATE_REASONING" not in caplog.text + json.dumps(result.metadata)
    report = metrics(provider.usage_records, chatgpt.settings, 1)
    assert report["billing_source"] == "chatgpt_plan" and report["estimated_cost_usd"] is None


@pytest.mark.parametrize(
    "end,expected",
    [("", "CHATGPT_STREAM_INTERRUPTED"), ("response.incomplete", "LLM_INVALID_OUTPUT")],
)
async def test_stream_requires_completion(chatgpt, bundle, end, expected):
    provider, _ = await chatgpt.provider()
    chatgpt.end = end
    with pytest.raises(DomainError) as e:
        await provider.extract_facts(bundle.documents[0].url, bundle)
    assert e.value.code == expected
    assert len([r for r in chatgpt.calls if r.url.path == "/v1/responses"]) == 1


@pytest.mark.parametrize(
    "code,expected",
    [
        ("subscription_sharing_usage_limit_exceeded", "CHATGPT_USAGE_LIMIT"),
        ("subscription_sharing_user_not_eligible", "CHATGPT_NOT_ELIGIBLE"),
        ("subscription_sharing_usage_unavailable", "CHATGPT_USAGE_UNAVAILABLE"),
        ("chatpass_v2_scope_not_authorized", "CHATGPT_PERMISSION_DENIED"),
    ],
)
async def test_failure_after_valid_text_never_succeeds(chatgpt, bundle, code, expected):
    provider, _ = await chatgpt.provider()
    chatgpt.stream_error = code
    with pytest.raises(DomainError) as e:
        await provider.extract_facts(bundle.documents[0].url, bundle)
    assert e.value.code == expected and e.value.details["upstream_status"] == 200
    assert e.value.details["upstream_code"] == code and e.value.details["request_id"] == "req_mock"
    assert len([r for r in chatgpt.calls if r.url.path == "/v1/responses"]) == 1


@pytest.mark.parametrize("status", [401, 403, 429, 503])
async def test_http_admission_failure_never_copies_sensitive_detail(chatgpt, bundle, status):
    provider, _ = await chatgpt.provider()
    chatgpt.stream_status = status
    with pytest.raises(DomainError) as e:
        await provider.extract_facts(bundle.documents[0].url, bundle)
    assert e.value.status == status and e.value.details["upstream_body_shape"] == "detail"
    assert "PRIVATE_DETAIL" not in json.dumps(e.value.details)


async def test_hosted_usage_blocked_without_api_key_fallback(settings, tmp_path):
    settings.chatgpt_auth_dir = tmp_path / "hosted"
    settings.chatgpt_deployment_mode = "hosted"
    settings.openai_api_key = SecretStr("must-not-use")
    with pytest.raises(DomainError) as e:
        build_provider(settings)
    assert (
        e.value.code == "CHATGPT_HOSTED_APPROVAL_REQUIRED"
        and not settings.chatgpt_auth_dir.exists()
    )


@pytest.mark.parametrize("status", [200, 503])
async def test_revocation_and_local_signout(chatgpt, status):
    account, _ = await chatgpt.connect()
    chatgpt.revoke_status = status
    result = await chatgpt.service.sign_out(account["id"])
    assert result["remote_revocation_confirmed"] == (status == 200)
    assert chatgpt.store.account(account["id"])["status"] == "signed_out"
    assert "access_token" not in chatgpt.store.account(account["id"])


def configure_api(client, monkeypatch, h):
    from app.chatgpt.routes import get_chatgpt_service
    from app.config import get_settings

    monkeypatch.setenv("LLM_PROVIDER", "openai_chatgpt")
    monkeypatch.setenv("OPENAI_API_KEY_BILLING_ENABLED", "false")
    monkeypatch.setenv("API_ACCESS_TOKEN", "local-access-test-only")
    monkeypatch.setenv("CHATGPT_AUTH_DIR", str(h.store.root))
    get_settings.cache_clear()
    client.base_url = httpx.URL("http://127.0.0.1:8000")
    client.headers["Authorization"] = "Bearer local-access-test-only"
    client.app.dependency_overrides[get_chatgpt_service] = lambda: h.service
    monkeypatch.setattr("app.chatgpt.routes.get_chatgpt_service", lambda: h.service)


@pytest.mark.integration
async def test_oauth_api_cookie_protection_account_models_and_csrf(client, chatgpt, monkeypatch):
    configure_api(client, monkeypatch, chatgpt)
    assert client.get("/v1/chatgpt/connect").status_code == 200
    assert client.get("/v1/chatgpt/accounts", headers={"Authorization": "wrong"}).status_code == 401
    assert (
        client.post(
            "/v1/chatgpt/authorizations",
            json={"plan_usage_consent": True},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert (
        client.post("/v1/chatgpt/authorizations", json={"plan_usage_consent": False}).status_code
        == 400
    )
    start = client.post("/v1/chatgpt/authorizations", json={"plan_usage_consent": True})
    assert start.status_code == 200 and "HttpOnly" in start.headers["set-cookie"]
    query = parse_qs(urlsplit(start.json()["authorization_url"]).query)
    chatgpt.nonce = query["nonce"][0]
    result = client.get(
        "/v1/chatgpt/callback",
        params={"state": query["state"][0], "code": "test-code", "client_id": chatgpt.issued},
    )
    assert result.status_code == 200
    rows = client.get("/v1/chatgpt/accounts").json()
    account = rows["items"][0]
    assert "test-access-a" not in json.dumps(rows) and "refresh_token" not in json.dumps(rows)
    assert client.get("/v1/chatgpt/accounts/" + account["id"] + "/models").status_code == 200
    assert (
        client.put(
            "/v1/chatgpt/accounts/" + account["id"] + "/model", json={"model": "account-model"}
        ).status_code
        == 200
    )
    assert not any(r.url.path == "/v1/responses" for r in chatgpt.calls)


@pytest.mark.integration
async def test_job_account_binding_is_unchanged_when_active_account_switches(
    client, factory, chatgpt, monkeypatch, bundle
):
    from app.worker import ExtractionWorker
    from tests.conftest import FakeSource

    configure_api(client, monkeypatch, chatgpt)
    a, _ = await chatgpt.connect()
    await chatgpt.service.select_model(a["id"], "account-model")
    brand = client.post("/v1/brands", json={"website_url": bundle.documents[0].url}).json()["id"]
    job = client.post(f"/v1/brands/{brand}/extractions").json()
    assert job["chatgpt_account_id"] == a["id"] and job["factual_model"] == "account-model"
    assert not any(r.url.path == "/v1/responses" for r in chatgpt.calls)
    chatgpt.issued = "oaiapp_test_b"
    chatgpt.token_value = "test-access-b"
    b, _ = await chatgpt.connect()
    worker = ExtractionWorker(
        factory,
        chatgpt.settings,
        FakeSource(bundle),
        ChatGPTProvider(chatgpt.settings, chatgpt.service),
    )
    assert await worker.process_once()
    assert client.get(job["poll_url"]).json()["status"] == "SUCCEEDED"
    profile = client.get(f"/v1/brands/{brand}/profile").json()
    assert profile["provider"] == "openai_chatgpt" and profile["stages"]["marketing"] == "SUCCEEDED"
    assert profile["extraction_metrics"]["billing_source"] == "chatgpt_plan"
    assert chatgpt.store.selected_id() == b["id"]
    assert all(
        r.headers["authorization"] == "Bearer test-access-a"
        for r in chatgpt.calls
        if r.url.path == "/v1/responses"
    )


@pytest.mark.integration
async def test_marketing_plan_limit_preserves_draft_and_pauses_further_calls(
    client, factory, chatgpt, monkeypatch, bundle
):
    from app.worker import ExtractionWorker
    from tests.conftest import FakeSource

    configure_api(client, monkeypatch, chatgpt)
    provider, account = await chatgpt.provider()
    chatgpt.stream_error = "subscription_sharing_usage_limit_exceeded"
    chatgpt.error_stage = "MarketingResult"
    brand = client.post("/v1/brands", json={"website_url": bundle.documents[0].url}).json()["id"]
    job = client.post(f"/v1/brands/{brand}/extractions").json()
    assert await ExtractionWorker(
        factory, chatgpt.settings, FakeSource(bundle), provider
    ).process_once()
    assert client.get(job["poll_url"]).json()["status"] == "SUCCEEDED"
    profile = client.get(f"/v1/brands/{brand}/profile").json()
    assert profile["profile"]["brand_identity"]["name"] == "Acme"
    assert profile["stages"] == {"facts": "SUCCEEDED", "marketing": "FAILED"}
    assert profile["stage_errors"]["marketing"]["code"] == "CHATGPT_USAGE_LIMIT"
    assert (
        client.post(f"/v1/brands/{brand}/extractions", json={"replace_existing": True}).status_code
        == 429
    )
    assert len([r for r in chatgpt.calls if r.url.path == "/v1/responses"]) == 2
    assert client.post("/v1/chatgpt/accounts/" + account["id"] + "/resume").status_code == 200


@pytest.mark.integration
async def test_sign_in_and_model_selection_required_before_enqueue(
    client, chatgpt, monkeypatch, bundle
):
    configure_api(client, monkeypatch, chatgpt)
    brand = client.post("/v1/brands", json={"website_url": bundle.documents[0].url}).json()["id"]
    assert client.post(f"/v1/brands/{brand}/extractions").status_code == 401
    await chatgpt.connect()
    assert client.post(f"/v1/brands/{brand}/extractions").status_code == 422
    assert not any(r.url.path == "/v1/responses" for r in chatgpt.calls)


async def test_code_exchange_retry_retains_issued_registration_without_saving_identity(chatgpt):
    result, cookie = chatgpt.service.begin(True)
    state = parse_qs(urlsplit(result["authorization_url"]).query)["state"][0]
    chatgpt.token_error = "invalid_grant"
    with pytest.raises(DomainError):
        await chatgpt.service.callback(state, cookie, "bad-code", chatgpt.issued)
    retry, _ = chatgpt.service.begin(True, recovery_cookie=cookie)
    query = parse_qs(urlsplit(retry["authorization_url"]).query)
    assert query["client_id"] == [chatgpt.issued] and "agent_name_hint" not in query
    assert not chatgpt.store.accounts()


@pytest.mark.parametrize("code", ["server_error", "invalid_client"])
async def test_nonterminal_refresh_preserves_credentials(chatgpt, code):
    account, _ = await chatgpt.connect()
    a = chatgpt.store.account(account["id"])
    a["expires_at"] = 0
    chatgpt.store.save_account(a)
    chatgpt.token_error = code
    with pytest.raises(DomainError) as error:
        await chatgpt.service.access_token(account["id"])
    assert error.value.code == (
        "CHATGPT_CLIENT_INVALID" if code == "invalid_client" else "CHATGPT_REFRESH_FAILED"
    )
    assert chatgpt.store.account(account["id"])["refresh_token"] == a["refresh_token"]


async def test_bad_json_and_stream_budget_never_retry_inference(chatgpt, bundle):
    provider, _ = await chatgpt.provider()
    chatgpt.malformed = True
    with pytest.raises(DomainError) as error:
        await provider.extract_facts(bundle.documents[0].url, bundle)
    assert error.value.code == "LLM_INVALID_OUTPUT"
    assert provider.usage_records[0]["input_tokens"] == 100
    assert len([r for r in chatgpt.calls if r.url.path == "/v1/responses"]) == 1


async def test_missing_new_client_and_returned_client_mismatch_are_rejected(chatgpt):
    result, cookie = chatgpt.service.begin(True)
    state = parse_qs(urlsplit(result["authorization_url"]).query)["state"][0]
    with pytest.raises(DomainError):
        await chatgpt.service.callback(state, cookie, "code")
    assert not chatgpt.calls
    account, _ = await chatgpt.connect()
    result, cookie = chatgpt.service.begin(True, account["id"])
    state = parse_qs(urlsplit(result["authorization_url"]).query)["state"][0]
    before = len(chatgpt.calls)
    with pytest.raises(DomainError):
        await chatgpt.service.callback(state, cookie, "code", "different-client")
    assert len(chatgpt.calls) == before


async def test_vm_handoff_preserves_vm_host_and_prevents_duplicate_local_refresh(chatgpt, tmp_path):
    from app.chatgpt.transfer import export_session, import_session

    account, _ = await chatgpt.connect()
    source_host = chatgpt.store.host_id()
    bundle = tmp_path / "session.enc"
    phrase = "long-test-passphrase-for-transfer"
    await export_session(chatgpt.service, account["id"], bundle, phrase)
    assert b"test-access-a" not in bundle.read_bytes()
    assert chatgpt.store.account(account["id"])["status"] == "transferred"
    vm_settings = chatgpt.settings.model_copy(
        update={"chatgpt_auth_dir": tmp_path / "vm", "chatgpt_deployment_mode": "self_hosted"}
    )
    vm = ChatGPTService(vm_settings)
    vm_host = vm.store.host_id()
    assert vm_host != source_host
    imported = await import_session(vm, bundle, phrase)
    assert vm.store.host_id() == vm_host
    assert vm.store.account(imported)["ext_agent_host_id"] == vm_host
    assert vm.require_account(imported)["client_id"] == chatgpt.issued
    with pytest.raises(DomainError):
        await import_session(vm, bundle, "wrong-but-long-passphrase")


async def test_invalid_signature_cannot_register_an_account(chatgpt):
    chatgpt.invalid_signature = True
    with pytest.raises(DomainError) as error:
        await chatgpt.connect()
    assert error.value.code == "CHATGPT_IDENTITY_INVALID"
    assert not chatgpt.store.accounts()


async def test_missing_plan_scope_never_leaves_another_account_as_implicit_billing_owner(chatgpt):
    first, _ = await chatgpt.connect()
    chatgpt.issued = "oaiapp_disabled"
    chatgpt.scope = "openid profile email"
    second, _ = await chatgpt.connect()
    assert first["id"] != second["id"] and chatgpt.store.selected_id() == second["id"]
    with pytest.raises(DomainError):
        chatgpt.service.require_account(chatgpt.store.selected_id())


async def test_expired_oauth_state_is_rejected_without_exchanging_code(chatgpt):
    result, cookie = chatgpt.service.begin(True)
    state = parse_qs(urlsplit(result["authorization_url"]).query)["state"][0]
    name = "pending-" + hashlib.sha256(state.encode()).hexdigest()
    pending = chatgpt.store.read(name)
    pending["expires_at"] = 0
    chatgpt.store.write(name, pending)
    with pytest.raises(DomainError):
        await chatgpt.service.callback(state, cookie, "code", chatgpt.issued)
    assert not chatgpt.calls
