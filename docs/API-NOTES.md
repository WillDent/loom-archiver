# Loom API Notes

Everything here was established by observing Loom's live GraphQL API, because it
cannot be established any other way. These notes exist so the next person does not
have to rediscover them.

## Introspection is disabled

Loom's GraphQL endpoint rejects introspection queries, so there is no schema to
generate clients or queries from. The `.graphql` files in `loom_archiver/queries/`
were captured from authenticated browser traffic in a real session. If they are
lost, they can only be recovered by repeating that capture — not by regenerating
them from a schema.

Treat those files as irreplaceable data, not as generated artifacts.

## `FolderSource` must be `"ACTIVE"`

`GetPublishedFolders` requires a non-null `source` variable. The only accepted value
found was `"ACTIVE"`. Thirteen other plausible values — including `MINE`, `OWNED`,
and `CREATED_BY_ME` — were rejected by the API.

## Both discovery passes are mandatory

There is no "return everything" query. Specifically:

- An unfiltered `CREATED_BY_ME` query returns **exactly** the `NOT_IN_FOLDER` set.
  It contains zero foldered videos.
- Videos inside folders are reachable **only** by walking folders and querying each
  one by `folderId`.

So a complete archive requires both passes, and neither can serve as a check on the
other. There is no independent total to reconcile against — which is why
`loom_archiver/folders.py` raises rather than warns when folder enumeration goes
wrong. A folder that fails to enumerate is a folder whose videos are silently
missing, with nothing to flag it.

## Folder listings return direct children only

Querying a folder by `folderId` returns the videos directly inside it, not those in
its subfolders.

The Loom web UI shows a different number: its per-folder count is a **subtree
total**. A folder the UI labels "10 videos" may return 9 from the API, with the
tenth living in a subfolder. The UI and the API are counting different things and
both are correct.

Verified against a real nested folder on 2026-07-20.

## `endCursor` is non-null even when `hasNextPage` is false

Pagination responses return a populated `endCursor` on the final page. Code that
follows a cursor whenever one is present will paginate forever.

Always gate on `hasNextPage`:

```python
cursor = page_info.get("endCursor") if page_info.get("hasNextPage") else None
```

This behaviour appears only in captured live responses; hand-written test fixtures
tend to set `endCursor` to null on the last page and so never encode it. The fixture
at `tests/fixtures/get_published_folders_live.json` is a real capture and does.

## Authentication

Loom authenticates via Google SSO, which resists scripted login. `loom-archiver auth`
therefore opens a real browser window and waits for you to sign in, then saves the
resulting session.

The session cookie to match is `connect.sid`, by **exact name**. Loose substring
matching on `sid` or `auth` wrongly matches pre-login cookies such as
`loom_oauth_state_v6`, which are present before authentication completes — so a
loose match reports success on a half-finished login.
