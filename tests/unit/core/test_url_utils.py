import pytest
from click import UsageError

from ggshield.core.url_utils import (
    api_to_dashboard_url,
    dashboard_to_api_url,
    is_saas_netloc,
    urljoin,
)


@pytest.mark.parametrize(
    ["api_url", "dashboard_url"],
    [
        ["https://api.gitguardian.com", "https://dashboard.gitguardian.com"],
        ["https://api.gitguardian.com/", "https://dashboard.gitguardian.com"],
        ["https://api.gitguardian.com/v1", "https://dashboard.gitguardian.com"],
        [
            "https://api.gitguardian.com/?foo=bar",
            "https://dashboard.gitguardian.com?foo=bar",
        ],
        # Self-hosted instance deployed under a gitguardian domain: it is NOT
        # SaaS, so /exposed is stripped instead of being rejected as a path.
        [
            "https://selfhosted.gitguardian.tech/exposed",
            "https://selfhosted.gitguardian.tech",
        ],
        # Prefixed SaaS host (api-<id>): host swap, no /exposed.
        [
            "https://api-1234.example.gitguardian.tech",
            "https://dashboard-1234.example.gitguardian.tech",
        ],
        ["https://example.com/exposed", "https://example.com"],
        ["https://example.com/exposed/", "https://example.com"],
        [
            "https://example.com/exposed/?foo=bar",
            "https://example.com?foo=bar",
        ],
        [
            "https://example.com/toto/exposed/?foo=bar",
            "https://example.com/toto?foo=bar",
        ],
        [
            "https://example.com/exposed/v1/?foo=bar",
            "https://example.com?foo=bar",
        ],
    ],
)
def test_api_to_dashboard_url(api_url, dashboard_url):
    assert api_to_dashboard_url(api_url) == dashboard_url


@pytest.mark.parametrize(
    ["dashboard_url", "api_url"],
    [
        ["https://dashboard.gitguardian.com", "https://api.gitguardian.com"],
        ["https://dashboard.gitguardian.com/", "https://api.gitguardian.com"],
        [
            "https://dashboard.gitguardian.com/?foo=bar",
            "https://api.gitguardian.com?foo=bar",
        ],
        # Self-hosted instance deployed under a gitguardian domain: it is NOT
        # SaaS, so /exposed is appended instead of swapping the host.
        [
            "https://selfhosted.gitguardian.tech",
            "https://selfhosted.gitguardian.tech/exposed",
        ],
        # Prefixed SaaS host (dashboard-<id>): host swap, no /exposed.
        [
            "https://dashboard-1234.example.gitguardian.tech",
            "https://api-1234.example.gitguardian.tech",
        ],
        ["https://example.com/", "https://example.com/exposed"],
        ["https://example.com/", "https://example.com/exposed"],
        [
            "https://example.com/?foo=bar",
            "https://example.com/exposed?foo=bar",
        ],
        [
            "https://example.com/toto?foo=bar",
            "https://example.com/toto/exposed?foo=bar",
        ],
    ],
)
def test_dashboard_to_api_url(dashboard_url, api_url):
    assert dashboard_to_api_url(dashboard_url) == api_url


@pytest.mark.parametrize(
    "api_url",
    ["https://api.gitguardian.com/exposed", "https://api.gitguardian.com/toto"],
)
def test_unexpected_path_api_url(api_url):
    with pytest.raises(UsageError, match="got an unexpected path"):
        api_to_dashboard_url(api_url)


@pytest.mark.parametrize(
    "dashboard_url",
    [
        "https://dashboard.gitguardian.com/exposed",
        "https://dashboard.gitguardian.com/toto",
    ],
)
def test_unexpected_path_dashboard_url(dashboard_url):
    with pytest.raises(UsageError, match="got an unexpected path"):
        api_to_dashboard_url(dashboard_url)


@pytest.mark.parametrize(
    ["netloc", "expected"],
    [
        # Bare dashboard/api hosts under a gitguardian domain are SaaS.
        ("dashboard.gitguardian.com", True),
        ("api.gitguardian.com", True),
        ("dashboard.staging.gitguardian.tech", True),
        # Prefixed dashboard-<id>/api-<id> first labels are SaaS too.
        ("dashboard-1234.example.gitguardian.tech", True),
        ("api-1234.example.gitguardian.tech", True),
        # Self-hosted instance under a gitguardian domain is NOT SaaS.
        ("selfhosted.gitguardian.tech", False),
        # "dashboard" must be a whole label, not a prefix of a longer word.
        ("dashboarding.gitguardian.tech", False),
        # Hosts outside the gitguardian domains are never SaaS.
        ("dashboard.example.com", False),
        ("example.com", False),
    ],
)
def test_is_saas_netloc(netloc, expected):
    assert is_saas_netloc(netloc) is expected


def test_urljoin_empty_base_raises_value_error():
    """
    GIVEN an empty base URL
    WHEN urljoin() is called
    THEN a ValueError is raised (not an IndexError)
    """
    with pytest.raises(ValueError, match="Base URL cannot be empty"):
        urljoin("", "path")


def test_urljoin_empty_segment_is_skipped():
    """
    GIVEN a urljoin call with an empty path segment
    WHEN urljoin() is called
    THEN the empty segment is skipped (not an IndexError)
    """
    assert urljoin("http://example.com", "", "path") == "http://example.com/path"
