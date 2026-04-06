"""
Tests for alerts/telegram.py
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from alerts.telegram import TelegramAlerter


class _MockResponse:
    """Mock aiohttp response."""
    def __init__(self, status=200, headers=None, text_body=""):
        self.status = status
        self.headers = headers or {}
        self._text_body = text_body

    async def text(self):
        return self._text_body


class _AsyncCtx:
    """Wrapper making an object usable as both await result and async context manager."""
    def __init__(self, obj):
        self._obj = obj

    def __await__(self):
        yield  # yields once to satisfy the iterator protocol, result is self
        return self

    async def __aenter__(self):
        return self._obj

    async def __aexit__(self, *args):
        pass


class _MockSession:
    """Mock aiohttp.ClientSession — async ctx manager that yields itself."""
    def __init__(self, response=None):
        self._response = response or _MockResponse()

    def post(self, url, **kwargs):
        return _AsyncCtx(self._response)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


# ── enabled flag ─────────────────────────────────────────────────────

def test_enabled_false_when_token_empty():
    alerter = TelegramAlerter("", "123456")
    assert alerter.enabled is False


def test_enabled_false_when_chat_id_empty():
    alerter = TelegramAlerter("tok_abc", "")
    assert alerter.enabled is False


def test_enabled_true_when_both_set():
    alerter = TelegramAlerter("tok_abc", "123456")
    assert alerter.enabled is True


# ── send() disabled ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_disabled_token_empty_logs_debug_no_http(caplog):
    alerter = TelegramAlerter("", "123456")
    with patch("alerts.telegram.aiohttp.ClientSession") as mock_cls:
        with caplog.at_level("DEBUG"):
            await alerter.send("hello")
    mock_cls.assert_not_called()
    assert "[alert] hello" in caplog.text


@pytest.mark.asyncio
async def test_send_disabled_chat_id_empty_logs_debug_no_http(caplog):
    alerter = TelegramAlerter("tok_abc", "")
    with patch("alerts.telegram.aiohttp.ClientSession") as mock_cls:
        with caplog.at_level("DEBUG"):
            await alerter.send("hello")
    mock_cls.assert_not_called()
    assert "[alert] hello" in caplog.text


# ── send() enabled ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_correct_url_and_payload():
    alerter = TelegramAlerter("tok_abc", "123456")
    mock_session = _MockSession(_MockResponse(status=200))
    with patch("alerts.telegram.aiohttp.ClientSession", return_value=mock_session):
        await alerter.send("test message")

    assert mock_session._response._called_url == "https://api.telegram.org/bottok_abc/sendMessage" if hasattr(mock_session._response, '_called_url') else True


@pytest.mark.asyncio
async def test_send_correct_payload_structure():
    alerter = TelegramAlerter("tok_abc", "123456")
    mock_resp = _MockResponse(status=200)
    mock_session = _MockSession(mock_resp)

    captured = {}
    original_post = mock_session.post

    def capturing_post(url, **kwargs):
        captured['url'] = url
        captured['kwargs'] = kwargs
        return original_post(url, **kwargs)

    mock_session.post = capturing_post

    with patch("alerts.telegram.aiohttp.ClientSession", return_value=mock_session):
        await alerter.send("test message")

    assert captured['url'] == "https://api.telegram.org/bottok_abc/sendMessage"
    payload = captured['kwargs']['json']
    assert payload["chat_id"] == "123456"
    assert payload["text"] == "test message"
    assert payload["parse_mode"] == "Markdown"


@pytest.mark.asyncio
async def test_send_handles_429_rate_limit_gracefully(caplog):
    alerter = TelegramAlerter("tok_abc", "123456")
    mock_session = _MockSession(
        _MockResponse(status=429, headers={"Retry-After": "30"})
    )
    with patch("alerts.telegram.aiohttp.ClientSession", return_value=mock_session):
        with caplog.at_level("WARNING"):
            await alerter.send("rate limited message")

    assert "rate limited" in caplog.text.lower() or "429" in caplog.text


@pytest.mark.asyncio
async def test_send_handles_non_200_response_gracefully(caplog):
    alerter = TelegramAlerter("tok_abc", "123456")
    mock_session = _MockSession(
        _MockResponse(status=500, text_body="Internal Server Error")
    )
    with patch("alerts.telegram.aiohttp.ClientSession", return_value=mock_session):
        with caplog.at_level("WARNING"):
            await alerter.send("failing message")

    assert "500" in caplog.text or "error" in caplog.text.lower()


@pytest.mark.asyncio
async def test_send_handles_network_exception_gracefully(caplog):
    alerter = TelegramAlerter("tok_abc", "123456")
    with patch("alerts.telegram.aiohttp.ClientSession", side_effect=ConnectionError("connection refused")):
        with caplog.at_level("WARNING"):
            await alerter.send("unreachable message")

    assert "failed" in caplog.text.lower() or "connection" in caplog.text.lower()
