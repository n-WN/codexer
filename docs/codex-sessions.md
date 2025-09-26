# Codex Session Notes

Codexer reads Codex transcripts stored as JSON Lines. Inspecting
recent exports (CLI 0.34.0 through 0.41.0) shows that the schema evolved, so we
capture the variants here along with a few UX tips.

## Line formats you will see

- **`type: session_meta`** – session metadata with `payload.id`, `payload.cwd`,
  CLI build info, and sometimes a long `<user_instructions>` blob.
- **`type: response_item`** – streaming frames from Codex. The message text now
  lives under `payload.content[*].text`; older runs put it directly in a top-
  level `content` array.
- **`type: message` (legacy)** – pre-`response_item` format. The role and content
  sit on the top level.
- **Other records** – `type: reasoning`, `type: function_call`,
  `type: function_call_output`, and `record_type: state` capture planning notes,
  tool invocations, and shell output. They rarely include plain text.

## How CWD metadata is encoded

Codex changed how it records the working directory:

1. Older logs include a plain sentence: `Current working directory: ...`.
2. Newer logs (September 2025+) wrap the path in XML-style tags:
   `<environment_context> ... <cwd>/path</cwd> ... </environment_context>`.
3. The same value is duplicated in structured JSON such as
   `payload.turn_context.cwd` or `payload.cwd`.

The explorer now checks all three forms, so filters that rely on the CWD keep
working even as the format shifts.

## Why the list can briefly show “ListItem” after mashing `r`

Pressing `r` triggers a full re-parse of every session. Textual clears the
`ListView` before the new entries finish rendering; during that split second the
library shows placeholder labels (literally `ListItem`). Wait a moment and the
rows will refill with the real session summaries. If they do not, confirm that
filters (keywords/CWD) still match something and try a single refresh.

## Jumping straight into a Codex session

- Use the arrow keys to highlight a session, then press `enter`.
  - When the `codex` CLI is available, the explorer exits and launches
    `codex resume <session-id>` (the detail pane shows the exact command plus
    the underlying log path so you can copy it to another terminal if you
    prefer).
  - If `codex` is missing, the status bar will say "codex CLI not found on PATH"
    and the app will stay open.
- When you quit Codex, you land back in the shell you launched the explorer
  from; the explorer process is gone (no extra cleanup needed).

### UX shortcuts

- The session list keeps focus by default; hit `/` to jump to the search bar and
  `Esc` to drop back to the list.
- The detail pane shows a plain-text `Resume command: ...` line.
- Text selection follows your terminal’s rules; see the note below for
  limitations when mouse reporting is active.
- Click the status bar’s `CWD` indicator to toggle between the current
  directory filter and a global view.

Keep this file updated as Codex evolves so the parser stays resilient.

### About selecting and copying text

Terminal TUIs that enable mouse reporting (including Textual apps) prevent the
terminal emulator from doing “normal” drag-to-select while the app is running.
Most emulators provide a modifier to bypass the report and capture the
selection themselves:

- On Linux (and most Windows terminals), hold `Shift` while dragging with the
  mouse to start a selection.
- On macOS inside the VS Code integrated terminal, turn on the
  `Terminal › Integrated: Mac Option Click Forces Selection` setting, then hold
  the `Option` (⌥/`Alt`) key while you drag.

If you rely on copying with `⌘C`/`Ctrl+C` without modifiers, you’ll need to
configure your terminal to ignore mouse reporting or temporarily suspend the app
(`Ctrl+Z` followed by `fg` afterwards) before selecting text.
