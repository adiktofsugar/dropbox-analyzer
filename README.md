# dropbox-analyzer

A terminal UI for finding what's using space in your Dropbox: a file browser
where every folder shows its total size, largest first.

> [!WARNING]
> - **It deletes for real**, including in shared folders, where deleting
>   anything *inside* removes it for every member.
> - Unofficial, not affiliated with Dropbox, no warranty.

## Setup

Pick a release from the Releases tab; each one has instructions for running
it with [uv](https://docs.astral.sh/uv/). Then follow the instructions it
prints.

The first scan can take a while for big accounts. Later runs only fetch
changes.

## License

[MIT](LICENSE)
