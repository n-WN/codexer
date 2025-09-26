# Codexer

Codexer is a Textual-based terminal UI for browsing and searching
coding-assistant session logs (Codex/Claude/other JSONL transcripts). It lets you
filter by keywords, restrict to a specific working directory, and jump through
sessions with a colorful, keyboard-friendly interface.

## Features

- Instant search over JSONL transcripts with AND/OR keyword modes.
- Per-session metadata panel with first/last message snippets and timestamps.
- CWD filter that defaults to your current `pwd`, ensuring you see the traces
  relevant to the project you are in.
- Click the status-bar CWD indicator to toggle between the current-directory
  filter and a global view.
- Sorting controls for newest-first, path, or session ID ordering.
- Keyboard shortcuts: `/` focus search, `f`/`c` update CWD filter, `s` cycle sort,
  `a` toggle match-all vs. match-any, `r` refresh from disk, `enter` resume the
  session in Codex, `q`/`Ctrl+C` exit.

## Quick Start

`uv` can install the CLI as an isolated tool so that `codexer`
is available on your `PATH` without managing a virtual environment manually.

```bash
# from the project root (or replace "." with the git URL once published)
uv tool install .     # installs the packaged entry point
codexer --help        # verify the command is on your PATH
```

If this is your first time using `uv tool`, ensure the tool bin directory is on
your shell `PATH`:

```bash
uv tool update-shell
```

Once installed, run the TUI directly with `codexer`.

By default it scans `~/.codex/sessions/**/*.jsonl`; pass a different path or glob
if your logs live elsewhere.

Use the search bar to type tokens (space separated). Press `f` to adjust the CWD
filter; submit a blank value to clear it.

## Develop

```bash
cd ~/codexer
uv tool install -e .    # optional: expose the script while you iterate
uv sync                 # creates .venv and installs dependencies
uv run codexer          # launches the TUI (defaults to ~/.codex/sessions)
```

## Dependencies

The project targets Python 3.11+ and depends on:

- [Textual](https://textual.textualize.io/) for the terminal UI.
- [Rich](https://rich.readthedocs.io/) for colored rendering.


## Entry Points

- `codexer` (console script) – launches the TUI.
- `python -m codexer` – equivalent module entry point.

## Development Notes

- Session files are parsed lazily; only JSON objects with `content` arrays/string
  fields contribute text. The tool automatically falls back to scanning remaining
  keys when no text chunks are present.
- Timestamp ordering is derived from file modification time; the list view
  defaults to newest first.
- Colors and layout follow a dark theme optimized for 24-bit terminals.
- See `docs/codex-sessions.md` for details on Codex JSONL formats, the CWD
  metadata variants, refresh behavior, and how to resume a session in Codex.

Contributions and tweaks are welcome—drop new widgets or filters as needed!
