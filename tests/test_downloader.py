import json

import httpx
import respx
from loom_archiver import downloader
from loom_archiver.config import Config


class Api:
    def __init__(self, primary=None, fallback=None):
        self.primary, self.fallback = primary, fallback

    def execute(self, op, query, variables):
        if op == "GetVideoTranscodedUrl":
            return {"getVideoTranscodedUrl": ({"url": self.primary} if self.primary else {})}
        if op == "GetVideoSource":
            node = {"nullableRawCdnUrl": ({"url": self.fallback} if self.fallback else None)}
            return {"getVideo": node}
        raise AssertionError(op)


def test_resolve_prefers_primary():
    assert downloader.resolve_direct_mp4_url(Api(primary="P", fallback="F"), "v1") == "P"


def test_resolve_uses_fallback_when_primary_empty():
    assert downloader.resolve_direct_mp4_url(Api(primary=None, fallback="F"), "v1") == "F"


def test_resolve_returns_none_when_both_empty():
    assert downloader.resolve_direct_mp4_url(Api(), "v1") is None


@respx.mock
def test_download_mp4_streams_and_renames(tmp_path):
    respx.get("https://cdn.loom.com/v1.mp4").mock(
        return_value=httpx.Response(200, content=b"BINARYDATA")
    )
    cfg = Config.default()
    cfg.disk_floor_gb = 0  # disable guard for the test volume
    api = Api(primary="https://cdn.loom.com/v1.mp4")
    with httpx.Client() as http:
        ok = downloader.download_mp4(api, http, "v1", tmp_path, "v1__Demo", cfg)
    assert ok is True
    assert (tmp_path / "v1__Demo.mp4").read_bytes() == b"BINARYDATA"
    assert not (tmp_path / "v1__Demo.mp4.part").exists()


def test_download_mp4_routes_hls_only_video_to_ytdlp(tmp_path, monkeypatch):
    # No direct URL available (HLS-only) -> yt-dlp path is used.
    cfg = Config.default()
    cfg.disk_floor_gb = 0
    cfg.cookies_path = tmp_path / "cookies.txt"
    cfg.cookies_path.write_text("# Netscape HTTP Cookie File\n")

    calls = {}

    def fake_ytdlp(video_id, dest_dir, file_stem, cookies_path):
        calls["args"] = (video_id, str(cookies_path))
        (tmp_path / f"{file_stem}.mp4").write_bytes(b"MUXED")

    monkeypatch.setattr(downloader, "download_via_ytdlp", fake_ytdlp)
    api = Api(primary=None, fallback=None)  # no direct url
    with httpx.Client() as http:
        ok = downloader.download_mp4(api, http, "v9", tmp_path, "v9__HLS", cfg)
    assert ok is True
    assert calls["args"] == ("v9", str(cfg.cookies_path))
    assert (tmp_path / "v9__HLS.mp4").read_bytes() == b"MUXED"


def test_download_mp4_returns_false_when_hls_and_no_cookies(tmp_path):
    cfg = Config.default()
    cfg.disk_floor_gb = 0
    cfg.cookies_path = tmp_path / "missing_cookies.txt"  # does not exist
    api = Api(primary=None, fallback=None)
    with httpx.Client() as http:
        ok = downloader.download_mp4(api, http, "v9", tmp_path, "v9__HLS", cfg)
    assert ok is False


def test_write_cookies_txt_filters_loom_and_formats_netscape(tmp_path):
    state = {"cookies": [
        {"name": "connect.sid", "value": "abc", "domain": "www.loom.com",
         "path": "/", "secure": True, "expires": 1900000000},
        {"name": "other", "value": "z", "domain": ".example.com", "path": "/"},
    ]}
    auth = tmp_path / "auth_state.json"
    auth.write_text(json.dumps(state))
    out = tmp_path / "cookies.txt"
    downloader.write_cookies_txt(auth, out)
    text = out.read_text()
    assert text.startswith("# Netscape HTTP Cookie File")
    assert "connect.sid\tabc" in text or "connect.sid" in text
    assert "example.com" not in text  # non-loom cookie filtered out
    # tab-separated, 7 fields on the cookie line
    cookie_line = [l for l in text.splitlines() if l.startswith("www.loom.com")][0]
    assert len(cookie_line.split("\t")) == 7


def test_write_cookies_txt_is_not_world_readable(tmp_path):
    """cookies.txt holds the session cookie; the README promises 0600."""
    import json, stat
    from loom_archiver import downloader
    state = {"cookies": [{"domain": ".loom.com", "path": "/", "secure": True,
                          "expires": 0, "name": "connect.sid", "value": "s3cret"}]}
    auth = tmp_path / "auth_state.json"
    auth.write_text(json.dumps(state))
    out = tmp_path / "cfg" / "cookies.txt"
    downloader.write_cookies_txt(auth, out)
    assert "connect.sid" in out.read_text()
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


# --- CloudFront-signed HLS path -------------------------------------------

MASTER = """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="audio",URI="mediaplaylist-audio.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=1280x720
mediaplaylist-video-bitrate1500.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3200000,RESOLUTION=1920x1080
mediaplaylist-video-bitrate3200.m3u8
"""


class HlsApi:
    """Loom returns a streaming manifest only when acceptableMimes is omitted."""

    def __init__(self, manifest):
        self.manifest = manifest

    def execute(self, op, query, variables):
        if op == "GetVideoTranscodedUrl":
            return {"getVideoTranscodedUrl": {}}
        if op == "GetVideoSource":
            if variables.get("acceptableMimes"):  # MP4/WEBM -> no single file
                return {"getVideo": {"nullableRawCdnUrl": None}}
            return {"getVideo": {"nullableRawCdnUrl": {"url": self.manifest}}}
        raise AssertionError(op)


def test_parse_master_picks_highest_bandwidth_and_audio():
    video, audio = downloader._parse_master(MASTER)
    assert video == "mediaplaylist-video-bitrate3200.m3u8"
    assert audio == "mediaplaylist-audio.m3u8"


def test_sign_appends_signature_to_relative_uri_only():
    assert downloader._sign("https://cdn/x", "seg0.ts", "Policy=P&Sig=S") == \
        "https://cdn/x/seg0.ts?Policy=P&Sig=S"
    # An absolute URI carries its own auth and must not be rewritten.
    assert downloader._sign("https://cdn/x", "https://other/seg.ts", "Policy=P") == \
        "https://other/seg.ts"


def test_split_signed_url_separates_base_and_query():
    base, query = downloader._split_signed_url("https://cdn/a/b/play.m3u8?Policy=P&Sig=S")
    assert base == "https://cdn/a/b"
    assert query == "Policy=P&Sig=S"


@respx.mock
def test_segment_urls_signs_segments_and_init_map():
    respx.get("https://cdn/v/media.m3u8").mock(return_value=httpx.Response(200, text=(
        "#EXTM3U\n"
        '#EXT-X-MAP:URI="init.mp4"\n'
        "#EXTINF:4.0,\n"
        "seg0.ts\n"
        "#EXTINF:4.0,\n"
        "seg1.ts\n"
    )))
    with httpx.Client() as http:
        urls = downloader._segment_urls(http, "https://cdn/v/media.m3u8?Sig=S")
    assert urls == [
        "https://cdn/v/init.mp4?Sig=S",
        "https://cdn/v/seg0.ts?Sig=S",
        "https://cdn/v/seg1.ts?Sig=S",
    ]


@respx.mock
def test_download_stream_tolerates_a_few_empty_segments(tmp_path):
    # Loom serves occasional 200-with-empty-body segments; those are real gaps.
    body = "#EXTM3U\n" + "".join(f"#EXTINF:4.0,\nseg{i}.ts\n" for i in range(10))
    respx.get("https://cdn/v/media.m3u8").mock(return_value=httpx.Response(200, text=body))
    for i in range(10):
        content = b"" if i == 3 else b"DATA"
        respx.get(f"https://cdn/v/seg{i}.ts").mock(
            return_value=httpx.Response(200, content=content))
    with httpx.Client() as http:
        out = downloader._download_stream(http, "https://cdn/v/media.m3u8?Sig=S",
                                          tmp_path, "video")
    assert out.read_bytes() == b"DATA" * 9  # empty segment contributes nothing


@respx.mock
def test_download_stream_rejects_mostly_empty_stream(tmp_path):
    body = "#EXTM3U\n" + "".join(f"#EXTINF:4.0,\nseg{i}.ts\n" for i in range(40))
    respx.get("https://cdn/v/media.m3u8").mock(return_value=httpx.Response(200, text=body))
    for i in range(40):
        respx.get(f"https://cdn/v/seg{i}.ts").mock(return_value=httpx.Response(200, content=b""))
    with httpx.Client() as http:
        try:
            downloader._download_stream(http, "https://cdn/v/media.m3u8?Sig=S",
                                        tmp_path, "video")
        except RuntimeError as e:
            assert "empty" in str(e)
        else:
            raise AssertionError("expected RuntimeError for a mostly-empty stream")


def test_resolve_hls_manifest_url_ignores_dash_manifests():
    assert downloader.resolve_hls_manifest_url(
        HlsApi("https://cdn/v/playlistmultibitrate.mpd?Sig=S"), "v1") is None
    assert downloader.resolve_hls_manifest_url(
        HlsApi("https://cdn/v/playlist-split.m3u8?Sig=S"), "v1") == \
        "https://cdn/v/playlist-split.m3u8?Sig=S"


def test_download_mp4_prefers_signed_hls_over_ytdlp(tmp_path, monkeypatch):
    cfg = Config.default()
    cfg.disk_floor_gb = 0
    cfg.cookies_path = tmp_path / "cookies.txt"
    cfg.cookies_path.write_text("# Netscape HTTP Cookie File\n")

    calls = {}

    def fake_hls(http, manifest_url, dest_dir, file_stem):
        calls["manifest"] = manifest_url
        (tmp_path / f"{file_stem}.mp4").write_bytes(b"MUXED")

    def boom(*a, **k):
        raise AssertionError("yt-dlp must not be used when an HLS manifest exists")

    monkeypatch.setattr(downloader, "download_via_signed_hls", fake_hls)
    monkeypatch.setattr(downloader, "download_via_ytdlp", boom)

    api = HlsApi("https://cdn/v/playlist-split.m3u8?Sig=S")
    with httpx.Client() as http:
        ok = downloader.download_mp4(api, http, "v9", tmp_path, "v9__HLS", cfg)
    assert ok is True
    assert calls["manifest"] == "https://cdn/v/playlist-split.m3u8?Sig=S"


class RaisingDirectApi(HlsApi):
    """Loom 400s on the direct-MP4 probe but still serves the video over HLS."""

    def execute(self, op, query, variables):
        if op == "GetVideoTranscodedUrl":
            raise httpx.HTTPStatusError(
                "400", request=httpx.Request("POST", "https://www.loom.com/graphql"),
                response=httpx.Response(400))
        return super().execute(op, query, variables)


def test_direct_url_error_does_not_hide_available_hls(tmp_path, monkeypatch):
    cfg = Config.default()
    cfg.disk_floor_gb = 0
    monkeypatch.setattr(downloader, "download_via_signed_hls",
                        lambda http, m, d, s: (tmp_path / f"{s}.mp4").write_bytes(b"OK"))
    api = RaisingDirectApi("https://cdn/v/playlist-split.m3u8?Sig=S")
    with httpx.Client() as http:
        assert downloader.download_mp4(api, http, "v9", tmp_path, "v9__HLS", cfg) is True


def test_direct_url_error_is_reraised_when_no_hls_available(tmp_path):
    cfg = Config.default()
    cfg.disk_floor_gb = 0
    api = RaisingDirectApi("https://cdn/v/playlistmultibitrate.mpd?Sig=S")  # DASH -> no HLS
    with httpx.Client() as http:
        try:
            downloader.download_mp4(api, http, "v9", tmp_path, "v9__HLS", cfg)
        except httpx.HTTPStatusError:
            pass
        else:
            raise AssertionError("expected the direct-probe error to surface")
