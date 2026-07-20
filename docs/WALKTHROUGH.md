# Archiving your Loom library — a walkthrough

This guide takes you from nothing to a complete local copy of your Loom videos and
transcripts. It assumes no prior knowledge of the tool.

Budget **15 minutes of setup**, then a download that runs unattended. A large library
takes hours — a real 1,658-video / 314 GB archive took roughly a day. You can stop and
resume at any point.

---

## What you'll end up with

A folder on your own disk, mirroring your Loom library:

```
my-loom-archive/
  main-library/                          videos not in any folder
    a1b2c3__Sprint demo.mp4
    a1b2c3__Sprint demo.vtt              transcript, with timings
    a1b2c3__Sprint demo.txt              transcript, plain text
  Clients/                               one directory per Loom folder
    Acme/                                nested exactly as in Loom
      d4e5f6__Kickoff call.mp4
      ...
  loom_video_list_progress.csv           what's done, what failed, and why
```

Filenames start with Loom's video ID, so two videos with the same title never
overwrite each other.

## What it does *not* do

Read this before you rely on it.

- **Only videos you created.** Videos shared *with* you are not archived.
- **No workspace-wide or admin mode.** There's no "download everything in the org."
- **Loom-player features don't survive.** CTAs, chapters, and filler-word removal exist
  only in Loom's player, not in an MP4.
- **Folder nesting is untested against real data.** The code walks nested folders and is
  covered by tests, but no account with actual nesting has ever been archived. If you
  have nested folders, check the results (see [Step 4](#step-4-check-what-it-found)).

---

## Before you start

You need four things.

**1. Python 3.11 or newer**

```bash
python3 --version
```
If that prints 3.10 or lower, install a newer Python from [python.org](https://www.python.org/downloads/)
or your package manager.

**2. pipx** — installs command-line tools without disturbing your other Python packages

```bash
# macOS
brew install pipx && pipx ensurepath

# Debian/Ubuntu
sudo apt install pipx && pipx ensurepath
```
Open a new terminal afterwards so `pipx ensurepath` takes effect.

**3. ffmpeg** — needed to assemble some videos. It is *not* installed automatically.

| Platform | Command |
|---|---|
| macOS | `brew install ffmpeg` |
| Debian/Ubuntu | `sudo apt install ffmpeg` |
| Windows | download from [ffmpeg.org](https://ffmpeg.org/download.html) |

Check it worked: `ffmpeg -version` should print a version, not "command not found."

**4. Google Chrome**, and the ability to sign in to Loom in it.

**Also: enough free disk space.** Videos are large. A few hundred videos can run to
tens of gigabytes. The tool stops rather than filling your disk — see
[Step 5](#step-5-download-everything).

---

## Step 1: Install

```bash
pipx install 'loom-archiver[auth]'
pipx run playwright install chrome
```

The second line downloads a browser build used for the sign-in window. It's a one-time
download of a few hundred MB.

> **Why `pipx run` and not just `playwright install`?** pipx only puts *this tool's* own
> command on your PATH, so a bare `playwright` won't be found. `pipx run` handles that.

Check the install:
```bash
loom-archiver --help
```
You should see:
```
usage: loom-archiver [-h] {auth,inventory,run} ...
```

---

## Step 2: Choose where the archive goes

Pick a folder with plenty of space. Every command needs it via `--dest`.

```bash
mkdir -p ~/my-loom-archive
```

Throughout this guide, replace `~/my-loom-archive` with your own path.

> **A note on external and network drives.** These work, and the tool is careful with
> them — it assembles files locally and moves each finished video across in one step, so
> an interrupted transfer can't leave a half-written file in your archive. But the drive
> must stay mounted for the whole run. If it disappears mid-run, the tool stops with a
> clear message rather than writing to your boot disk.

---

## Step 3: Sign in

```bash
loom-archiver auth --dest ~/my-loom-archive
```

A Chrome window opens and you'll see:

```
A Chrome window opened. Sign in to Loom with Google if prompted.
Waiting for an authenticated session (up to 5 minutes)...
```

Sign in to Loom in that window exactly as you normally would — Google SSO, email, or
whatever your organisation uses. Once you're in, the tool detects it automatically:

```
Saved session to /Users/you/Library/Application Support/loom-archiver/auth_state.json
```

You can close the window. **You only do this once**, until the session expires.

### What just happened to your credentials

You typed your password into Google's own sign-in page, in a real browser. The tool
never saw it. What it kept is the session cookie your browser received — the same thing
that keeps you logged in to Loom normally.

That cookie is stored in your user config directory with `0600` permissions, meaning only
your account can read it. It is sent to Loom itself, exactly as your browser does, and to
nowhere else. There is no backend and no telemetry.

**Treat it like a password.** Anyone who can read that file can act as you on Loom. When
your archive is finished, sign out of the Loom session to invalidate it.

> `auth` requires `--dest` even though it doesn't write there. That's a wart — pass it
> anyway.

---

## Step 4: Check what it found

**Do this before downloading anything.** It's read-only and takes under a minute.

```bash
loom-archiver inventory --dest ~/my-loom-archive
```

You'll see the shape of your library:

```
Enumerated 1680 videos across 3 folder(s) + main library:
      1  AIDemos
      1  Artivo-Mkt
     10  MD Turbines
   1668  main-library
Inventory: 1680 discovered, 1680 total in ledger.
```

**Read those numbers against what you expect.** This is your chance to catch a problem
while it costs you nothing.

- Does the folder list match the folders you see in Loom?
- Is the total roughly the number of videos you think you have?
- If you have nested folders, do they appear as `Parent/Child`?

If a folder you expected is missing, stop and investigate rather than downloading — an
archive is only useful if it's complete.

You may also see:

```
Warning: 2 folder(s) exist that were not walked:
  - Team Space (a1b2c3...)
  Videos you created inside them may be missing from this archive.
```

This means folders exist that the tool couldn't enumerate as yours — usually someone
else's folders that you can see. Videos *you* created inside them may not be archived.
It's a warning, not a failure, and the run continues.

---

## Step 5: Download everything

```bash
loom-archiver run --dest ~/my-loom-archive
```

It re-checks the inventory, then reports what's left and works through it:

```
1680 videos to process.
```

There's no progress bar — this is a long, quiet job. Leave it running.

**You can stop it at any time with `Ctrl-C`, and rerun the same command to resume.**
Progress is saved after every video. Nothing is downloaded twice.

### Useful options

| Option | What it does | Default |
|---|---|---|
| `--floor 50` | Stop when free space drops below this many GB | 50 |
| `--delay 0.5` | Seconds to pause between requests | 0.5 |
| `--max-depth 10` | How deep to follow nested folders | 10 |

If you're short on space, lower `--floor`. If you'd rather be gentler on Loom, raise
`--delay`.

### When it finishes

```
Done. 1680 processed, 0 with failures.
127 video(s) have no transcript available (not an error).
```

That transcript line is normal — Loom simply has no transcript for some videos (short
recordings, no speech). It is **not** a failure, and those videos won't be retried on
future runs.

Real failures are listed individually:

```
  FAIL a1b2c3 (main-library): mp4: HTTP 404
```

---

## Step 6: Verify before you trust it

If you're archiving because you plan to cancel Loom, do these checks first.

**1. Compare counts.** The `Done. N processed` number should match the `Enumerated N`
number from Step 4.

**2. Look for failures in the ledger.** Open `loom_video_list_progress.csv` — it has one
row per video with columns `id`, `name`, `folder`, `visibility`, `share_url`,
`mp4_status`, `transcript_status`, `error`.

```bash
# how many videos didn't download?
grep -c "failed" ~/my-loom-archive/loom_video_list_progress.csv
```

`transcript_status` has three values: `done`, `unavailable` (Loom has no transcript —
fine), and `failed` (something went wrong — worth investigating).

**3. Actually play a few videos.** Pick a long one, a recent one, and one from a folder.
Scrub to the end of each. A file that exists and has a sensible size can still be
truncated — playing it is the only real check.

**4. Spot-check a folder.** Count the files in one folder's directory against what Loom
shows for that folder.

Only after all four should you consider cancelling anything.

---

## When something goes wrong

**`ffmpeg was not found on PATH`**
```
ffmpeg was not found on PATH, and it is required to save HLS videos.
  macOS:   brew install ffmpeg
  Debian:  sudo apt install ffmpeg
  Windows: https://ffmpeg.org/download.html
```
Install it and rerun. The tool checks this at startup so you find out immediately rather
than an hour into a download.

**`Not signed in — no saved session at ...`**
You skipped Step 3, or you're pointing at a different config directory. Run
`loom-archiver auth --dest ~/my-loom-archive`.

**`session expired — run 'loom-archiver auth' to sign in again`**
Loom sessions don't last forever. Run `auth` again; your progress is safe and `run` picks
up where it stopped.

**`Stopped: ...` partway through**
```
Stopped: <reason>
Progress saved to the ledger — rerun to resume.
```
Usually free space dropping below `--floor`, or a drive unmounting. Fix the cause and
rerun the same command.

**`repeated pagination cursor ... refusing to report a partial archive as complete`**
Loom's API returned a page loop. The tool stops rather than quietly giving you a short
archive. Rerun; if it persists, it's a problem on Loom's side — wait and try later.

**`folder nesting deeper than 10`**
Your folders nest deeper than the default limit. Rerun with a higher value, e.g.
`--max-depth 20`.

**A video fails with a 404**
The video's media is gone from Loom's servers. Nothing can recover it — this happens with
old recordings. Check `share_url` in the ledger and try opening it in a browser to
confirm.

---

## Keeping the archive current

Rerunning `run` later picks up anything new and skips what you already have:

```bash
loom-archiver run --dest ~/my-loom-archive
```

Safe to run as often as you like. If your session has expired, run `auth` first.

---

## When you're done

If you archived in order to leave Loom, **sign out of the Loom session** you created in
Step 3 — that invalidates the stored cookie. You can also delete the saved session:

```bash
# macOS
rm -rf ~/Library/Application\ Support/loom-archiver

# Linux
rm -rf ~/.config/loom-archiver
```

Your archive is yours; nothing in it depends on the tool or on Loom.
