"""Tests for the TinyFish search provider (_search_tinyfish)."""

import pytest
import pytest_asyncio
import httpx
import respx
from unittest.mock import AsyncMock, patch

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

SEARCH_URL = "https://api.search.tinyfish.ai/"
FETCH_URL = "https://api.fetch.tinyfish.ai/"

# Build a canonical 10-result payload (TinyFish always returns 10)
def _make_search_payload(n: int = 10) -> dict:
    return {
        "query": "test query",
        "total_results": n,
        "page": 1,
        "results": [
            {
                "position": i,
                "site_name": f"site{i}.com",
                "title": f"Result Title {i}",
                "snippet": f"Snippet for result {i}.",
                "url": f"https://site{i}.com/page",
            }
            for i in range(1, n + 1)
        ],
    }


def _make_fetch_payload(urls: list[str], content: str = "A" * 600) -> dict:
    return {
        "results": [
            {
                "url": url,
                "final_url": url,
                "title": "Page Title",
                "description": "desc",
                "language": "en",
                "text": content,
                "latency_ms": 200.0,
                "format": "markdown",
            }
            for url in urls
        ],
        "errors": [],
    }


# ---------------------------------------------------------------------------
# Test 1 – basic search, verify sliced to max_results
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_basic(monkeypatch):
    """Search API returns 10 results; function must slice to max_results=5."""
    monkeypatch.setenv("TINYFISH_API_KEY", "test-key")

    with respx.mock(assert_all_called=False) as mock:
        mock.get(SEARCH_URL).mock(
            return_value=httpx.Response(200, json=_make_search_payload(10))
        )

        from backend.search import _search_tinyfish

        result = await _search_tinyfish("test query", max_results=5, full_content_results=0)

    # Should contain exactly 5 results, no more
    assert "Result 5:" in result
    assert "Result 6:" not in result


# ---------------------------------------------------------------------------
# Test 2 – full content fetch via TinyFish Fetch API (batch)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_full_content_fetch(monkeypatch):
    """Fetch API is called in batch and content appears in output."""
    monkeypatch.setenv("TINYFISH_API_KEY", "test-key")

    fetch_urls = [f"https://site{i}.com/page" for i in range(1, 4)]
    fetch_payload = _make_fetch_payload(fetch_urls, content="Fetched content. " * 40)

    with respx.mock(assert_all_called=False) as mock:
        mock.get(SEARCH_URL).mock(
            return_value=httpx.Response(200, json=_make_search_payload(10))
        )
        mock.post(FETCH_URL).mock(
            return_value=httpx.Response(200, json=fetch_payload)
        )

        from backend.search import _search_tinyfish

        result = await _search_tinyfish("test query", max_results=5, full_content_results=3)

    # At least the first result should have content (not just Summary:)
    assert "Content:" in result


# ---------------------------------------------------------------------------
# Test 3 – fallback to Jina Reader for URLs in errors[]
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_fetch_fallback_to_jina(monkeypatch):
    """When Fetch API returns a URL in errors[], _fetch_with_jina is called for it."""
    monkeypatch.setenv("TINYFISH_API_KEY", "test-key")

    # Only url for result 1 (index 0) succeeds; result 2 (index 1) fails
    success_url = "https://site1.com/page"
    failed_url = "https://site2.com/page"

    fetch_payload = {
        "results": [
            {
                "url": success_url,
                "final_url": success_url,
                "title": "Page",
                "description": "desc",
                "language": "en",
                "text": "Fetched content. " * 40,
                "latency_ms": 100.0,
                "format": "markdown",
            }
        ],
        "errors": [
            {"url": failed_url, "error": "fetch_failed"}
        ],
    }

    with respx.mock(assert_all_called=False) as mock:
        mock.get(SEARCH_URL).mock(
            return_value=httpx.Response(200, json=_make_search_payload(10))
        )
        mock.post(FETCH_URL).mock(
            return_value=httpx.Response(200, json=fetch_payload)
        )

        jina_mock = AsyncMock(return_value="Jina fallback content. " * 30)

        with patch("backend.search._fetch_with_jina", jina_mock):
            from backend.search import _search_tinyfish

            result = await _search_tinyfish("test query", max_results=5, full_content_results=2)

    # Jina must have been called exactly once (for the failed URL)
    jina_mock.assert_awaited_once()
    called_url = jina_mock.await_args[0][0]
    assert called_url == failed_url


# ---------------------------------------------------------------------------
# Test 4 – missing API key returns system note
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_missing_api_key(monkeypatch):
    """No TINYFISH_API_KEY in env → returns system note string."""
    monkeypatch.delenv("TINYFISH_API_KEY", raising=False)

    from backend.search import _search_tinyfish

    result = await _search_tinyfish("test query")

    assert "System Note" in result
    assert "TinyFish" in result
    assert "key" in result.lower()


# ---------------------------------------------------------------------------
# Test 5 – API returns 401, returns system note
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_api_error(monkeypatch):
    """401 from Search API → returns system note string."""
    monkeypatch.setenv("TINYFISH_API_KEY", "bad-key")

    with respx.mock(assert_all_called=False) as mock:
        mock.get(SEARCH_URL).mock(
            return_value=httpx.Response(401, json={"error": "Unauthorized"})
        )

        from backend.search import _search_tinyfish

        result = await _search_tinyfish("test query")

    assert "System Note" in result
    assert "TinyFish" in result


# ---------------------------------------------------------------------------
# Test 6 – full_content_results=0, Fetch API never called
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_no_full_content(monkeypatch):
    """When full_content_results=0, Fetch API must not be called."""
    monkeypatch.setenv("TINYFISH_API_KEY", "test-key")

    with respx.mock(assert_all_called=True) as mock:
        mock.get(SEARCH_URL).mock(
            return_value=httpx.Response(200, json=_make_search_payload(10))
        )
        # Do NOT register FETCH_URL — if it's called, respx will raise

        from backend.search import _search_tinyfish

        result = await _search_tinyfish("test query", max_results=5, full_content_results=0)

    # Results should use Summary: not Content:
    assert "Summary:" in result
    assert "Content:" not in result
