# Changelog

## 0.1.0 — unreleased

First public release.

### Added
- Local MCP server so Claude can search/summarize transcripts, inventory and
  list videos, and selectively download them. Install with the
  `loom-archiver[mcp]` extra; runs as the `loom-archiver-mcp` entry point.
- Recursive folder discovery via Loom's `GetPublishedFolders` operation, with
  cycle and depth guards and a cross-check for folders outside the walk.
- Per-run summary of what was enumerated (loose count, folder count, per-folder counts).
- `ffmpeg` preflight check at startup.
- `pipx`-installable `loom-archiver` console script.
- Added `--folders-only` to archive just the videos inside folders.
- Verified recursive folder discovery against real nested data; the captured
  API responses are now replayed as a regression test.

### Changed
- Credentials moved from the package directory to platform config dirs and are
  written `0600`. Under `pipx`, the old location was inside site-packages.
- `--dest` is now required; the personal NAS default is gone.
- Videos with no transcript are recorded as `unavailable` rather than `failed`,
  and are no longer retried on every run.

### Fixed
- Videos inside folders were silently skipped for anyone but the original author,
  because folder IDs were hardcoded. No error was raised and the run exited 0.
