# dropbox-analyzer

A terminal UI for finding out what's using space in your Dropbox. It works
like a file browser where every folder shows its combined size, sorted
largest first.

> [!WARNING]
> This tool can delete files from your Dropbox, and from other people's
> Dropbox when the files are in a shared folder. It's an unofficial project,
> not affiliated with or endorsed by Dropbox, and provided as-is with no
> warranty. Read [Disclaimers and known limitations](#disclaimers-and-known-limitations)
> before deleting anything you care about.

## Setup

1. Create a Dropbox app at <https://www.dropbox.com/developers/apps>:
   - **Scoped access**, **Full Dropbox**
   - On the **Permissions** tab, enable `account_info.read`,
     `files.metadata.read`, `files.content.write` (needed for delete), and
     `sharing.read` (shared folder owners and members), then click
     **Submit**.
   - No redirect URI or app secret is needed; login uses OAuth with PKCE.
2. Run it:

   ```sh
   uv run dropbox-analyzer
   ```

   The first run asks for your app key (or set `DROPBOX_APP_KEY`), prints a
   Dropbox authorization link, and asks you to paste the code it gives you
   back. The refresh token is saved to
   `~/.config/dropbox-analyzer/token.json`, so you only do this once.

The first scan lists every file in your Dropbox and can take a while for big
accounts. The result is cached in `~/.cache/dropbox-analyzer/`, and later runs
only fetch what changed.

## Keys

| Key                     | Action                                  |
| ----------------------- | --------------------------------------- |
| click / enter           | open folder                             |
| backspace / left / h    | go up                                   |
| d                       | delete, after a confirmation            |
| D                       | delete without the confirmation         |
| i                       | info, including largest files inside    |
| s                       | toggle sort by size / name              |
| r                       | refresh (fetch changes since last scan) |
| R                       | full rescan                             |
| q                       | quit                                    |

## Shared folders

Shared folders you've added to your Dropbox are included, with a **Shared**
column showing `owner` (you own it) or `member`. The info view (`i`) shows the
owner, your access level, and everyone it's shared with.

Delete confirmations explain who else is affected. As we understand
Dropbox's behavior (see the caveats below):

- Deleting a **shared folder itself** only removes it from your Dropbox. Other
  members keep the folder and everything in it, even if you're the owner. To
  remove it for everyone, unshare it on dropbox.com.
- Deleting **anything inside** a shared folder deletes it for everyone the
  folder is shared with.

## Options

- `--rescan` ignores the cache and scans everything again.
- `--logout` forgets the saved login.

## Disclaimers and known limitations

### Deleting

- **Deletes are real.** `d` and `D` call Dropbox's delete API. Deleted items
  go to Dropbox's deleted files and can usually be restored from dropbox.com,
  but only for as long as your plan keeps deleted files. After that they're
  gone.
- **`D` skips the confirmation.** It deletes the highlighted item right away,
  so you won't see any of the shared-folder warnings. It still stops if the
  item changed (below).
- **Changed items ask before deleting.** The view can be out of date (edits
  on another device, or by people you share with). If the item changed since
  your last scan, the delete stops and asks "This changed since <time>. Delete
  anyway?". For files, Dropbox checks this itself by comparing revisions, so
  it's reliable. Folders have no revision in Dropbox's API, so the app fetches
  changes just before deleting instead. Something added to a folder in the
  moment between that check and the delete would be deleted with it.

### Shared folders

- **The delete rules above aren't verified.** They come from Dropbox's
  community forum, not from Dropbox's API documentation or our own testing.
  In particular, we haven't confirmed that deleting a shared folder through
  the API behaves the same as deleting it in the Dropbox app or website.
- **Owner deletes are the least certain case.** We don't know what happens to
  ownership if the owner deletes a shared folder: whether it passes to another
  member or the folder is left without an owner.
- **Re-adding vs. restoring.** A shared folder you've deleted is reportedly
  re-added from the Shared page on dropbox.com rather than restored from
  deleted files. We haven't verified this.
- **Ownership comes from Dropbox's sharing API.** If that call fails (for
  example, the `sharing.read` permission isn't enabled), folders show as
  `shared` with no owner, and the delete warnings can't tell whether you own
  them.
- **Only folders in your Dropbox are included.** Shared folders you haven't
  added to your Dropbox, and shared *files* or links, aren't shown.

### Sizes and storage

- **The scanned total isn't your quota.** It's the sum of the files listed.
  "Account usage" comes from Dropbox's quota API, and the two can differ.
  Whether shared folders count against your quota depends on your plan and
  who owns them.
- **Only current files are counted.** Version history and deleted files aren't
  shown.
- **Some files may show as 0 B.** Cloud-only documents (such as Google Docs
  stored in Dropbox) might be listed with no size. We haven't checked this.
- **Sizes use 1024-based units**, so 1 GB here is 1024³ bytes.

### Testing and account types

- **Tested against a fake Dropbox client only.** The automated tests use
  made-up data, so real accounts may hit edge cases they don't cover.
- **Business/team accounts aren't supported properly.** The scan starts at
  your personal folder, so team space folders outside it may be missing.
  This hasn't been tested with a team account at all.
- **Very large accounts may be slow or fail.** Dropbox's documentation warns
  that listing a whole account in one go can run into performance problems
  or errors with very large numbers of files. The full listing is also held in
  memory.

### Privacy and security

- **Your login and file list are stored on disk.** The login token is in
  `~/.config/dropbox-analyzer/token.json`, readable only by your user. It
  grants the permissions listed in Setup, including delete, until you run
  `--logout` or revoke the app at
  <https://www.dropbox.com/account/connected_apps>. The cache in
  `~/.cache/dropbox-analyzer/` contains the name, path, and size of every
  file in your Dropbox, in plain text.
- **You use your own Dropbox app.** Each person creates their own app key
  (see Setup), so nothing goes through anyone else's app or server. All
  requests go directly from your machine to Dropbox.

### Other

- **Single-click to open uses a private Textual method.** A future version of
  Textual could break it; clicking twice or pressing enter would still work.

## License

[MIT](LICENSE)
