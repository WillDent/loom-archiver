import json
import stat

from loom_archiver import auth


class FakeContext:
    def __init__(self):
        self.saved_to = None

    def storage_state(self, path=None):
        self.saved_to = path
        return {"cookies": []}


def test_save_storage_state_writes_via_secure_write(tmp_path):
    """auth_state.json must be written 0600 via paths.secure_write, not
    context.storage_state(path=...) (which offers no permission guarantee)."""
    ctx = FakeContext()
    out = tmp_path / "auth_state.json"
    auth.save_storage_state(ctx, out)
    assert ctx.saved_to is None  # storage_state() called with no path
    assert json.loads(out.read_text()) == {"cookies": []}
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


class CookieContext:
    def __init__(self, cookies):
        self._cookies = cookies

    def cookies(self):
        return self._cookies


def test_has_session_cookie_false_for_anon_and_oauth_state():
    # The exact set that previously false-positived: anonymous + OAuth-flow cookies,
    # no authenticated session cookie.
    ctx = CookieContext([
        {"name": "loom_anon_id", "domain": ".loom.com", "value": "x"},
        {"name": "loom_oauth_state_v6", "domain": "www.loom.com", "value": "y"},
        {"name": "ajs_anonymous_id", "domain": ".loom.com", "value": "z"},
    ])
    assert auth._has_session_cookie(ctx) is False


def test_has_session_cookie_true_for_connect_sid():
    ctx = CookieContext([
        {"name": "loom_anon_id", "domain": ".loom.com", "value": ""},
        {"name": "connect.sid", "domain": "www.loom.com", "value": "s%3Areal-session"},
    ])
    assert auth._has_session_cookie(ctx) is True


def test_has_session_cookie_false_for_empty_connect_sid():
    ctx = CookieContext([
        {"name": "connect.sid", "domain": "www.loom.com", "value": ""},
    ])
    assert auth._has_session_cookie(ctx) is False


def test_login_timeout_message_names_the_console_script():
    """User-facing text must name a command the user can actually run."""
    import inspect
    from loom_archiver import auth
    source = inspect.getsource(auth.login)
    assert "loom-archiver auth" in source
    assert "Rerun `auth`" not in source
