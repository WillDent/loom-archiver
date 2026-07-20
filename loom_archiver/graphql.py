from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

ENDPOINT = "https://www.loom.com/graphql"

AUTH_ACTION = "Run `loom-archiver auth` to sign in."
AUTH_HINT = f"session expired — {AUTH_ACTION}"


class AuthError(Exception):
    pass


def load_cookie_header(auth_state_path: Path) -> str:
    try:
        state = json.loads(Path(auth_state_path).read_text())
    except FileNotFoundError:
        raise AuthError(f"Not signed in — no saved session at {auth_state_path}. {AUTH_ACTION}") from None
    except (json.JSONDecodeError, OSError):
        raise AuthError(
            f"Couldn't read saved session at {auth_state_path} — it looks corrupt or incomplete. {AUTH_ACTION}"
        ) from None
    parts = [
        f'{c["name"]}={c["value"]}'
        for c in state.get("cookies", [])
        if "loom.com" in c.get("domain", "")
    ]
    if not parts:
        raise AuthError(f"No loom.com cookies found in {auth_state_path}. {AUTH_ACTION}")
    return "; ".join(parts)


class LoomGraphQL:
    def __init__(self, cookie_header: str, loom_web_version: str = "1",
                 client: httpx.Client | None = None, max_retries: int = 3):
        self._cookie = cookie_header
        self._version = loom_web_version
        self._client = client or httpx.Client(timeout=60)
        self._max_retries = max_retries

    def _headers(self, operation_name: str) -> dict:
        return {
            "Content-Type": "application/json",
            "Origin": "https://www.loom.com",
            "x-loom-request-source": f"loom_web_{self._version}",
            "apollographql-client-name": "web",
            "graphql-operation-name": operation_name,
            "Cookie": self._cookie,
        }

    def _post_with_retries(self, operation_name: str, payload: dict) -> httpx.Response:
        # Transient network errors (timeouts, dropped connections) are common over
        # a multi-hour run; retry them with backoff rather than crashing the run.
        headers = self._headers(operation_name)
        for attempt in range(self._max_retries + 1):
            try:
                return self._client.post(ENDPOINT, headers=headers, json=payload)
            except httpx.TransportError:
                if attempt >= self._max_retries:
                    raise
                time.sleep(min(2 ** attempt, 10))

    def execute(self, operation_name: str, query: str, variables: dict) -> dict:
        payload = {"operationName": operation_name, "query": query, "variables": variables}
        resp = self._post_with_retries(operation_name, payload)
        if resp.status_code in (401, 403):
            raise AuthError(f"HTTP {resp.status_code} from Loom; {AUTH_HINT}")
        resp.raise_for_status()
        payload = resp.json()
        for err in payload.get("errors", []) or []:
            if (err.get("extensions") or {}).get("code") == "UNAUTHENTICATED":
                raise AuthError(f"Loom returned UNAUTHENTICATED; {AUTH_HINT}")
        if "data" not in payload:
            raise RuntimeError(f"GraphQL {operation_name} returned no data: {payload.get('errors')}")
        return payload["data"]

    def close(self) -> None:
        self._client.close()


FETCH_VIDEO_TRANSCRIPT = """
query FetchVideoTranscript($videoId: ID!, $password: String) {
  fetchVideoTranscript(videoId: $videoId, password: $password) {
    ... on VideoTranscriptDetails {
      id
      video_id
      source_url
      captions_source_url
      __typename
    }
  }
}
""".strip()

GET_VIDEO_TRANSCODED_URL = """
query GetVideoTranscodedUrl($videoId: ID!, $forceOriginal: Boolean) {
  getVideoTranscodedUrl(videoId: $videoId, forceOriginal: $forceOriginal) {
    ... on VideoSource {
      url
      __typename
    }
    __typename
  }
}
""".strip()

# Fallback for a direct-file CDN URL. The enum type is CloudfrontVideoAcceptableMime
# (confirmed against live Loom, 2026-07-17). Request only single-file formats
# (MP4/WEBM) here; HLS/DASH-only videos return no url and are routed to yt-dlp.
GET_VIDEO_SOURCE = """
query GetVideoSource($videoId: ID!, $password: String, $acceptableMimes: [CloudfrontVideoAcceptableMime!]) {
  getVideo(id: $videoId, password: $password) {
    ... on RegularUserVideo {
      id
      nullableRawCdnUrl(acceptableMimes: $acceptableMimes, password: $password) {
        url
        __typename
      }
      __typename
    }
    __typename
  }
}
""".strip()

# Fallback candidate query (Task 8, Step 1); replace/correct against live Loom
# traffic before relying on it for the real inventory count.
GET_LOOMS_FOR_LIBRARY = (
    Path(__file__).with_name("queries") / "get_looms_for_library.graphql"
).read_text().strip()

# Loom's own folder-listing operation, captured verbatim from the web client
# (2026-07-20). Introspection is disabled, so this could not be derived: the
# `source` enum value is "ACTIVE" -- MINE, OWNED, PERSONAL and CREATED_BY_ME
# were all rejected as invalid FolderSource values. Do not "tidy" these.
GET_PUBLISHED_FOLDERS = (
    Path(__file__).with_name("queries") / "get_published_folders.graphql"
).read_text().strip()
