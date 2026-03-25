"""
Reverse interface: rate limits.
"""

import orjson
from typing import Any
from curl_cffi.requests import AsyncSession

from app.core.logger import logger
from app.core.config import get_config
from app.core.proxy_pool import (
    build_http_proxies,
    get_current_proxy_from,
    rotate_proxy,
    should_rotate_proxy,
)
from app.core.exceptions import UpstreamException
from app.services.reverse.utils.headers import build_headers
from app.services.reverse.utils.retry import retry_on_status

RATE_LIMITS_API = "https://grok.com/rest/rate-limits"


def _classify_rate_limits_failure(
    status_code: int,
    content_type: str,
    server_header: str,
    resp_text: str,
) -> tuple[bool, bool]:
    body_lower = (resp_text or "").lower()
    content_type = (content_type or "").lower()
    server_header = (server_header or "").lower()

    is_cloudflare = "challenge-platform" in body_lower
    if "cloudflare" in server_header and "application/json" not in content_type:
        is_cloudflare = True

    is_token_expired = False
    if "application/json" in content_type:
        if (
            "unauthorized:blocked-user" in body_lower
            or "account:email-domain-rejected" in body_lower
        ):
            is_token_expired = True
        elif status_code == 401:
            auth_error_keywords = [
                "unauthorized",
                "not logged in",
                "unauthenticated",
                "bad-credentials",
            ]
            if any(k in body_lower for k in auth_error_keywords):
                is_token_expired = True

    return is_token_expired, is_cloudflare


class RateLimitsReverse:
    """/rest/rate-limits reverse interface."""

    @staticmethod
    async def request(session: AsyncSession, token: str) -> Any:
        """Fetch rate limits from Grok.

        Args:
            session: AsyncSession, the session to use for the request.
            token: str, the SSO token.

        Returns:
            Any: The response from the request.
        """
        try:
            headers = build_headers(
                cookie_token=token,
                content_type="application/json",
                origin="https://grok.com",
                referer="https://grok.com/",
            )

            payload = {
                "requestKind": "DEFAULT",
                "modelName": "grok-4-1-thinking-1129",
            }

            timeout = get_config("usage.timeout")
            browser = get_config("proxy.browser")
            active_proxy_key = None

            async def _do_request():
                nonlocal active_proxy_key
                active_proxy_key, proxy_url = get_current_proxy_from(
                    "proxy.base_proxy_url"
                )
                proxies = build_http_proxies(proxy_url)
                response = await session.post(
                    RATE_LIMITS_API,
                    headers=headers,
                    data=orjson.dumps(payload),
                    timeout=timeout,
                    proxies=proxies,
                    impersonate=browser,
                )

                if response.status_code != 200:
                    try:
                        resp_text = response.text
                    except Exception:
                        resp_text = "N/A"

                    server_header = response.headers.get("Server", "")
                    content_type = response.headers.get("Content-Type", "")
                    is_token_expired, is_cloudflare = _classify_rate_limits_failure(
                        response.status_code,
                        content_type,
                        server_header,
                        resp_text,
                    )

                    logger.error(
                        "RateLimitsReverse: Request failed, status={}, is_token_expired={}, is_cloudflare={}, Body: {}",
                        response.status_code,
                        is_token_expired,
                        is_cloudflare,
                        resp_text[:300],
                        extra={"error_type": "UpstreamException"},
                    )

                    raise UpstreamException(
                        message=f"RateLimitsReverse: Request failed, {response.status_code}",
                        details={
                            "status": response.status_code,
                            "body": resp_text,
                            "is_token_expired": is_token_expired,
                            "is_cloudflare": is_cloudflare,
                        },
                    )

                return response

            async def _on_retry(
                attempt: int, status_code: int, error: Exception, delay: float
            ):
                if active_proxy_key and should_rotate_proxy(status_code):
                    rotate_proxy(active_proxy_key)

            return await retry_on_status(_do_request, on_retry=_on_retry)

        except Exception as e:
            if isinstance(e, UpstreamException):
                status = None
                if e.details and isinstance(e.details, dict):
                    status = e.details.get("status")

                if status is None:
                    status = getattr(e, "status_code", None)

                logger.debug(
                    f"RateLimitsReverse: Upstream error caught: {str(e)}, status={status}"
                )
                raise

            import traceback

            error_details = traceback.format_exc()
            logger.error(
                f"RateLimitsReverse: Unexpected error, {type(e).__name__}: {str(e)}\n{error_details}"
            )
            raise UpstreamException(
                message=f"RateLimitsReverse: Request failed, {str(e)}",
                details={"status": 502, "error": str(e), "traceback": error_details},
            )


__all__ = ["RateLimitsReverse"]
