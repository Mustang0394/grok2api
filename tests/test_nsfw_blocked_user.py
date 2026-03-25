import asyncio

from app.core.exceptions import UpstreamException
from app.services.grok.batch_services.nsfw import NSFWService
from app.services.token.manager import TokenManager
from app.services.token.models import TokenInfo, TokenStatus
from app.services.token.pool import TokenPool


class _DummySession:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_nsfw_blocked_user_marks_token_expired(monkeypatch):
    def fake_get_config(key, default=None):
        overrides = {
            "nsfw.batch_size": 1,
            "nsfw.concurrent": 1,
            "proxy.browser": None,
        }
        return overrides.get(key, default)

    async def fake_accept_tos(session, token):
        return None

    async def fake_set_birth(session, token):
        return None

    async def fake_nsfw_mgmt(session, token):
        raise UpstreamException(
            message="blocked user",
            details={
                "status": 403,
                "grpc_status": 7,
                "grpc_message": (
                    "User is blocked: Bot abuse "
                    "[WKE=unauthorized:blocked-user]"
                ),
            },
        )

    monkeypatch.setattr("app.services.grok.batch_services.nsfw.get_config", fake_get_config)
    monkeypatch.setattr(
        "app.services.grok.batch_services.nsfw.ResettableSession", _DummySession
    )
    monkeypatch.setattr(
        "app.services.grok.batch_services.nsfw.AcceptTosReverse.request",
        fake_accept_tos,
    )
    monkeypatch.setattr(
        "app.services.grok.batch_services.nsfw.SetBirthReverse.request",
        fake_set_birth,
    )
    monkeypatch.setattr(
        "app.services.grok.batch_services.nsfw.NsfwMgmtReverse.request",
        fake_nsfw_mgmt,
    )

    async def _run():
        mgr = TokenManager()
        monkeypatch.setattr(mgr, "_schedule_save", lambda: None)

        pool = TokenPool("ssoBasic")
        pool.add(TokenInfo(token="tok_test"))
        mgr.pools = {"ssoBasic": pool}

        result = await NSFWService.batch(["tok_test"], mgr)
        token = mgr.pools["ssoBasic"].get("tok_test")

        assert token is not None
        assert token.status == TokenStatus.EXPIRED
        assert token.last_fail_reason == "unauthorized:blocked-user"
        assert token.fail_count == 1
        assert result["tok_test"]["success"] is False
        assert result["tok_test"]["http_status"] == 403

    asyncio.run(_run())
