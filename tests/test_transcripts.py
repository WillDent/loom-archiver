import httpx
import respx
from pathlib import Path
from loom_archiver.transcripts import vtt_to_text
from loom_archiver import transcripts

SAMPLE_VTT = """WEBVTT

1
00:00:00.000 --> 00:00:02.000
Hello everyone

2
00:00:02.000 --> 00:00:04.000
Hello everyone

3
00:00:04.000 --> 00:00:06.000
welcome to the demo
"""


def test_vtt_to_text_extracts_spoken_lines():
    out = vtt_to_text(SAMPLE_VTT)
    assert out == "Hello everyone\nwelcome to the demo"


def test_vtt_to_text_handles_empty():
    assert vtt_to_text("WEBVTT\n\n") == ""


def test_vtt_to_text_converts_voice_tags_to_speaker_prefix():
    vtt = ("WEBVTT\n\n1\n00:00:00.000 --> 00:00:02.000\n"
           "<v Cameron Aiton>Good morning everyone</v>\n")
    assert vtt_to_text(vtt) == "Cameron Aiton: Good morning everyone"


def test_vtt_to_text_strips_other_inline_tags():
    vtt = ("WEBVTT\n\n1\n00:00:00.000 --> 00:00:02.000\n"
           "<c.yellow>hello</c> <00:00:01.000>there\n")
    assert vtt_to_text(vtt) == "hello there"


def test_vtt_to_text_strips_note_lines():
    vtt = "WEBVTT\n\nNOTE This is a comment\n\n1\n00:00:00.000 --> 00:00:01.000\nreal speech\n"
    assert vtt_to_text(vtt) == "real speech"


class FakeApi:
    def __init__(self, captions_url):
        self._url = captions_url

    def execute(self, op, query, variables):
        return {"fetchVideoTranscript": {
            "captions_source_url": self._url,
            "source_url": "https://cdn.loom.com/x.json",
        }}


@respx.mock
def test_fetch_and_write_creates_vtt_and_txt(tmp_path):
    vtt = "WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.000\nhi there\n"
    respx.get("https://cdn.loom.com/x.vtt").mock(return_value=httpx.Response(200, text=vtt))
    api = FakeApi("https://cdn.loom.com/x.vtt")
    with httpx.Client() as http:
        wrote = transcripts.fetch_and_write(api, http, "v1", tmp_path, "v1__Demo")
    assert wrote is True
    assert (tmp_path / "v1__Demo.vtt").read_text() == vtt
    assert (tmp_path / "v1__Demo.txt").read_text() == "hi there"


@respx.mock
def test_fetch_and_write_returns_false_when_no_transcript(tmp_path):
    api = FakeApi(None)
    with httpx.Client() as http:
        wrote = transcripts.fetch_and_write(api, http, "v1", tmp_path, "v1__Demo")
    assert wrote is False
    assert not (tmp_path / "v1__Demo.vtt").exists()
