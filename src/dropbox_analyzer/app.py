import argparse

import dropbox
from dropbox.exceptions import ApiError, AuthError
from rich.bar import Bar
from rich.filesize import decimal as human_size
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Label, Static

from . import auth, sharing
from .scan import (
    Node,
    Snapshot,
    build_tree,
    full_scan,
    load_snapshot,
    refresh,
    remove_subtree,
    save_snapshot,
)
from .sharing import SharedFolder

BAR_WIDTH = 20
UP_KEY = "__up__"


def percent_of(part: int, whole: int) -> float:
    if whole == 0:
        return 0.0
    return 100 * part / whole


class ClickToOpenTable(DataTable):
    # Stock DataTable only selects on clicking the already-highlighted row,
    # so move the cursor first and the stock handler sees a repeat click.

    # Here, not on the app, so they beat DataTable's own "left".
    BINDINGS = [Binding("backspace,left,h", "app.up", "Up")]

    async def _on_click(self, event) -> None:
        meta = event.style.meta
        row_index = meta.get("row")
        column_index = meta.get("column")
        clicked_a_row = row_index is not None and row_index >= 0
        if clicked_a_row and column_index is not None:
            self.cursor_coordinate = Coordinate(row_index, column_index)
        await super()._on_click(event)


class ConfirmDelete(ModalScreen[bool]):
    BINDINGS = [
        Binding("y", "confirm", "Delete"),
        Binding("n,escape", "cancel", "Cancel"),
    ]

    def __init__(
        self, node: Node, warnings: list[Text], title: str | None = None
    ) -> None:
        super().__init__()
        self.node = node
        self.warnings = warnings
        self.title = title

    def compose(self) -> ComposeResult:
        node = self.node
        kind = "folder" if node.is_dir else "file"
        details = f"{human_size(node.size)}"
        if node.is_dir:
            details += f", {node.file_count:,} files"
        with Vertical(id="dialog"):
            yield Label(self.title or f"Delete this {kind}?", id="title")
            yield Label(Text(node.path_display, style="bold"))
            yield Label(details)
            for warning in self.warnings:
                yield Label(warning, classes="delete-warning")
            yield Label(
                "It goes to Dropbox's deleted files and can be restored "
                "from dropbox.com for a limited time.",
                classes="hint",
            )
            with Horizontal(id="buttons"):
                yield Button("Delete (y)", variant="error", id="delete")
                yield Button("Cancel (n)", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    @on(Button.Pressed, "#delete")
    def action_confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(False)


class InfoScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,i,q", "close", "Close")]
    LARGEST_COUNT = 15

    def __init__(self, node: Node, root: Node, analyzer: "DropboxAnalyzer") -> None:
        super().__init__()
        self.node = node
        self.root = root
        self.analyzer = analyzer
        self.shared_root = node.shared_root()

    def _rows(self) -> list[tuple[str, str]]:
        node = self.node
        size_text = human_size(node.size)
        # Under 1 kB, human_size already gives the exact byte count.
        if node.size >= 1000:
            size_text += f"  ({node.size:,} bytes)"
        rows = [
            ("Name", node.name),
            ("Path", node.path_display),
            ("Type", "Folder" if node.is_dir else "File"),
            ("Size", size_text),
        ]
        if node.parent is not None:
            parent_share = percent_of(node.size, node.parent.size)
            rows.append(("Of parent", f"{parent_share:.1f}%"))
        total_share = percent_of(node.size, self.root.size)
        rows.append(("Of scanned total", f"{total_share:.2f}%"))
        if node.is_dir:
            rows.append(("Files", f"{node.file_count:,}"))
            rows.append(("Subfolders", f"{node.folder_count:,}"))
        if node.modified is not None:
            rows.append(("Modified", node.modified.strftime("%Y-%m-%d %H:%M UTC")))
        if node.entry is not None and node.entry.id:
            rows.append(("ID", node.entry.id))
        if node.entry is not None and node.entry.content_hash:
            rows.append(("Content hash", node.entry.content_hash))
        rows.extend(self._sharing_rows())
        return rows

    def _sharing_rows(self) -> list[tuple[str, str]]:
        shared_root = self.shared_root
        if shared_root is None:
            return [("Sharing", "Not shared")]
        if shared_root is self.node:
            rows = [("Sharing", "Shared folder")]
        else:
            rows = [("Sharing", f"Inside shared folder {shared_root.path_display}")]
        folder = self.analyzer.shared_folders.get(shared_root.shared_folder_id)
        if folder is None:
            rows.append(("Owner", "Unknown (couldn't load sharing info)"))
            return rows
        rows.append(("Owner", folder.owner_text))
        if not folder.you_own_it:
            rows.append(("Your access", folder.access_type))
        return rows

    def _largest_files(self) -> Text:
        files = sorted(self.node.iter_files(), key=lambda f: f.size, reverse=True)
        top_files = files[: self.LARGEST_COUNT]
        prefix_length = len(self.node.path_display.rstrip("/")) + 1
        text = Text()
        for file_node in top_files:
            relative_path = file_node.path_display[prefix_length:]
            text.append(f"{human_size(file_node.size):>10}  ", style="bold")
            text.append(relative_path + "\n")
        return text

    def compose(self) -> ComposeResult:
        rows = self._rows()
        label_width = max(len(label) for label, _ in rows)
        details = Text()
        for label, value in rows:
            details.append(f"{label:>{label_width}}  ", style="bold cyan")
            details.append(value + "\n")
        with VerticalScroll(id="dialog"):
            yield Label("Info", id="title")
            yield Static(details, id="details")
            if self.shared_root is not None:
                yield Label("Shared with", classes="section")
                yield Static("Loading members…", id="members")
            if self.node.is_dir and self.node.file_count:
                yield Label("Largest files inside", classes="section")
                yield Static(self._largest_files())
            yield Label("esc / i to close", classes="hint")

    def on_mount(self) -> None:
        if self.shared_root is not None:
            self.load_members(self.shared_root.shared_folder_id)

    @work(thread=True)
    def load_members(self, shared_folder_id: str) -> None:
        client = self.analyzer.client
        account_id = self.analyzer.account_id
        try:
            members = sharing.list_members(client, shared_folder_id, account_id)
        except (ApiError, AuthError) as error:
            message = Text(f"Couldn't load members: {error}", style="red")
            self.app.call_from_thread(self._show_members, message)
            return
        self.app.call_from_thread(self._show_members, self._format_members(members))

    def _format_members(self, members: list[sharing.Member]) -> Text:
        text = Text()
        if not members:
            text.append("No members listed", style="dim")
            return text
        for member in members:
            text.append(f"{member.access_type:>8}  ", style="bold")
            text.append(member.name)
            if member.is_you:
                text.append(" (you)", style="green")
            if member.email:
                text.append(f"  {member.email}", style="dim")
            if member.kind == "invitee":
                text.append("  invited", style="yellow")
            text.append("\n")
        return text

    def _show_members(self, content: Text) -> None:
        self.query_one("#members", Static).update(content)

    def action_close(self) -> None:
        self.dismiss(None)


class DropboxAnalyzer(App):
    TITLE = "Dropbox Analyzer"
    CSS = """
    #summary { padding: 0 1; background: $boost; height: auto; }
    #status { padding: 0 1; color: $text-muted; height: 1; }
    DataTable { height: 1fr; }
    ConfirmDelete, InfoScreen { align: center middle; }
    #dialog {
        width: 90; max-width: 95%; height: auto; max-height: 90%;
        padding: 1 2; border: thick $accent; background: $surface;
    }
    ConfirmDelete #dialog { border: thick $error; }
    #title { text-style: bold; margin-bottom: 1; }
    .section { text-style: bold; margin-top: 1; }
    .hint { color: $text-muted; margin-top: 1; width: 100%; }
    .delete-warning { color: $text-warning; margin-top: 1; width: 100%; }
    #buttons { height: auto; margin-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """
    BINDINGS = [
        Binding("d", "delete", "Delete"),
        Binding("D", "delete_now", "Delete (no confirm)"),
        Binding("i", "info", "Info"),
        Binding("s", "toggle_sort", "Sort"),
        Binding("r", "refresh", "Refresh"),
        Binding("R", "rescan", "Full rescan"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, client: dropbox.Dropbox, force_rescan: bool = False) -> None:
        super().__init__()
        self.client = client
        self.force_rescan = force_rescan
        self.account_id = ""
        self.snapshot: Snapshot | None = None
        self.root: Node | None = None
        self.current: Node | None = None
        self.sort_by_name = False
        self.quota_text = ""
        self.shared_folders: dict[str, SharedFolder] = {}
        self.busy = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="summary")
        yield ClickToOpenTable(cursor_type="row", zebra_stripes=True)
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("Name", key="name")
        table.add_column("Shared", key="shared")
        table.add_column("Size", key="size")
        table.add_column("%", key="percent")
        table.add_column("", key="bar")
        table.add_column("Files", key="files")
        self.load(rescan=self.force_rescan)

    def set_status(self, message: str) -> None:
        self.query_one("#status", Static).update(message)

    def _report_progress(self, entry_count: int) -> None:
        message = f"Scanning… {entry_count:,} items"
        self.call_from_thread(self.set_status, message)

    @work(thread=True, exclusive=True)
    def load(self, rescan: bool = False) -> None:
        self.busy = True
        try:
            self._load(rescan)
        except Exception as error:
            self.call_from_thread(self.set_status, f"Scan failed: {error}")
            self.call_from_thread(self.notify, str(error), severity="error")
        finally:
            self.busy = False

    def _load(self, rescan: bool) -> None:
        self.call_from_thread(self.set_status, "Connecting…")
        account = self.client.users_get_current_account()
        self.account_id = account.account_id
        self.quota_text = self._fetch_quota_text()

        cached = None if rescan else load_snapshot(self.account_id)
        if cached is not None:
            self.call_from_thread(self.set_status, "Checking for changes…")
            snapshot = refresh(self.client, cached, self._report_progress).snapshot
        else:
            snapshot = full_scan(self.client, self.account_id, self._report_progress)
        save_snapshot(snapshot)

        self.call_from_thread(self.set_status, "Loading sharing info…")
        self.shared_folders = self._fetch_shared_folders()

        self.call_from_thread(self.set_status, "Building tree…")
        root = build_tree(snapshot.entries)
        self.snapshot = snapshot
        self.call_from_thread(self._show_new_tree, root)

    def _fetch_shared_folders(self) -> dict[str, SharedFolder]:
        try:
            return sharing.list_shared_folders(self.client)
        except (ApiError, AuthError) as error:
            message = f"Couldn't load sharing info: {error}"
            self.call_from_thread(self.notify, message, severity="warning")
            return {}

    def _fetch_quota_text(self) -> str:
        usage = self.client.users_get_space_usage()
        allocation = usage.allocation
        if allocation.is_individual():
            allocated = allocation.get_individual().allocated
        elif allocation.is_team():
            allocated = allocation.get_team().allocated
        else:
            return f"Account usage: {human_size(usage.used)}"
        used_share = percent_of(usage.used, allocated)
        return (
            f"Account usage: {human_size(usage.used)} of {human_size(allocated)} "
            f"({used_share:.1f}%)"
        )

    def _show_new_tree(self, root: Node) -> None:
        previous_path = self.current.path_lower if self.current else ""
        highlighted = self._highlighted() if self.root else None
        highlight_key = highlighted.path_lower if highlighted else None
        self.root = root
        self.current = self._find(previous_path) or root
        self.render_folder(highlight_key=highlight_key)
        file_count = root.file_count
        self.set_status(f"Scanned {file_count:,} files, {root.folder_count:,} folders")

    def _find(self, path_lower: str) -> Node | None:
        node = self.root
        if path_lower == "":
            return node
        for part in path_lower.strip("/").split("/"):
            node = node.children.get(part)
            if node is None:
                return None
        return node

    def _sorted_children(self, folder: Node) -> list[Node]:
        children = list(folder.children.values())
        if self.sort_by_name:
            return sorted(children, key=lambda n: (not n.is_dir, n.name.lower()))
        return sorted(children, key=lambda n: n.size, reverse=True)

    def render_folder(self, highlight_key: str | None = None) -> None:
        folder = self.current
        table = self.query_one(DataTable)
        table.clear()

        if folder.parent is not None:
            table.add_row(Text("..", style="bold"), "", "", "", "", "", key=UP_KEY)

        largest = max((child.size for child in folder.children.values()), default=0)
        for child in self._sorted_children(folder):
            if child.is_dir:
                name = Text(child.name + "/", style="bold blue")
                files = f"{child.file_count:,}"
            else:
                name = Text(child.name)
                files = ""
            share = percent_of(child.size, folder.size)
            bar = Bar(
                size=largest,
                begin=0,
                end=child.size,
                width=BAR_WIDTH,
                color="cyan",
            )
            table.add_row(
                name,
                self._sharing_marker(child),
                Text(human_size(child.size), justify="right"),
                Text(f"{share:.1f}%", justify="right"),
                bar,
                Text(files, justify="right"),
                key=child.path_lower,
            )

        if highlight_key is not None:
            try:
                row_index = table.get_row_index(highlight_key)
                table.move_cursor(row=row_index)
            except Exception:
                pass

        self.sub_title = folder.path_display
        summary = Text()
        summary.append(folder.path_display, style="bold")
        summary.append(
            f"   {human_size(folder.size)} in {folder.file_count:,} files"
        )
        if folder.parent is not None:
            summary.append(
                f"  ({percent_of(folder.size, self.root.size):.1f}% of scanned)"
            )
        shared_root = folder.shared_root()
        if shared_root is not None:
            summary.append("\n")
            summary.append(self._shared_description(shared_root), style="yellow")
        summary.append(f"\nScanned total: {human_size(self.root.size)}   ")
        summary.append(self.quota_text, style="dim")
        self.query_one("#summary", Static).update(summary)
        table.focus()

    def _sharing_marker(self, node: Node) -> Text:
        if not node.shared_folder_id:
            return Text("")
        folder = self.shared_folders.get(node.shared_folder_id)
        if folder is None:
            return Text("shared", style="yellow")
        if folder.you_own_it:
            return Text("owner", style="bold green")
        return Text("member", style="yellow")

    def _shared_description(self, shared_root: Node) -> str:
        if shared_root is self.current:
            description = "Shared folder"
        else:
            description = f"Inside shared folder {shared_root.path_display}"
        folder = self.shared_folders.get(shared_root.shared_folder_id)
        if folder is None:
            return description
        owner = "you" if folder.you_own_it else folder.owner_text
        return f"{description}, owned by {owner}"

    def _highlighted(self) -> Node | None:
        table = self.query_one(DataTable)
        if self.current is None or table.row_count == 0:
            return None
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        if row_key.value == UP_KEY:
            return None
        child_key = row_key.value.rpartition("/")[2]
        return self.current.children.get(child_key)

    @on(DataTable.RowSelected)
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value == UP_KEY:
            self.action_up()
        else:
            self.action_open()

    def action_open(self) -> None:
        node = self._highlighted()
        if node is None or not node.is_dir:
            return
        self.current = node
        self.render_folder()

    def action_up(self) -> None:
        if self.current is None or self.current.parent is None:
            return
        came_from = self.current.path_lower
        self.current = self.current.parent
        self.render_folder(highlight_key=came_from)

    def action_toggle_sort(self) -> None:
        if self.current is None:
            return
        self.sort_by_name = not self.sort_by_name
        node = self._highlighted()
        self.render_folder(highlight_key=node.path_lower if node else None)
        order = "name" if self.sort_by_name else "size"
        self.notify(f"Sorted by {order}")

    def action_info(self) -> None:
        node = self._highlighted() or self.current
        if node is None:
            return
        self.push_screen(InfoScreen(node, self.root, self))

    def action_refresh(self) -> None:
        if not self.busy:
            self.load(rescan=False)

    def action_rescan(self) -> None:
        if not self.busy:
            self.load(rescan=True)

    def action_delete(self) -> None:
        node = self._highlighted()
        if node is None or self.busy:
            return

        def confirmed(should_delete: bool | None) -> None:
            if should_delete:
                self.delete_node(node.path_lower)

        warnings = self._delete_warnings(node)
        self.push_screen(ConfirmDelete(node, warnings), confirmed)

    def action_delete_now(self) -> None:
        node = self._highlighted()
        if node is None or self.busy:
            return
        self.delete_node(node.path_lower)

    def _delete_warnings(self, node: Node) -> list[Text]:
        warnings = []
        shared_root = node.shared_root()
        if shared_root is node:
            folder = self.shared_folders.get(node.shared_folder_id)
            you_own_it = folder is not None and folder.you_own_it
            message = (
                "This is a shared folder. Deleting it removes it from your "
                "Dropbox only; other members keep access, and you can add it "
                "back from dropbox.com."
            )
            if you_own_it:
                message += (
                    " You own it: to remove it for everyone, unshare it on "
                    "dropbox.com instead."
                )
            warnings.append(Text(message))
        elif shared_root is not None:
            folder = self.shared_folders.get(shared_root.shared_folder_id)
            owner = f" (owned by {folder.owner_text})" if folder else ""
            warnings.append(
                Text(
                    f"This is inside the shared folder {shared_root.path_display}"
                    f"{owner}. Deleting it deletes it for everyone that folder "
                    "is shared with. Are you sure?",
                    style="bold red",
                )
            )

        nested_shares = node.shared_folders_beneath() if node.is_dir else []
        if nested_shares:
            names = ", ".join(share.name for share in nested_shares[:3])
            if len(nested_shares) > 3:
                names += f", and {len(nested_shares) - 3} more"
            warnings.append(
                Text(
                    f"Contains {len(nested_shares)} shared folder(s): {names}. "
                    "They'll be removed from your Dropbox; their other members "
                    "keep access."
                )
            )
        return warnings

    # The view may be stale. Dropbox guards files via parent_rev, but folders
    # have no revision, so fetch changes before deleting one.

    @work(thread=True)
    def delete_node(self, path_lower: str, force: bool = False) -> None:
        self.busy = True
        try:
            self._delete(path_lower, force)
        except Exception as error:
            self._report_error(f"Couldn't delete {path_lower}: {error}")
        finally:
            self.busy = False

    def _delete(self, path_lower: str, force: bool) -> None:
        node = self._find(path_lower)
        if node.is_dir and not force:
            self.call_from_thread(self.set_status, "Checking for changes…")
            changes = refresh(self.client, self.snapshot, self._report_progress)
            if changes.anything_changed:
                save_snapshot(self.snapshot)
                root = build_tree(self.snapshot.entries)
                self.call_from_thread(self._show_new_tree, root)
            if changes.touches(path_lower):
                current_node = self._find(path_lower) or node
                self.call_from_thread(self._ask_delete_anyway, current_node)
                return

        check_rev = not node.is_dir and not force
        parent_rev = node.entry.rev if check_rev else None
        self.call_from_thread(self.set_status, f"Deleting {node.path_display}…")
        try:
            self.client.files_delete_v2(path_lower, parent_rev=parent_rev)
        except ApiError as error:
            if check_rev and self._file_changed(node):
                self.call_from_thread(self._ask_delete_anyway, node)
                return
            self._report_error(f"Couldn't delete {node.path_display}: {error.error}")
            return
        remove_subtree(self.snapshot.entries, path_lower)
        save_snapshot(self.snapshot)
        self.call_from_thread(self._after_delete, self._find(path_lower) or node)

    def _file_changed(self, node: Node) -> bool:
        try:
            current = self.client.files_get_metadata(node.path_lower)
        except ApiError:
            return False  # gone; the delete error already says so
        return getattr(current, "rev", None) != node.entry.rev

    def _ask_delete_anyway(self, node: Node) -> None:
        synced_at = self.snapshot.synced_at
        if synced_at is None:
            since = "your last scan"
        else:
            since = synced_at.astimezone().strftime("%b %-d, %H:%M")
        title = f"This changed since {since}. Delete anyway?"

        def answered(delete_anyway: bool | None) -> None:
            if delete_anyway:
                self.delete_node(node.path_lower, force=True)
            else:
                self.action_refresh()

        self.push_screen(ConfirmDelete(node, [], title=title), answered)

    def _report_error(self, message: str) -> None:
        self.call_from_thread(self.set_status, message)
        self.call_from_thread(self.notify, message, severity="error")

    def _after_delete(self, node: Node) -> None:
        table = self.query_one(DataTable)
        row_index = table.cursor_row
        freed = human_size(node.size)
        node.detach()
        self.render_folder()
        last_row = max(table.row_count - 1, 0)
        table.move_cursor(row=min(row_index, last_row))
        message = f"Deleted {node.path_display} ({freed})"
        self.set_status(message)
        self.notify(message)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="See what's using space in your Dropbox."
    )
    parser.add_argument(
        "--rescan", action="store_true", help="ignore the cache and rescan everything"
    )
    parser.add_argument(
        "--logout", action="store_true", help="forget the saved Dropbox login"
    )
    args = parser.parse_args()

    if args.logout:
        auth.logout()
        print("Logged out.")
        return

    client = auth.get_client()
    DropboxAnalyzer(client, force_rescan=args.rescan).run()


if __name__ == "__main__":
    main()
