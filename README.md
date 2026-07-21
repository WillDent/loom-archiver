# Loom Archiver

Archive the Loom videos **you created** — video, transcript, and a resumable
ledger — to a folder you control.

Loom has no bulk export. Downloads are one video at a time, gated to paid plans,
and transcripts cannot be exported at all. This tool walks your library over
Loom's own API and saves everything locally.

**Your credentials go only to Loom.** You sign in through a real Chrome window
(Google SSO included); the tool never sees your password, only the resulting
session cookie, stored `0600` in your platform config directory. That cookie is
sent to Loom itself — exactly as your browser does — and nowhere else. There is
no backend, no telemetry, and no third party in the path.

Proven on a 1,658-video / 314 GB library.

**New here?** [**docs/WALKTHROUGH.md**](docs/WALKTHROUGH.md) is a step-by-step guide
from installation to a verified archive, with the output you should expect at each
stage and what to do when something fails. The rest of this README is the reference.

## Install

```bash
pipx install 'loom-archiver[auth]'
pipx run playwright install chrome
```

(`pipx` only puts the installed package's own command on your PATH, not
`playwright`'s — `pipx run` fetches Playwright into a throwaway environment
just to run its installer. The Chrome build it downloads lands in a shared
user-level cache, so the `loom-archiver` command finds it regardless of which
environment triggered the download.)

**ffmpeg is required** and is not installed automatically:

| Platform | Command |
|---|---|
| macOS | `brew install ffmpeg` |
| Debian/Ubuntu | `sudo apt install ffmpeg` |
| Windows | <https://ffmpeg.org/download.html> |

## Usage

```bash
loom-archiver auth      --dest ~/loom-archive   # sign in once, in a real browser
loom-archiver inventory --dest ~/loom-archive   # list what exists; downloads nothing
loom-archiver run       --dest ~/loom-archive   # download everything
```

`run` is resumable — Ctrl-C and rerun it. Progress lives in
`<dest>/loom_video_list_progress.csv`.

Options: `--floor <GB>` (stop when free space drops below this, default 50),
`--delay <seconds>` (pause between requests, default 0.5), `--max-depth <n>`
(maximum folder nesting depth to walk, default 10), `--folders-only` (skip the
main-library pass and discover only videos inside folders). `--folders-only`
scopes *discovery*, not processing: against a destination that already has a
ledger, videos left pending from an earlier run are still processed.

## Use with Claude (MCP)

Drive your Loom library through Claude — search and summarize transcripts,
list/inventory videos, and download selected ones — via a local MCP server.
Full-library archiving is still a job for the `loom-archiver run` CLI.

Install the extra:

```bash
pipx install 'loom-archiver[mcp]'
# or: pip install 'loom-archiver[mcp]'
```

You still run `loom-archiver auth --dest ~/loom-archive` once first to sign
in. The MCP server reuses that saved session and will tell you to run it if
you're not signed in yet.

Add it to Claude Desktop or Claude Code's MCP config:

```json
{
  "mcpServers": {
    "loom-archiver": {
      "command": "loom-archiver-mcp",
      "env": { "LOOM_ARCHIVER_DEST": "/Users/you/loom-archive" }
    }
  }
}
```

With `pipx`, `command` may need the absolute path to the shim if Claude
can't find it on PATH (`pipx list` or `which loom-archiver-mcp`). Add
`LOOM_ARCHIVER_CONFIG_DIR` to the `env` block if your session lives in a
non-default location.

**What Claude can do:** check inventory and auth status, list/filter videos
by folder or download status, search transcript text, read a transcript to
summarize it, and download a specific set of videos.

Two things worth knowing before you rely on this:

- **Selective, not bulk.** `download_videos` refuses large batches (default
  cap 10); the full 300GB-scale archive stays a CLI job. This is deliberate.
- **Filter axes are limited.** You can filter by folder, transcript text,
  and download status — but Loom's API exposes no video dates, durations, or
  view counts, so Claude can't answer "my videos from last month" or "my
  longest video."
- **Prompt-injection note:** transcripts are your own recordings, returned to
  Claude as data. The MCP exposes no destructive Loom operations (no
  delete/move/share) — it can read, inventory, search, and download only.

## What you get

```
<dest>/
  main-library/         videos not in any folder
    <id>__<title>.mp4
    <id>__<title>.vtt   transcript, original cue format
    <id>__<title>.txt   transcript, plain text
  <Folder>/             one directory per Loom folder, nested as in Loom
  loom_video_list_progress.csv
```

Every run prints what it enumerated — loose count, folder count, per-folder
counts — so you can see the shape of your archive rather than infer it.

## Limitations

- **Videos you created only.** Videos shared *with* you are not archived.
- **No workspace/admin archive.** There is no "download everything in the org" mode.
- Downloaded MP4s do not include Loom features that only exist in their player
  (CTAs, chapters, filler-word removal).
- A small number of videos are served as DASH; those fall back to `yt-dlp`.
- Deeply nested folders are walked to depth 10 by default and will raise rather
  than silently truncate beyond that.

## Credentials

Session state lives in your platform config directory:

- macOS: `~/Library/Application Support/loom-archiver/`
- Linux: `~/.config/loom-archiver/` (or `$XDG_CONFIG_HOME/loom-archiver/`)
- Windows: untested. There's no Windows-specific path, so it falls back to the
  Linux logic above (typically `~\.config\loom-archiver\`, which is not an
  idiomatic Windows location). Set `LOOM_ARCHIVER_CONFIG_DIR` to place it
  explicitly, e.g. `%LOCALAPPDATA%\loom-archiver`.

Override with `LOOM_ARCHIVER_CONFIG_DIR`. Treat `auth_state.json` like a
password. Sign out of the Loom session when your archive is complete.

## How it works

Loom's web app talks to a private GraphQL API; this tool uses the same
operations your browser does — `GetLoomsForLibrary` to page through videos,
`GetPublishedFolders` to walk folders, `FetchVideoTranscript` for captions, and
`GetVideoSource` / `GetVideoTranscodedUrl` for media. Videos served only as
CloudFront-signed HLS are fetched segment by segment and muxed with ffmpeg.

## Legal

This tool accesses **your own content, in your own account, with your own
credentials**. It does not redistribute anything, does not touch other people's
data, and rate-limits itself by default.

Your use of Loom remains subject to your agreement with Loom/Atlassian —
compliance with those terms is between you and them. Not affiliated with Loom or
Atlassian.

## License

MIT © 2026 Will Dent
