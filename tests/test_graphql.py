import json
import httpx
import pytest
import respx
from loom_archiver import graphql


def test_load_cookie_header_filters_loom_domain(tmp_path):
    state = {"cookies": [
        {"name": "sid", "value": "abc", "domain": ".loom.com"},
        {"name": "other", "value": "zzz", "domain": ".example.com"},
    ]}
    p = tmp_path / "auth_state.json"
    p.write_text(json.dumps(state))
    header = graphql.load_cookie_header(p)
    assert "sid=abc" in header
    assert "other" not in header


@respx.mock
def test_execute_returns_data():
    respx.post("https://www.loom.com/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"ok": True}})
    )
    api = graphql.LoomGraphQL(cookie_header="sid=abc")
    data = api.execute("Op", "query Op { ok }", {})
    assert data == {"ok": True}


@respx.mock
def test_execute_raises_autherror_on_401():
    respx.post("https://www.loom.com/graphql").mock(
        return_value=httpx.Response(401, text="unauthorized")
    )
    api = graphql.LoomGraphQL(cookie_header="sid=abc")
    with pytest.raises(graphql.AuthError):
        api.execute("Op", "query Op { ok }", {})


@respx.mock
def test_execute_raises_autherror_on_graphql_unauthenticated():
    respx.post("https://www.loom.com/graphql").mock(
        return_value=httpx.Response(200, json={"errors": [{"extensions": {"code": "UNAUTHENTICATED"}}]})
    )
    api = graphql.LoomGraphQL(cookie_header="sid=abc")
    with pytest.raises(graphql.AuthError):
        api.execute("Op", "query Op { ok }", {})


@respx.mock
def test_execute_retries_transient_transport_error(monkeypatch):
    monkeypatch.setattr(graphql.time, "sleep", lambda s: None)
    route = respx.post("https://www.loom.com/graphql").mock(
        side_effect=[httpx.ReadTimeout("boom"), httpx.Response(200, json={"data": {"ok": True}})]
    )
    api = graphql.LoomGraphQL(cookie_header="sid=abc")
    data = api.execute("Op", "query Op { ok }", {})
    assert data == {"ok": True}
    assert route.call_count == 2  # retried once, then succeeded


@respx.mock
def test_execute_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr(graphql.time, "sleep", lambda s: None)
    respx.post("https://www.loom.com/graphql").mock(side_effect=httpx.ReadTimeout("always"))
    api = graphql.LoomGraphQL(cookie_header="sid=abc", max_retries=2)
    with pytest.raises(httpx.ReadTimeout):
        api.execute("Op", "query Op { ok }", {})


def test_missing_auth_state_message_does_not_claim_expiry(tmp_path):
    """A first run has no session to expire; saying so confuses new users."""
    with pytest.raises(graphql.AuthError) as excinfo:
        graphql.load_cookie_header(tmp_path / "auth_state.json")
    message = str(excinfo.value)
    assert "loom-archiver auth" in message
    assert "expired" not in message


def test_corrupt_auth_state_gives_a_clean_error(tmp_path):
    """An interrupted `auth` can leave a truncated file; don't traceback."""
    import pytest
    from loom_archiver import graphql
    state = tmp_path / "auth_state.json"
    state.write_text('{"cookies": [')  # truncated
    with pytest.raises(graphql.AuthError) as excinfo:
        graphql.load_cookie_header(state)
    assert "loom-archiver auth" in str(excinfo.value)
