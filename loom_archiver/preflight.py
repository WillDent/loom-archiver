from __future__ import annotations

import shutil


class MissingDependencyError(RuntimeError):
    """A required external program is not installed."""


def assert_ffmpeg() -> None:
    """Fail before downloading anything if ffmpeg is missing.

    Checked up front rather than at the first HLS video, which on a large
    library can be an hour into a run. Same instinct as diskguard checking
    headroom before writing rather than after filling the disk.
    """
    if shutil.which("ffmpeg") is None:
        raise MissingDependencyError(
            "ffmpeg was not found on PATH, and it is required to save HLS videos.\n"
            "  macOS:   brew install ffmpeg\n"
            "  Debian:  sudo apt install ffmpeg\n"
            "  Windows: https://ffmpeg.org/download.html"
        )
