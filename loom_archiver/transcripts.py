from __future__ import annotations

import re
from pathlib import Path

from . import graphql

# `<v Speaker Name>` voice spans become a readable "Speaker Name: " prefix;
# every other inline cue tag (`</v>`, `<c>`, `<00:00:00.000>`, …) is dropped.
_VOICE_TAG = re.compile(r"<v\s+([^>]*)>", re.IGNORECASE)
_ANY_TAG = re.compile(r"<[^>]+>")


def _clean_cue_line(line: str) -> str:
    line = _VOICE_TAG.sub(lambda m: f"{m.group(1).strip()}: ", line)
    line = _ANY_TAG.sub("", line)
    return re.sub(r"\s+", " ", line).strip()


def vtt_to_text(vtt: str) -> str:
    lines: list[str] = []
    for raw in vtt.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "WEBVTT" or line.startswith("NOTE"):
            continue
        if "-->" in line:
            continue
        if line.isdigit():  # cue index
            continue
        line = _clean_cue_line(line)  # strip/convert inline VTT tags
        if not line:
            continue
        if lines and lines[-1] == line:  # drop consecutive dupes
            continue
        lines.append(line)
    return "\n".join(lines)


def fetch_and_write(api, http, video_id: str, dest_dir: Path, file_stem: str) -> bool:
    data = api.execute("FetchVideoTranscript", graphql.FETCH_VIDEO_TRANSCRIPT,
                       {"videoId": video_id, "password": None})
    details = data.get("fetchVideoTranscript") or {}
    captions_url = details.get("captions_source_url")
    if not captions_url:
        return False

    resp = http.get(captions_url, follow_redirects=True)
    resp.raise_for_status()
    vtt = resp.text

    Path(dest_dir).mkdir(parents=True, exist_ok=True)
    (Path(dest_dir) / f"{file_stem}.vtt").write_text(vtt, encoding="utf-8")
    (Path(dest_dir) / f"{file_stem}.txt").write_text(vtt_to_text(vtt), encoding="utf-8")
    return True
