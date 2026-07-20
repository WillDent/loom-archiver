import pytest

from loom_archiver import preflight


def test_assert_ffmpeg_passes_when_present(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    preflight.assert_ffmpeg()  # must not raise


def test_assert_ffmpeg_raises_with_install_hint(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    with pytest.raises(preflight.MissingDependencyError) as excinfo:
        preflight.assert_ffmpeg()
    message = str(excinfo.value)
    assert "ffmpeg" in message
    assert "brew install ffmpeg" in message


def test_assert_ffmpeg_checks_the_right_binary(monkeypatch):
    asked = []
    monkeypatch.setattr(preflight.shutil, "which", lambda name: asked.append(name) or "/x")
    preflight.assert_ffmpeg()
    assert asked == ["ffmpeg"]
