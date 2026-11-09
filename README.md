# dropbox-analyzer

A terminal UI for finding what's using space in your Dropbox: a file browser
where every folder shows its total size, largest first.

> [!WARNING]
> - **It deletes for real**, including in shared folders, where deleting
>   anything *inside* removes it for every member.
> - Unofficial, not affiliated with Dropbox, no warranty.

## Setup

- clone the repo
- `uv run dropbox-analyzer`
- follow the instructions

> Coming soon - a released version so cloning isn't necessary!

The first scan can take a while for big accounts. Later runs only fetch
changes.

## License

[MIT](LICENSE)
