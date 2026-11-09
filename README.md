# dropbox-analyzer

A terminal UI for finding out what's using space in your Dropbox. It works
like a file browser where every folder shows its combined size, sorted
largest first.

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
| D                       | delete without confirming               |
| i                       | info, including largest files inside    |
| s                       | toggle sort by size / name              |
| r                       | refresh (fetch changes since last scan) |
| R                       | full rescan                             |
| q                       | quit                                    |

## Shared folders

Shared folders you've added to your Dropbox are included, with a **Shared**
column showing `owner` (you own it) or `member`. The info view (`i`) shows the
owner, your access level, and everyone it's shared with.

Delete confirmations explain who else is affected, following Dropbox's rules:

- Deleting a **shared folder itself** only removes it from your Dropbox. Other
  members keep access, even if you're the owner. To remove it for everyone,
  unshare it on dropbox.com.
- Deleting **anything inside** a shared folder deletes it for everyone the
  folder is shared with.

## Options

- `--rescan` ignores the cache and scans everything again.
- `--logout` forgets the saved login.

## Notes

- "Account usage" comes from Dropbox's quota API and can differ from the
  scanned total: shared folders, team space, and Dropbox's own accounting
  don't always line up with summing file sizes.
- Deleted files can be restored from dropbox.com for the period your plan
  allows.
