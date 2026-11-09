# dropbox-analyzer

A terminal UI for finding what's using space in your Dropbox: a file browser
where every folder shows its total size, largest first.

> [!WARNING]
> - **It deletes for real**, including in shared folders, where deleting
>   anything *inside* removes it for every member. Deleted items can be
>   restored from dropbox.com only for as long as your plan keeps them.
> - `D` deletes with no confirmation.
> - The shared-folder delete behavior is based on Dropbox forum posts, not
>   tested against real accounts. Tests only use a fake client.
> - Unofficial, not affiliated with Dropbox, no warranty.

## Setup

1. Create an app at <https://www.dropbox.com/developers/apps> with **Scoped
   access** and **Full Dropbox**. On its **Permissions** tab, enable
   `account_info.read`, `files.metadata.read`, `files.content.write`, and
   `sharing.read`, then click **Submit**.
2. Run `uv run dropbox-analyzer`. The first run asks for the app key (or set
   `DROPBOX_APP_KEY`) and walks you through logging in.

The first scan can take a while for big accounts. Later runs only fetch
changes.

## License

[MIT](LICENSE)
