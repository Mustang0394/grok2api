from app.services.reverse.rate_limits import _classify_rate_limits_failure


def test_rate_limits_blocked_user_is_expired():
    is_token_expired, is_cloudflare = _classify_rate_limits_failure(
        403,
        "application/json",
        "cloudflare",
        '{"code":7,"message":"User is blocked [WKE=unauthorized:blocked-user]","details":[]}',
    )

    assert is_token_expired is True
    assert is_cloudflare is False


def test_rate_limits_cloudflare_challenge_is_not_expired():
    is_token_expired, is_cloudflare = _classify_rate_limits_failure(
        403,
        "text/html",
        "cloudflare",
        "<html>challenge-platform</html>",
    )

    assert is_token_expired is False
    assert is_cloudflare is True


def test_rate_limits_email_domain_rejected_is_expired():
    is_token_expired, is_cloudflare = _classify_rate_limits_failure(
        400,
        "application/json",
        "cloudflare",
        (
            '{"code":3,"message":"This email domain has been rejected '
            '[WKE=account:email-domain-rejected]","details":[]}'
        ),
    )

    assert is_token_expired is True
    assert is_cloudflare is False
