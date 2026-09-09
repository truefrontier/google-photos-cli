# google-photos-cli

Unofficial read-only CLI for [Google Photos](https://photos.google.com). Sign in once in Chrome. Then list albums, search your library, and inspect items.

Built by [True Frontier](https://truefrontierapps.com). Not affiliated with Google.

The session stays on your machine, in a local Chrome profile. This tool does not print cookies, and it does not ship a cloud login.

The official Photos Library API is locked down for most third-party apps. This CLI drives the signed-in web UI the same way [loom-cli](https://github.com/truefrontier/loom-cli) and [google-recorder-cli](https://github.com/truefrontier/google-recorder-cli) do.

## Requirements

- Python 3.10+
- Google Chrome

## Install

```bash
pipx install git+https://github.com/truefrontier/google-photos-cli.git
```

Or from a checkout:

```bash
pipx install .
```

Playwright uses your installed Chrome (`channel="chrome"`). You do not need `playwright install chromium`.

## Commands

```bash
photos login
photos albums
photos albums --format json
photos list
photos list --album <id-or-url>
photos list --album <id> --limit 20
photos search "kids"
photos search "kids" --format json
photos info <id-or-url>
photos download <id-or-url>
photos download <id> -o ~/Downloads
```

`photos login` opens Chrome. Sign in, wait until Photos loads, and the window closes. The profile is saved at `~/.photos-cli/chrome-profile/`.

If a command says the session expired, run `photos login` again.

## How it works

After login, the CLI opens Photos pages with your saved Chrome profile and reads album and media tiles from the signed-in UI. Google Photos talks to itself through opaque `batchexecute` RPCs, so this first cut stays on the page surface rather than hard-coding fragile RPC ids.

Read-only against the library. It can download originals you already own. It cannot upload, edit, delete, or share.


## Agent output

Inspired by [CLI Printing Press](https://github.com/mvanhorn/cli-printing-press) agent UX:

- Piped stdout auto-uses JSON when `--format` is `table` (no `--json` needed)
- `--compact` for high-gravity fields only
- `--select id,title` to project JSON/CSV fields
- `--csv` for spreadsheet-friendly rows
- `--quiet` / `-q` to suppress status lines
- Exit codes: `0` ok, `2` usage, `3` not found, `4` auth, `5` api/runtime

## Limits

- Your library only.
- `list` and `search` load what the page renders after a short scroll. Raise `--limit` only within that window.
- `info` is thinner than the labels returned by `list` and `search`.
- `download` fetches the original through Photos media URLs (`=dv` for video, `=d` for photos) and saves the suggested filename.
