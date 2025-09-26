"""Codexer TUI for browsing coding-assistant session logs."""

import argparse
import glob
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Set, Tuple

from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, ListItem, ListView, Static

from rich.align import Align
from rich.console import Group
from rich.markdown import Markdown
from rich.text import Text

CWD_PATTERN = re.compile(r"Current working directory:\s*(.+)", re.IGNORECASE)
CWD_TAG_PATTERN = re.compile(r"<cwd>(.*?)</cwd>", re.IGNORECASE | re.DOTALL)


def _is_plausible_cwd(value: str) -> bool:
    value = value.strip()
    if not value:
        return False
    if value.startswith(("/", "~")):
        return True
    if len(value) >= 2 and value[0].isalpha() and value[1] == ":":
        return True
    return False


@dataclass
class SessionEntry:
    session_id: str
    path: Path
    mtime: float
    timestamp: datetime
    cwd_values: Set[str]
    primary_cwd: Optional[str]
    first_role: Optional[str]
    first_text: Optional[str]
    last_role: Optional[str]
    last_text: Optional[str]
    search_blob: str

    @property
    def short_path(self) -> str:
        home = Path.home()
        try:
            return str(self.path.relative_to(home))
        except ValueError:
            return str(self.path)

    @property
    def display_id(self) -> str:
        identifier = self.session_id or self.path.stem
        if identifier.startswith("rollout-"):
            identifier = identifier[len("rollout-") :]
        if len(identifier) > 28:
            return identifier[:28] + "…"
        return identifier

    @property
    def display_cwd(self) -> str:
        if self.primary_cwd:
            return self.primary_cwd
        if self.cwd_values:
            return sorted(self.cwd_values)[0]
        return "—"

    @property
    def ordered_cwds(self) -> List[str]:
        if not self.cwd_values:
            return []
        ordered = sorted(self.cwd_values)
        if self.primary_cwd and self.primary_cwd in ordered:
            ordered.remove(self.primary_cwd)
            return [self.primary_cwd, *ordered]
        return ordered

    @property
    def relative_time(self) -> str:
        now = datetime.now(timezone.utc if self.timestamp.tzinfo else None)
        delta = now - self.timestamp
        seconds = int(delta.total_seconds())
        if seconds < 0:
            seconds = 0
        if seconds < 60:
            return f"{seconds}s ago"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        if days < 7:
            return f"{days}d ago"
        weeks = days // 7
        if weeks < 5:
            return f"{weeks}w ago"
        months = days // 30
        if months < 12:
            return f"{months}mo ago"
        years = days // 365
        return f"{years}y ago"


@dataclass
class ResumeRequest:
    session_path: Path
    session_id: Optional[str]


@dataclass
class MatchConfig:
    cwd_filter: Optional[str]
    match_any: bool
    sort_by: str
    ascending: bool


def gather_files(targets: Sequence[str]) -> List[Path]:
    files: List[Path] = []
    seen: Set[Path] = set()
    for target in targets:
        expanded = Path(os.path.expanduser(target))
        if expanded.exists():
            if expanded.is_file():
                _record_file(expanded, files, seen)
            elif expanded.is_dir():
                for candidate in sorted(expanded.rglob("*.jsonl")):
                    _record_file(candidate, files, seen)
            continue
        for match in sorted(glob.glob(str(expanded), recursive=True)):
            candidate = Path(match)
            if candidate.is_file():
                _record_file(candidate, files, seen)
    return files


def _record_file(path: Path, files: List[Path], seen: Set[Path]) -> None:
    resolved = path.resolve()
    if resolved not in seen:
        seen.add(resolved)
        files.append(resolved)


def _flatten(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, (int, float, bool)):
        yield str(value)
    elif value is None:
        return
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _flatten(nested)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _flatten(item)


def _collect_cwds(text: str) -> Set[str]:
    cwds: Set[str] = set()
    for match in CWD_PATTERN.finditer(text):
        value = match.group(1).strip()
        if value and _is_plausible_cwd(value):
            cwds.add(value)
    for match in CWD_TAG_PATTERN.finditer(text):
        value = match.group(1).strip()
        if value and _is_plausible_cwd(value):
            cwds.add(value)
    return cwds


def _collect_structured_cwds(entry: Any) -> Tuple[Set[str], Optional[str]]:
    cwds: Set[str] = set()
    primary: Optional[str] = None

    def _visit(value: Any) -> None:
        nonlocal primary
        if isinstance(value, dict):
            for key, nested in value.items():
                if key.lower() == "cwd" and isinstance(nested, str) and nested.strip():
                    cleaned = nested.strip()
                    if _is_plausible_cwd(cleaned):
                        cwds.add(cleaned)
                        primary = cleaned
                else:
                    _visit(nested)
        elif isinstance(value, list):
            for item in value:
                _visit(item)

    _visit(entry)
    return cwds, primary


def _format_snippet(text: str, limit: int = 280) -> str:
    cleaned = " ".join(text.strip().split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "…"


def _iter_content_chunks(content: Any) -> Iterable[str]:
    if isinstance(content, list):
        for chunk in content:
            if isinstance(chunk, dict):
                text = chunk.get("text")
                if isinstance(text, str):
                    yield text
                else:
                    yield from _iter_content_chunks(chunk.get("content"))
            elif isinstance(chunk, str):
                yield chunk
    elif isinstance(content, dict):
        yield from _iter_content_chunks(content.get("content"))
    elif isinstance(content, str):
        yield content


def _iter_entry_texts(entry: dict) -> Iterable[Tuple[Optional[str], str]]:
    role = entry.get("role")

    content = entry.get("content")
    if content is not None:
        for text in _iter_content_chunks(content):
            yield role, text

    text = entry.get("text")
    if isinstance(text, str):
        yield role, text

    payload = entry.get("payload")
    if isinstance(payload, dict):
        payload_role = payload.get("role", role)
        payload_content = payload.get("content")
        if payload_content is not None:
            for text_chunk in _iter_content_chunks(payload_content):
                yield payload_role, text_chunk
        payload_text = payload.get("text")
        if isinstance(payload_text, str):
            yield payload_role, payload_text

    message = entry.get("message")
    if isinstance(message, dict):
        message_role = message.get("role", role)
        message_content = message.get("content")
        if message_content is not None:
            for text_chunk in _iter_content_chunks(message_content):
                yield message_role, text_chunk
        message_text = message.get("text")
        if isinstance(message_text, str):
            yield message_role, message_text


META_PREFIXES = (
    "<environment_context>",
    "<user_instructions>",
    "<developer_instructions>",
    "<system_message>",
)


def _is_meta_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    first_line = stripped.splitlines()[0]
    for prefix in META_PREFIXES:
        if stripped.startswith(prefix) or first_line.startswith(prefix):
            return True
    return False


def parse_session(path: Path) -> Optional[SessionEntry]:
    session_id: Optional[str] = None
    first_role: Optional[str] = None
    first_text: Optional[str] = None
    last_role: Optional[str] = None
    last_text: Optional[str] = None
    blob_parts: List[str] = [str(path)]
    cwds: Set[str] = set()
    primary_cwd: Optional[str] = None
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                if not session_id:
                    potential_id = entry.get("id")
                    if isinstance(potential_id, str) and potential_id:
                        session_id = potential_id
                    else:
                        payload = entry.get("payload")
                        if isinstance(payload, dict):
                            potential_id = payload.get("id")
                            if isinstance(potential_id, str) and potential_id:
                                session_id = potential_id
                entry_had_text = False
                for role, text in _iter_entry_texts(entry):
                    if not isinstance(text, str):
                        continue
                    entry_had_text = True
                    blob_parts.append(text)
                    if first_text is None and not _is_meta_text(text):
                        first_text = text.strip()
                        first_role = role
                    if text.strip():
                        last_text = text.strip()
                        last_role = role
                        cwds.update(_collect_cwds(text))
                        tag_matches = [
                            match.group(1).strip()
                            for match in CWD_TAG_PATTERN.finditer(text)
                            if _is_plausible_cwd(match.group(1))
                        ]
                        if tag_matches and not primary_cwd:
                            primary_cwd = tag_matches[-1]
                if not entry_had_text:
                    blob_parts.extend(_flatten(entry))
                structured_cwds, structured_primary = _collect_structured_cwds(entry)
                cwds.update(structured_cwds)
                if structured_primary:
                    primary_cwd = structured_primary
    except OSError as exc:
        print(f"Failed to read {path}: {exc}", file=sys.stderr)
        return None

    if not session_id:
        session_id = path.stem
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    timestamp = datetime.fromtimestamp(mtime, tz=timezone.utc)
    search_blob = "\n".join(blob_parts).lower()
    return SessionEntry(
        session_id=session_id,
        path=path,
        mtime=mtime,
        timestamp=timestamp,
        cwd_values=cwds,
        primary_cwd=primary_cwd,
        first_role=first_role,
        first_text=first_text,
        last_role=last_role,
        last_text=last_text,
        search_blob=search_blob,
    )


def apply_filters(
    sessions: Sequence[SessionEntry],
    query: str,
    config: MatchConfig,
) -> List[SessionEntry]:
    tokens = [token for token in query.lower().split() if token]
    cwd_filter = config.cwd_filter

    def matches(entry: SessionEntry) -> bool:
        if cwd_filter:
            if cwd_filter not in entry.cwd_values:
                return False
        if not tokens:
            return True
        if config.match_any:
            return any(token in entry.search_blob for token in tokens)
        return all(token in entry.search_blob for token in tokens)

    filtered = [entry for entry in sessions if matches(entry)]
    if config.sort_by == "time":
        filtered.sort(key=lambda item: item.mtime, reverse=not config.ascending)
    elif config.sort_by == "path":
        filtered.sort(key=lambda item: str(item.path), reverse=not config.ascending)
    elif config.sort_by == "id":
        filtered.sort(key=lambda item: item.session_id, reverse=not config.ascending)
    return filtered


class CwdPrompt(ModalScreen[str]):
    """Modal input for CWD filtering."""

    def __init__(self, current: Optional[str]) -> None:
        super().__init__()
        self.current = current or ""

    def compose(self) -> ComposeResult:
        yield Container(
            Vertical(
                Static("Set CWD filter (leave blank for any):", id="prompt-label"),
                Input(value=self.current, placeholder="/path/to/project", id="prompt-input"),
                Static("Enter to accept · Esc to cancel", id="prompt-hint"),
                id="prompt-body",
            ),
            id="prompt-container",
        )

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or "")

    def on_key(self, event: events.Key) -> None:
        if (event.key == "c" and (event.ctrl or event.meta)):
            event.stop()
            return
        if event.key == "escape":
            self.dismiss(None)


class CodexerApp(App):
    TITLE = "Codexer"
    ENABLE_MOUSE_CAPTURE = False
    CSS = """
    Screen {
        background: #0f1117;
    }

    Container#layout {
        height: 100%;
    }

    Vertical#main-column {
        height: 100%;
    }

    Horizontal#control-row {
        padding: 0 1;
        background: #1b1e24;
        border-bottom: tall #2a3240;
        height: auto;
        min-height: 3;
    }

    Input#search-input {
        border: tall #5c6bc0;
        background: #101218;
        color: #f0f3ff;
        width: 1fr;
        max-width: 64;
        margin-right: 1;
    }

    Static#status-text {
        color: #9fa8da;
        align-vertical: middle;
        padding: 0 1;
    }

    Horizontal#content-row {
        height: 1fr;
    }

    ListView#session-list {
        width: 48;
        min-width: 36;
        border: tall #37474f;
    }

    Vertical#detail-column {
        padding: 1 2;
        width: 1fr;
    }

    ListItem {
        background: #11151c;
        padding: 1 1;
    }

    ListItem.--highlight {
        background: #2c3f66;
        border-left: tall #90caf9;
    }

    ListItem.--highlight Static {
        color: #e3f2fd;
    }

    .list-id {
        color: #64b5f6;
    }

    .list-time {
        color: #ffb74d;
    }

    .list-cwd {
        color: #aed581;
    }

    Static#detail-panel {
        padding: 0;
    }

    Static#prompt-label {
        color: #90caf9;
    }

    Static#prompt-hint {
        color: #78909c;
        padding-top: 1;
    }

    Container#prompt-container {
        align: center middle;
    }

    Vertical#prompt-body {
        border: round #5c6bc0;
        background: #101218;
        padding: 1 2;
        width: 60;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("q", "quit", "Quit"),
        Binding("/", "focus_search", "Search"),
        Binding("f", "prompt_cwd", "Filter CWD"),
        Binding("c", "prompt_cwd", "Filter CWD"),
        Binding("s", "cycle_sort", "Sort"),
        Binding("a", "toggle_match_mode", "Match ALL/ANY"),
        Binding("r", "refresh", "Refresh"),
        Binding("enter", "resume_session", "Resume in Codex", key_display="↵", priority=True),
    ]

    def __init__(
        self,
        sessions: Sequence[SessionEntry],
        initial_query: str,
        config: MatchConfig,
    ) -> None:
        super().__init__()
        self.sessions = list(sessions)
        self.query_text = initial_query
        self.config = config
        self.filtered: List[SessionEntry] = []
        self.status_message: str = ""
        self._cwd_last: Optional[str] = config.cwd_filter
        self._last_resume_command: Optional[str] = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Container(id="layout"):
            with Vertical(id="main-column"):
                with Horizontal(id="control-row"):
                    yield Input(
                        value=self.query_text,
                        placeholder="Type to search (space-separated tokens)",
                        id="search-input",
                    )
                    yield Static(self._status_text(), id="status-text")
                with Horizontal(id="content-row"):
                    yield ListView(id="session-list")
                    with Vertical(id="detail-column"):
                        yield Static("", id="detail-panel")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_results()
        list_view = self.query_one(ListView)
        if self.filtered:
            list_view.index = 0
            self.show_entry(self.filtered[0])
        self.query_one("Input#search-input", Input).focus()

    def _status_text(self) -> str:
        sort_desc = {
            "time": "Time",
            "path": "Path",
            "id": "ID",
        }.get(self.config.sort_by, self.config.sort_by)
        direction = "↑" if self.config.ascending else "↓"
        cwd_text = self.config.cwd_filter or "any"
        match_mode = "ANY" if self.config.match_any else "ALL"
        base = (
            f"Sort: {sort_desc} {direction} · CWD: {cwd_text} · Match: {match_mode} · "
            f"Sessions: {len(self.filtered)}/{len(self.sessions)}"
        )
        if self.status_message:
            return f"{base} · {self.status_message}"
        return base

    def _set_status(self, message: str = "") -> None:
        self.status_message = message
        status = self.query_one("Static#status-text", Static)
        status.update(self._status_text())

    def refresh_results(self) -> None:
        self.filtered = apply_filters(self.sessions, self.query_text, self.config)
        list_view = self.query_one(ListView)
        list_view.clear()
        for entry in self.filtered:
            list_view.append(self._to_list_item(entry))
        self._set_status(self.status_message)
        if not self.filtered:
            detail = self.query_one("Static#detail-panel", Static)
            detail.update("[dim]No sessions match the current filters.[/dim]")

    def _to_list_item(self, entry: SessionEntry) -> ListItem:
        last_text = (entry.last_text or "").replace("\n", " ")
        snippet = (last_text[:100] + "…") if len(last_text) > 100 else last_text
        time_str = entry.relative_time
        cwd_display = entry.display_cwd
        text = Text()
        text.append(f"[{time_str}] ", style="bold #ffb74d")
        text.append(f"{entry.display_id}", style="bold #64b5f6")
        text.append(" · ")
        text.append(entry.short_path, style="italic #b0bec5")
        text.append("\n")
        text.append(f"cwd: {cwd_display}", style="#aed581")
        if snippet:
            text.append("\n")
            text.append(snippet)
        return ListItem(Static(text))

    def action_focus_search(self) -> None:
        self.query_one("Input#search-input", Input).focus()

    def action_prompt_cwd(self) -> None:
        self.push_screen(CwdPrompt(self.config.cwd_filter), callback=self._on_cwd_selected)

    def _on_cwd_selected(self, value: Optional[str]) -> None:
        if value is None:
            return
        cwd = value or None
        if cwd:
            self._cwd_last = cwd
        self.config = MatchConfig(
            cwd_filter=cwd,
            match_any=self.config.match_any,
            sort_by=self.config.sort_by,
            ascending=self.config.ascending,
        )
        self.refresh_results()

    def action_cycle_sort(self) -> None:
        order = ["time", "path", "id"]
        idx = order.index(self.config.sort_by) if self.config.sort_by in order else 0
        next_sort = order[(idx + 1) % len(order)]
        self.config = MatchConfig(
            cwd_filter=self.config.cwd_filter,
            match_any=self.config.match_any,
            sort_by=next_sort,
            ascending=False,
        )
        self.refresh_results()

    def action_toggle_match_mode(self) -> None:
        self.config = MatchConfig(
            cwd_filter=self.config.cwd_filter,
            match_any=not self.config.match_any,
            sort_by=self.config.sort_by,
            ascending=self.config.ascending,
        )
        self.refresh_results()

    def action_refresh(self) -> None:
        updated: List[SessionEntry] = []
        for entry in self.sessions:
            refreshed = parse_session(entry.path)
            if refreshed:
                updated.append(refreshed)
        self.sessions = updated
        self.refresh_results()
        self._set_status("Refreshed")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search-input":
            self.query_text = event.value
            self.refresh_results()
            self._set_status("")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if not self.filtered:
            return
        if 0 <= event.index < len(self.filtered):
            entry = self.filtered[event.index]
            self.show_entry(entry)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if not self.filtered:
            return
        list_view = getattr(event, "list_view", None) or self.query_one(ListView)
        index = list_view.index
        if index is None:
            return
        if 0 <= index < len(self.filtered):
            entry = self.filtered[index]
            self.show_entry(entry)


    @on(events.Click, "#status-text")
    def on_status_clicked(self, event: events.Click) -> None:
        event.stop()
        if self.config.cwd_filter is None:
            if not self._cwd_last:
                self._cwd_last = str(Path.cwd())
            new_cwd = self._cwd_last
            message = f"CWD filter: {new_cwd}"
        else:
            self._cwd_last = self.config.cwd_filter
            new_cwd = None
            message = "CWD filter: global"
        self.config = MatchConfig(
            cwd_filter=new_cwd,
            match_any=self.config.match_any,
            sort_by=self.config.sort_by,
            ascending=self.config.ascending,
        )
        self.refresh_results()
        self._set_status(message)

    def on_key(self, event: events.Key) -> None:
        if event.key == "enter" and isinstance(self.focused, ListView):
            event.stop()
            self.action_resume_session()
        elif event.key == "escape" and isinstance(self.focused, Input):
            self.action_focus_search()

    def _current_entry(self) -> Optional[SessionEntry]:
        if not self.filtered:
            return None
        list_view = self.query_one(ListView)
        index = list_view.index
        if index is None:
            index = 0
        if not (0 <= index < len(self.filtered)):
            return None
        return self.filtered[index]

    def action_resume_session(self) -> None:
        entry = self._current_entry()
        if entry is None:
            return
        self.action_focus_search()
        codex_executable = shutil.which("codex")
        if codex_executable is None:
            self._set_status("codex CLI not found on PATH")
            self.bell()
            return
        self._set_status("")
        self.exit(ResumeRequest(entry.path, entry.session_id))

    def show_entry(self, entry: SessionEntry) -> None:
        detail = self.query_one("Static#detail-panel", Static)
        sections: List[Any] = []

        title = Align.center(Text(entry.session_id, style="bold #64b5f6"))
        sections.append(title)

        if entry.first_text:
            first_line = Text()
            first_line.append(
                f"First ({entry.first_role or 'unknown'}): ",
                style="bold #9fa8da",
            )
            first_line.append(_format_snippet(entry.first_text), style="#e0f2f1")
            sections.append(first_line)

        if entry.last_text:
            last_line = Text()
            last_line.append(
                f"Last ({entry.last_role or 'unknown'}): ",
                style="bold #9fa8da",
            )
            last_line.append(_format_snippet(entry.last_text), style="#fff3e0")
            sections.append(last_line)

        metadata_lines = [
            f"**Path:** `{entry.short_path}`",
            f"**Modified:** {entry.timestamp.astimezone().isoformat()} ({entry.relative_time})",
        ]
        ordered_cwds = entry.ordered_cwds
        if ordered_cwds:
            metadata_lines.append(
                "**CWDs:** ``{}``".format("`, `".join(ordered_cwds))
            )
        sections.append(Markdown("\n\n".join(metadata_lines)))
        resume_target = entry.session_id or str(entry.path)
        resume_command = f"codex resume {resume_target}"
        sections.append(Text(f"Resume command: {resume_command}"))
        self._last_resume_command = resume_command
        sections.append(Text(f"Log file: {entry.path}"))

        detail.update(Group(*sections))


def build_session_list(paths: Sequence[Path]) -> List[SessionEntry]:
    entries: List[SessionEntry] = []
    for path in paths:
        entry = parse_session(path)
        if entry:
            entries.append(entry)
    return entries


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Browse coding-assistant session logs in a TUI.")
    parser.add_argument(
        "targets",
        nargs="*",
        default=["~/.codex/sessions/**/*.jsonl"],
        help="Files, directories, or glob patterns to include (default: ~/.codex/sessions/**/*.jsonl)",
    )
    parser.add_argument(
        "-k",
        "--keyword",
        action="append",
        help="Initial keyword(s) to pre-populate the search box.",
    )
    parser.add_argument(
        "--cwd",
        help='Initial CWD filter (default: current working directory; pass "" to disable).',
    )
    parser.add_argument(
        "--match-any",
        action="store_true",
        help="Use OR semantics for the initial keyword list.",
    )
    parser.add_argument(
        "--sort-by",
        choices=["time", "path", "id"],
        default="time",
        help="Initial sort column (default: time).",
    )
    parser.add_argument(
        "--ascending",
        action="store_true",
        help="Sort ascending instead of descending.",
    )
    args = parser.parse_args(argv)

    paths = gather_files(args.targets)
    if not paths:
        print("No session files found for the given targets.", file=sys.stderr)
        return 1

    entries = build_session_list(paths)
    if not entries:
        print("No parsable sessions found.", file=sys.stderr)
        return 1

    initial_query = " ".join(args.keyword or [])

    if args.cwd is None:
        cwd_filter: Optional[str] = str(Path.cwd())
    else:
        cwd_filter = str(Path(args.cwd).expanduser()) if args.cwd else None

    config = MatchConfig(
        cwd_filter=cwd_filter,
        match_any=args.match_any,
        sort_by=args.sort_by,
        ascending=args.ascending,
    )

    app = CodexerApp(entries, initial_query=initial_query, config=config)
    result = app.run()
    if isinstance(result, ResumeRequest):
        codex_executable = shutil.which("codex")
        if codex_executable is None:
            print(
                "Unable to resume session: `codex` executable not found on PATH.",
                file=sys.stderr,
            )
            return 1
        target = result.session_id or str(result.session_path)
        os.execv(codex_executable, ["codex", "resume", target])
    return 0


def cli() -> None:
    """Console script entry point."""
    raise SystemExit(main())


if __name__ == "__main__":  # pragma: no cover - module execution guard
    cli()
