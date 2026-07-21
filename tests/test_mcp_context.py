from pathlib import Path

import pytest

from loom_archiver import graphql
from loom_archiver.mcp import context


def test_load_config_reads_dest_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_ARCHIVER_DEST", str(tmp_path / "archive"))
    cfg = context.load_config()
    assert cfg.dest_root == tmp_path / "archive"


def test_load_config_raises_when_env_unset(monkeypatch):
    monkeypatch.delenv("LOOM_ARCHIVER_DEST", raising=False)
    with pytest.raises(context.ConfigError) as exc_info:
        context.load_config()
    assert "LOOM_ARCHIVER_DEST" in str(exc_info.value)


def test_load_config_raises_when_env_empty(monkeypatch):
    monkeypatch.setenv("LOOM_ARCHIVER_DEST", "")
    with pytest.raises(context.ConfigError) as exc_info:
        context.load_config()
    assert "LOOM_ARCHIVER_DEST" in str(exc_info.value)


class FakeHttpClient:
    def __init__(self, *a, **k):
        self.closed = False

    def close(self):
        self.closed = True


class FakeApi:
    def __init__(self, cookie_header, loom_web_version):
        self.cookie_header = cookie_header
        self.loom_web_version = loom_web_version
        self.closed = False

    def close(self):
        self.closed = True


def _cfg(tmp_path):
    from loom_archiver.config import Config
    return Config.default(tmp_path / "archive")


def test_open_session_closes_both_on_normal_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(context.graphql, "load_cookie_header", lambda p: "ck=1")
    monkeypatch.setattr(context.graphql, "LoomGraphQL", FakeApi)
    monkeypatch.setattr(context.httpx, "Client", FakeHttpClient)

    cfg = _cfg(tmp_path)
    with context.open_session(cfg) as (api, http):
        assert isinstance(api, FakeApi)
        assert isinstance(http, FakeHttpClient)
        assert not api.closed
        assert not http.closed

    assert api.closed
    assert http.closed


def test_open_session_closes_both_when_body_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(context.graphql, "load_cookie_header", lambda p: "ck=1")
    monkeypatch.setattr(context.graphql, "LoomGraphQL", FakeApi)
    monkeypatch.setattr(context.httpx, "Client", FakeHttpClient)

    cfg = _cfg(tmp_path)
    captured = {}
    with pytest.raises(RuntimeError):
        with context.open_session(cfg) as (api, http):
            captured["api"] = api
            captured["http"] = http
            raise RuntimeError("boom")

    assert captured["api"].closed
    assert captured["http"].closed


def test_open_session_propagates_auth_error(monkeypatch, tmp_path):
    def boom(path):
        raise graphql.AuthError("no session")

    monkeypatch.setattr(context.graphql, "load_cookie_header", boom)
    monkeypatch.setattr(context.graphql, "LoomGraphQL", FakeApi)
    monkeypatch.setattr(context.httpx, "Client", FakeHttpClient)

    cfg = _cfg(tmp_path)
    with pytest.raises(graphql.AuthError):
        with context.open_session(cfg):
            pass


def test_auth_error_payload_reuses_graphql_constant():
    payload = context.auth_error_payload()
    assert payload["error"] == "not_authenticated"
    assert "loom-archiver auth" in payload["message"]
    assert graphql.AUTH_ACTION in payload["message"]


def test_auth_error_payload_includes_exception_text():
    payload = context.auth_error_payload(graphql.AuthError("session expired badly"))
    assert "session expired badly" in payload["message"]
    assert graphql.AUTH_ACTION in payload["message"]


def test_auth_error_payload_does_not_claim_expiry_for_first_run():
    """A first-run/never-signed-in user has no session to expire; saying so is wrong."""
    payload = context.auth_error_payload()
    assert "expired" not in payload["message"].lower()


class FakeHttpClientThatRaisesOnClose:
    def __init__(self, *a, **k):
        self.close_attempted = False

    def close(self):
        self.close_attempted = True
        raise RuntimeError("http close boom")


class FakeApiThatRaisesOnClose:
    def __init__(self, cookie_header, loom_web_version):
        self.cookie_header = cookie_header
        self.loom_web_version = loom_web_version
        self.close_attempted = False

    def close(self):
        self.close_attempted = True
        raise RuntimeError("api close boom")


def test_open_session_closes_both_even_if_http_close_raises(monkeypatch, tmp_path):
    from loom_archiver.config import Config

    monkeypatch.setattr(context.graphql, "load_cookie_header", lambda p: "ck=1")
    monkeypatch.setattr(context.graphql, "LoomGraphQL", FakeApiThatRaisesOnClose)
    monkeypatch.setattr(context.httpx, "Client", FakeHttpClientThatRaisesOnClose)

    cfg = Config.default(tmp_path / "archive")
    captured = {}
    # Both close() calls raise. Python's nested try/finally surfaces the
    # *last* exception raised (api's, since it's in the outer finally),
    # chaining http's as __context__ -- but the key guarantee under test is
    # that BOTH close attempts happen and nothing is silently swallowed.
    with pytest.raises(RuntimeError, match="api close boom") as excinfo:
        with context.open_session(cfg) as (api, http):
            captured["api"] = api
            captured["http"] = http

    assert captured["http"].close_attempted
    assert captured["api"].close_attempted
    assert isinstance(excinfo.value.__context__, RuntimeError)
    assert "http close boom" in str(excinfo.value.__context__)
