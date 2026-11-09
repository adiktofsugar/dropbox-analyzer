"""Listing the whole Dropbox, caching it, and building a size tree.

The full listing is cached along with Dropbox's list_folder cursor, so later
runs only fetch what changed since the last scan.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import dropbox
from dropbox.exceptions import ApiError
from dropbox.files import DeletedMetadata, FileMetadata, FolderMetadata

from .paths import cache_dir

CACHE_VERSION = 2
PAGE_LIMIT = 2000

ProgressCallback = Callable[[int], None]


@dataclass
class Entry:
    name: str
    path_display: str
    is_dir: bool
    size: int = 0
    modified: str | None = None
    id: str | None = None
    content_hash: str | None = None
    shared_folder_id: str | None = None  # set only on a shared folder's root

    def to_list(self) -> list:
        return [
            self.name,
            self.path_display,
            self.is_dir,
            self.size,
            self.modified,
            self.id,
            self.content_hash,
            self.shared_folder_id,
        ]

    @classmethod
    def from_list(cls, values: list) -> "Entry":
        return cls(*values)


@dataclass
class Snapshot:
    account_id: str
    cursor: str | None = None
    entries: dict[str, Entry] = field(default_factory=dict)  # keyed by path_lower


def _cache_file(account_id: str):
    safe_id = account_id.replace(":", "_")
    return cache_dir() / f"{safe_id}.json"


def load_snapshot(account_id: str) -> Snapshot | None:
    path = _cache_file(account_id)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    if raw.get("version") != CACHE_VERSION:
        return None
    entries = {key: Entry.from_list(value) for key, value in raw["entries"].items()}
    return Snapshot(account_id=account_id, cursor=raw["cursor"], entries=entries)


def save_snapshot(snapshot: Snapshot) -> None:
    raw = {
        "version": CACHE_VERSION,
        "cursor": snapshot.cursor,
        "entries": {key: entry.to_list() for key, entry in snapshot.entries.items()},
    }
    path = _cache_file(snapshot.account_id)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(raw))
    temp_path.replace(path)


def remove_subtree(entries: dict[str, Entry], path_lower: str) -> None:
    removed = entries.pop(path_lower, None)
    was_file = removed is not None and not removed.is_dir
    if was_file:
        return
    prefix = path_lower + "/"
    descendants = [key for key in entries if key.startswith(prefix)]
    for key in descendants:
        del entries[key]


def _apply(entries: dict[str, Entry], metadata) -> None:
    path_lower = metadata.path_lower
    if isinstance(metadata, DeletedMetadata):
        remove_subtree(entries, path_lower)
    elif isinstance(metadata, FolderMetadata):
        sharing_info = metadata.sharing_info
        shared_folder_id = sharing_info.shared_folder_id if sharing_info else None
        entries[path_lower] = Entry(
            name=metadata.name,
            path_display=metadata.path_display,
            is_dir=True,
            id=metadata.id,
            shared_folder_id=shared_folder_id,
        )
    elif isinstance(metadata, FileMetadata):
        entries[path_lower] = Entry(
            name=metadata.name,
            path_display=metadata.path_display,
            is_dir=False,
            size=metadata.size,
            modified=metadata.server_modified.isoformat(),
            id=metadata.id,
            content_hash=metadata.content_hash,
        )


def _drain(client: dropbox.Dropbox, result, snapshot: Snapshot, on_progress) -> None:
    while True:
        for metadata in result.entries:
            _apply(snapshot.entries, metadata)
        on_progress(len(snapshot.entries))
        if not result.has_more:
            break
        result = client.files_list_folder_continue(result.cursor)
    snapshot.cursor = result.cursor


def full_scan(
    client: dropbox.Dropbox, account_id: str, on_progress: ProgressCallback
) -> Snapshot:
    snapshot = Snapshot(account_id=account_id)
    first_page = client.files_list_folder(
        "", recursive=True, include_deleted=False, limit=PAGE_LIMIT
    )
    _drain(client, first_page, snapshot, on_progress)
    return snapshot


def refresh(
    client: dropbox.Dropbox, snapshot: Snapshot, on_progress: ProgressCallback
) -> Snapshot:
    """Bring a cached snapshot up to date, falling back to a full scan."""
    if snapshot.cursor is None:
        return full_scan(client, snapshot.account_id, on_progress)
    try:
        first_page = client.files_list_folder_continue(snapshot.cursor)
    except ApiError as error:
        cursor_expired = error.error.is_reset()
        if not cursor_expired:
            raise
        return full_scan(client, snapshot.account_id, on_progress)
    _drain(client, first_page, snapshot, on_progress)
    return snapshot


# ---------------------------------------------------------------------------
# Tree


@dataclass(eq=False)
class Node:
    name: str
    path_lower: str
    is_dir: bool
    entry: Entry | None = None
    parent: "Node | None" = None
    children: dict[str, "Node"] = field(default_factory=dict)
    size: int = 0  # file size, or total of everything beneath a folder
    file_count: int = 0
    folder_count: int = 0

    @property
    def key(self) -> str:
        """This node's key in its parent's children (last path_lower part)."""
        return self.path_lower.rpartition("/")[2]

    @property
    def path_display(self) -> str:
        if self.entry is not None:
            return self.entry.path_display
        if self.parent is None:
            return "/"
        return f"{self.parent.path_display.rstrip('/')}/{self.name}"

    @property
    def modified(self) -> datetime | None:
        if self.entry is None or self.entry.modified is None:
            return None
        return datetime.fromisoformat(self.entry.modified)

    @property
    def shared_folder_id(self) -> str | None:
        if self.entry is None:
            return None
        return self.entry.shared_folder_id

    def shared_root(self) -> "Node | None":
        """The shared folder this node is, or is inside, if any."""
        node = self
        while node is not None:
            if node.shared_folder_id:
                return node
            node = node.parent
        return None

    def shared_folders_beneath(self) -> list["Node"]:
        found = []
        stack = list(self.children.values())
        while stack:
            node = stack.pop()
            if not node.is_dir:
                continue
            if node.shared_folder_id:
                found.append(node)
            stack.extend(node.children.values())
        return found

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def iter_files(self):
        stack = [self]
        while stack:
            node = stack.pop()
            if not node.is_dir:
                yield node
            stack.extend(node.children.values())

    def detach(self) -> None:
        """Remove this node from the tree, updating ancestor totals."""
        if self.parent is None:
            return
        removed_files = self.file_count if self.is_dir else 1
        removed_folders = self.folder_count + (1 if self.is_dir else 0)
        for ancestor in self.ancestors():
            ancestor.size -= self.size
            ancestor.file_count -= removed_files
            ancestor.folder_count -= removed_folders
        del self.parent.children[self.key]
        self.parent = None


def build_tree(entries: dict[str, Entry]) -> Node:
    root = Node(name="Dropbox", path_lower="", is_dir=True)
    nodes: dict[str, Node] = {"": root}

    def get_folder(path_lower: str) -> Node:
        existing = nodes.get(path_lower)
        if existing is not None:
            return existing
        # parent_path, _, name = path_lower.rpartition("/")
        parent_path, _, name = path_lower.rpartition("/")
        parent = get_folder(parent_path)
        folder = Node(name=name, path_lower=path_lower, is_dir=True, parent=parent)
        parent.children[name] = folder
        nodes[path_lower] = folder
        return folder

    # Shortest paths first so folders exist (with real names) before children.
    for path_lower in sorted(entries, key=len):
        entry = entries[path_lower]
        if entry.is_dir:
            folder = get_folder(path_lower)
            folder.entry = entry
            folder.name = entry.name
            continue
        parent_path = path_lower.rpartition("/")[0]
        parent = get_folder(parent_path)
        file_node = Node(
            name=entry.name,
            path_lower=path_lower,
            is_dir=False,
            entry=entry,
            parent=parent,
            size=entry.size,
        )
        parent.children[file_node.key] = file_node

    _total(root)
    return root


def _total(root: Node) -> None:
    # Post-order without recursion; Dropbox trees can be very deep.
    order = []
    stack = [root]
    while stack:
        node = stack.pop()
        order.append(node)
        stack.extend(child for child in node.children.values() if child.is_dir)
    for folder in reversed(order):
        size = 0
        file_count = 0
        folder_count = 0
        for child in folder.children.values():
            size += child.size
            if child.is_dir:
                file_count += child.file_count
                folder_count += child.folder_count + 1
            else:
                file_count += 1
        folder.size = size
        folder.file_count = file_count
        folder.folder_count = folder_count
