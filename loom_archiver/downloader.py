from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from concurrent import futures
from pathlib import Path

from . import graphql, diskguard, paths
from .config import Config

# Some Loom videos (typically long recordings) have no direct transcoded MP4 —
# only a CloudFront-signed HLS stream. The signature lives in the manifest URL's
# query string and is required on EVERY fragment request.
#
# yt-dlp cannot fetch these: pointed at the share page it rediscovers the
# manifest, then resolves fragment names relative to it, which drops the query
# string. CloudFront answers 403 for every fragment and yt-dlp reports the
# misleading "Did not get any data blocks" (confirmed against live Loom,
# 2026-07-20: playlist 200, fragment without signature 403, same fragment with
# signature 200). So we localize the playlist ourselves — rewriting each URI to
# an absolute, signed URL — and mux with ffmpeg. yt-dlp remains a last resort
# for anything this path cannot parse.
SHARE_URL = "https://www.loom.com/share/{id}"


def resolve_direct_mp4_url(api, video_id: str) -> str | None:
    """Return a direct single-file CDN URL (MP4/WEBM) or None if only HLS exists."""
    data = api.execute("GetVideoTranscodedUrl", graphql.GET_VIDEO_TRANSCODED_URL,
                       {"videoId": video_id, "forceOriginal": False})
    primary = (data.get("getVideoTranscodedUrl") or {}).get("url")
    if primary:
        return primary

    data = api.execute("GetVideoSource", graphql.GET_VIDEO_SOURCE,
                       {"videoId": video_id, "password": None,
                        "acceptableMimes": ["MP4", "WEBM"]})
    node = data.get("getVideo") or {}
    raw = node.get("nullableRawCdnUrl") or {}
    return raw.get("url")


def write_cookies_txt(auth_state_path: Path, cookies_path: Path) -> Path:
    """Export loom.com cookies from a Playwright storage_state to a Netscape
    cookies.txt that yt-dlp can consume."""
    state = json.loads(Path(auth_state_path).read_text())
    fallback_expiry = int(time.time()) + 7 * 24 * 3600
    lines = ["# Netscape HTTP Cookie File"]
    for c in state.get("cookies", []):
        domain = c.get("domain", "")
        if "loom.com" not in domain:
            continue
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        secure = "TRUE" if c.get("secure") else "FALSE"
        expires = int(c.get("expires") or 0)
        if expires <= 0:
            expires = fallback_expiry
        lines.append("\t".join([
            domain, include_sub, c.get("path", "/"), secure,
            str(expires), c.get("name", ""), c.get("value", ""),
        ]))
    paths.secure_write(Path(cookies_path), "\n".join(lines) + "\n")
    return Path(cookies_path)


def _stream_to_file(http, url: str, dest_dir: Path, file_stem: str) -> None:
    part = Path(dest_dir) / f"{file_stem}.mp4.part"
    final = Path(dest_dir) / f"{file_stem}.mp4"
    with http.stream("GET", url, follow_redirects=True) as resp:
        resp.raise_for_status()
        with open(part, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=1 << 20):
                f.write(chunk)
    part.replace(final)


def resolve_hls_manifest_url(api, video_id: str) -> str | None:
    """Return the CloudFront-signed HLS master playlist URL, or None.

    Omitting acceptableMimes makes Loom return the streaming manifest rather
    than a single-file URL. Videos served as DASH (.mpd) are not handled here.
    """
    data = api.execute("GetVideoSource", graphql.GET_VIDEO_SOURCE,
                       {"videoId": video_id, "password": None,
                        "acceptableMimes": None})
    raw = (data.get("getVideo") or {}).get("nullableRawCdnUrl") or {}
    url = raw.get("url")
    return url if url and ".m3u8" in url else None


def _split_signed_url(url: str) -> tuple[str, str]:
    """Split a signed playlist URL into (base_dir_url, query_string)."""
    without_query, _, query = url.partition("?")
    return without_query.rsplit("/", 1)[0], query


def _sign(base: str, uri: str, query: str) -> str:
    """Resolve a playlist URI to an absolute URL carrying the CloudFront signature."""
    if uri.startswith("http://") or uri.startswith("https://"):
        return uri  # already absolute; assume it carries its own auth
    return f"{base}/{uri}?{query}" if query else f"{base}/{uri}"


def _parse_master(text: str) -> tuple[str | None, str | None]:
    """Return (highest-bandwidth video URI, audio URI or None) from a master playlist."""
    video_uri, best_bandwidth, audio_uri = None, -1, None
    lines = [ln.strip() for ln in text.splitlines()]
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-MEDIA:") and "TYPE=AUDIO" in line:
            match = re.search(r'URI="([^"]+)"', line)
            if match:
                audio_uri = match.group(1)
        elif line.startswith("#EXT-X-STREAM-INF:"):
            match = re.search(r"BANDWIDTH=(\d+)", line)
            bandwidth = int(match.group(1)) if match else 0
            uri = next((x for x in lines[i + 1:] if x and not x.startswith("#")), None)
            if uri and bandwidth > best_bandwidth:
                video_uri, best_bandwidth = uri, bandwidth
    return video_uri, audio_uri


def _segment_urls(http, playlist_url: str) -> list[str]:
    """Return every segment URL in a media playlist, absolute and signed."""
    base, query = _split_signed_url(playlist_url)
    resp = http.get(playlist_url, follow_redirects=True)
    resp.raise_for_status()
    urls = []
    for line in resp.text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#EXT-X-MAP:"):  # fMP4 init segment is a segment too
            match = re.search(r'URI="([^"]+)"', stripped)
            if match:
                urls.append(_sign(base, match.group(1), query))
        elif stripped and not stripped.startswith("#"):
            urls.append(_sign(base, stripped, query))
    return urls


def _fetch_segment(http, url: str, out_path: Path, attempts: int = 4) -> None:
    """Fetch one segment, retrying transient errors with backoff."""
    for attempt in range(attempts):
        try:
            resp = http.get(url, follow_redirects=True)
            resp.raise_for_status()
            out_path.write_bytes(resp.content)
            return
        except Exception:
            if attempt == attempts - 1:
                raise
            time.sleep(2 ** attempt)


def _download_stream(http, playlist_url: str, tmp_dir: Path, name: str) -> Path:
    """Download every segment of a playlist and concatenate them into one file.

    We fetch segments ourselves rather than letting ffmpeg read the playlist over
    HTTP: ffmpeg's HLS demuxer silently dropped roughly half the video packets on
    these streams (measured 1.2fps out vs 5.6fps in the source, 2026-07-20).
    Fetching directly also lets a failed segment raise instead of vanishing.
    """
    urls = _segment_urls(http, playlist_url)
    if not urls:
        raise RuntimeError(f"no segments in playlist: {playlist_url}")

    parts_dir = tmp_dir / f"{name}-parts"
    parts_dir.mkdir()
    paths = [parts_dir / f"{i:05d}.ts" for i in range(len(urls))]
    # A failed fetch raises out of pool.map — a genuinely missing segment is an
    # error, not something to paper over.
    with futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda a: _fetch_segment(http, a[0], a[1]), zip(urls, paths)))

    # Loom serves a few segments as 200-with-empty-body (3/426 on a sampled
    # video). Those are real gaps in the recording, not fetch failures, so they
    # are tolerated — but a large share means something is actually wrong.
    empty = [p for p in paths if p.stat().st_size == 0]
    if len(empty) > max(5, len(paths) // 20):
        raise RuntimeError(
            f"{len(empty)}/{len(paths)} segments empty for {name} — refusing partial download")

    combined = tmp_dir / f"{name}.ts"
    with open(combined, "wb") as out:
        for path in paths:
            out.write(path.read_bytes())
            path.unlink()
    return combined


def download_via_signed_hls(http, manifest_url: str, dest_dir: Path, file_stem: str) -> None:
    """Download a CloudFront-signed HLS video by localizing its playlists.

    Muxes in a LOCAL temp dir — writing many small fragments straight to a
    network volume (AFP/SMB) fails intermittently — then moves the finished MP4
    to the destination via a .part rename for atomicity.
    """
    dest_dir = Path(dest_dir)
    final = dest_dir / f"{file_stem}.mp4"
    base, query = _split_signed_url(manifest_url)

    resp = http.get(manifest_url, follow_redirects=True)
    resp.raise_for_status()
    video_uri, audio_uri = _parse_master(resp.text)
    if not video_uri:
        raise RuntimeError(f"no video stream in master playlist: {manifest_url}")

    with tempfile.TemporaryDirectory(prefix="loomhls-") as tmp:
        tmp_path = Path(tmp)
        video_ts = _download_stream(http, _sign(base, video_uri, query), tmp_path, "video")
        inputs = ["-i", str(video_ts)]
        if audio_uri:
            audio_ts = _download_stream(http, _sign(base, audio_uri, query), tmp_path, "audio")
            inputs += ["-i", str(audio_ts)]

        produced = tmp_path / "out.mp4"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error",
             # Loom segments carry two h264 streams (screen + camera bubble);
             # -map picks the primary video explicitly rather than letting
             # ffmpeg guess, and keeps the separate audio track.
             *inputs,
             *(["-map", "0:v:0", "-map", "1:a:0"] if audio_uri else ["-map", "0:v:0", "-map", "0:a?"]),
             "-c", "copy", "-movflags", "+faststart",
             str(produced)],
            check=True,
        )
        if not produced.exists() or produced.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg produced no output for {manifest_url}")
        part = dest_dir / f"{file_stem}.mp4.part"
        shutil.move(str(produced), str(part))  # local -> destination (cross-fs copy)
    part.replace(final)  # atomic rename on the destination filesystem


def download_via_ytdlp(video_id: str, dest_dir: Path, file_stem: str, cookies_path: Path) -> None:
    """Download an HLS-only video with yt-dlp (native HLS downloader + ffmpeg mux).

    yt-dlp downloads and muxes in a LOCAL temp dir — concurrent fragment writes on
    a network volume (AFP/SMB) fail intermittently. Only the finished MP4 is moved
    to the (possibly network) destination, via a .part rename for atomicity.
    """
    dest_dir = Path(dest_dir)
    final = dest_dir / f"{file_stem}.mp4"
    with tempfile.TemporaryDirectory(prefix="loomdl-") as tmp:
        out_template = str(Path(tmp) / "video.%(ext)s")
        subprocess.run(
            ["yt-dlp", "--quiet", "--no-warnings", "--no-progress",
             "--concurrent-fragments", "4",   # parallel HLS segments -> faster per video
             # These HLS streams have hundreds of tiny fragments; CloudFront
             # occasionally drops one. Retry generously with backoff so a single
             # transient fragment failure doesn't abort the whole video. yt-dlp
             # skips genuinely-unavailable fragments by default and still muxes.
             "--fragment-retries", "30",
             "--retries", "10",
             "--retry-sleep", "fragment:exp=1:20",
             "--merge-output-format", "mp4",
             "--cookies", str(cookies_path),
             "-o", out_template,
             SHARE_URL.format(id=video_id)],
            check=True,
        )
        produced = Path(tmp) / "video.mp4"
        if not produced.exists():
            candidates = list(Path(tmp).glob("video.*"))
            if not candidates:
                raise RuntimeError(f"yt-dlp produced no output for {video_id}")
            produced = candidates[0]
        part = dest_dir / f"{file_stem}.mp4.part"
        shutil.move(str(produced), str(part))  # local -> destination (cross-fs copy)
    part.replace(final)  # atomic rename on the destination filesystem


def download_mp4(api, http, video_id: str, dest_dir: Path, file_stem: str, cfg: Config) -> bool:
    Path(dest_dir).mkdir(parents=True, exist_ok=True)
    diskguard.assert_headroom(dest_dir, cfg.disk_floor_gb)

    # A failure probing for a direct MP4 must not hide an available HLS stream:
    # Loom answers 400 for some videos here while still serving them over HLS.
    # The error is re-raised below if the HLS path cannot handle the video.
    direct_error: Exception | None = None
    try:
        url = resolve_direct_mp4_url(api, video_id)
    except Exception as e:  # noqa: BLE001 - deferred, not swallowed
        url, direct_error = None, e

    if url:
        _stream_to_file(http, url, dest_dir, file_stem)
        return True

    # HLS-only video: fetch the signed segments and mux with ffmpeg.
    manifest_url = resolve_hls_manifest_url(api, video_id)
    if manifest_url:
        download_via_signed_hls(http, manifest_url, dest_dir, file_stem)
        return (Path(dest_dir) / f"{file_stem}.mp4").exists()

    if direct_error is not None:
        raise direct_error  # no HLS to fall back on — surface the real cause

    # Anything else (e.g. DASH): last-resort yt-dlp, which needs the cookies.
    if not cfg.cookies_path or not Path(cfg.cookies_path).exists():
        return False
    download_via_ytdlp(video_id, dest_dir, file_stem, cfg.cookies_path)
    return (Path(dest_dir) / f"{file_stem}.mp4").exists()
