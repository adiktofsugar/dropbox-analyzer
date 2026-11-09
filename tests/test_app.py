"""Drive the TUI against a fake Dropbox client."""

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from dropbox import sharing
from dropbox.files import (
    DeletedMetadata,
    FileMetadata,
    FolderMetadata,
    FolderSharingInfo,
    ListFolderResult,
)
from dropbox.users import IndividualSpaceAllocation, SpaceAllocation, SpaceUsage

from dropbox_analyzer.app import ConfirmDelete, DropboxAnalyzer, InfoScreen
from dropbox_analyzer.scan import load_snapshot

MY_ACCOUNT = "dbid:" + "M" * 35


def folder(path, shared_folder_id=None):
    sharing_info = None
    if shared_folder_id:
        sharing_info = FolderSharingInfo(
            read_only=False, shared_folder_id=shared_folder_id
        )
    return FolderMetadata(
        name=path.rsplit("/", 1)[1],
        path_lower=path.lower(),
        path_display=path,
        id="id:" + path,
        sharing_info=sharing_info,
    )


def file(path, size):
    return FileMetadata(
        name=path.rsplit("/", 1)[1],
        path_lower=path.lower(),
        path_display=path,
        id="id:" + path,
        client_modified=datetime(2024, 1, 1),
        server_modified=datetime(2024, 1, 2),
        rev="0123456789abc",
        size=size,
        content_hash="h" * 64,
    )


ENTRIES = [
    folder("/Photos"),
    folder("/Photos/2019"),
    file("/Photos/2019/a.jpg", 5_000_000),
    file("/Photos/2019/b.jpg", 7_000_000),
    file("/Photos/c.png", 1_000),
    folder("/Work"),
    file("/Work/big.zip", 3_000_000_000),
    folder("/Family", shared_folder_id="sf-family"),
    file("/Family/video.mp4", 900_000_000),
    folder("/Theirs", shared_folder_id="sf-theirs"),
    file("/Theirs/deck.pdf", 50_000_000),
    file("/notes.txt", 2048),
]


def shared_folder_metadata(shared_folder_id, name, access, owners):
    return sharing.SharedFolderMetadata(
        access_type=access,
        is_inside_team_folder=False,
        is_team_folder=False,
        name=name,
        policy=sharing.FolderPolicy(
            acl_update_policy=sharing.AclUpdatePolicy.owner,
            shared_link_policy=sharing.SharedLinkPolicy.anyone,
        ),
        preview_url="",
        shared_folder_id=shared_folder_id,
        time_invited=datetime(2024, 1, 1),
        owner_display_names=owners,
        path_lower="/" + name.lower(),
        path_display="/" + name,
    )


def user_member(account_id, name, email, access):
    return sharing.UserMembershipInfo(
        access_type=access,
        user=sharing.UserInfo(
            account_id=account_id,
            email=email,
            display_name=name,
            same_team=False,
        ),
    )


class FakeDropbox:
    def __init__(self):
        self.deleted = []
        self.delta = []

    def users_get_current_account(self):
        return SimpleNamespace(account_id=MY_ACCOUNT)

    def users_get_space_usage(self):
        allocation = SpaceAllocation.individual(
            IndividualSpaceAllocation(allocated=2 * 1024**4)
        )
        return SpaceUsage(used=4_000_000_000, allocation=allocation)

    def files_list_folder(self, path, **kwargs):
        return ListFolderResult(entries=ENTRIES[:4], cursor="c1", has_more=True)

    def files_list_folder_continue(self, cursor):
        if cursor == "c1":
            return ListFolderResult(entries=ENTRIES[4:], cursor="c2", has_more=False)
        return ListFolderResult(entries=self.delta, cursor="c3", has_more=False)

    def files_delete_v2(self, path):
        self.deleted.append(path)

    def sharing_list_folders(self):
        entries = [
            shared_folder_metadata(
                "sf-family", "Family", sharing.AccessLevel.owner, ["Me Myself"]
            ),
            shared_folder_metadata(
                "sf-theirs", "Theirs", sharing.AccessLevel.editor, ["Alex Other"]
            ),
        ]
        return sharing.ListFoldersResult(entries=entries, cursor=None)

    def sharing_list_folder_members(self, shared_folder_id):
        users = [
            user_member(MY_ACCOUNT, "Me Myself", "me@x.test", sharing.AccessLevel.owner),
            user_member("dbid:" + "S" * 35, "Sam", "sam@x.test", sharing.AccessLevel.editor),
        ]
        return sharing.SharedFolderMembers(
            users=users, groups=[], invitees=[], cursor=None
        )


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


def row_names(app):
    table = app.query_one("DataTable")
    return [str(table.get_row_at(i)[0]) for i in range(table.row_count)]


def row_markers(app):
    table = app.query_one("DataTable")
    return {
        str(table.get_row_at(i)[0]): str(table.get_row_at(i)[1])
        for i in range(table.row_count)
    }


def move_to(app, name):
    table = app.query_one("DataTable")
    table.move_cursor(row=row_names(app).index(name))


def warning_text(app):
    labels = app.screen.query(".delete-warning")
    return " ".join(str(label.render()) for label in labels)


def run(scenario):
    async def go():
        fake = FakeDropbox()
        app = DropboxAnalyzer(fake)
        async with app.run_test(size=(110, 36)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            await scenario(app, pilot, fake)

    asyncio.run(go())


def test_browse_click_and_up():
    async def scenario(app, pilot, fake):
        assert row_names(app) == [
            "Work/", "Family/", "Theirs/", "Photos/", "notes.txt",
        ]
        move_to(app, "Photos/")
        await pilot.press("enter")
        assert app.current.path_display == "/Photos"
        await pilot.press("h")
        assert app.current.path_display == "/"
        # A single click opens a folder (row 0 is the header).
        await pilot.click("DataTable", offset=(3, 1))
        await pilot.pause()
        assert app.current.path_display == "/Work"
        await pilot.press("left")
        assert app.current.path_display == "/"

    run(scenario)


def test_shared_markers():
    async def scenario(app, pilot, fake):
        markers = row_markers(app)
        assert markers["Family/"] == "owner"
        assert markers["Theirs/"] == "member"
        assert markers["Photos/"] == ""

    run(scenario)


def test_info_shows_owner_and_members():
    async def scenario(app, pilot, fake):
        move_to(app, "Theirs/")
        await pilot.press("i")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, InfoScreen)
        details = str(app.screen.query_one("#details").render())
        assert "Owner" in details and "Alex Other" in details
        assert "editor" in details
        members = str(app.screen.query_one("#members").render())
        assert "Me Myself (you)" in members
        assert "sam@x.test" in members

    run(scenario)


def test_delete_plain_folder_confirms():
    async def scenario(app, pilot, fake):
        move_to(app, "Work/")
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDelete)
        assert warning_text(app) == ""
        await pilot.press("y")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert fake.deleted == ["/work"]
        assert "Work/" not in row_names(app)
        assert "/work/big.zip" not in load_snapshot(MY_ACCOUNT).entries

    run(scenario)


def test_delete_owned_shared_folder_warns():
    async def scenario(app, pilot, fake):
        move_to(app, "Family/")
        await pilot.press("d")
        await pilot.pause()
        warning = warning_text(app)
        assert "removes it from your Dropbox only" in warning
        assert "You own it" in warning
        await pilot.press("n")
        await pilot.pause()
        assert fake.deleted == []

    run(scenario)


def test_delete_inside_shared_folder_warns():
    async def scenario(app, pilot, fake):
        move_to(app, "Theirs/")
        await pilot.press("enter")
        move_to(app, "deck.pdf")
        await pilot.press("d")
        await pilot.pause()
        warning = warning_text(app)
        assert "deletes it for everyone" in warning
        assert "Alex Other" in warning

    run(scenario)


def test_capital_d_skips_confirmation():
    async def scenario(app, pilot, fake):
        move_to(app, "Family/")
        await pilot.press("D")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmDelete)
        assert fake.deleted == ["/family"]

    run(scenario)


def test_incremental_refresh():
    async def scenario(app, pilot, fake):
        fake.delta = [
            DeletedMetadata(
                name="a.jpg",
                path_lower="/photos/2019/a.jpg",
                path_display="/Photos/2019/a.jpg",
            ),
            file("/new.bin", 10),
        ]
        await pilot.press("r")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "new.bin" in row_names(app)
        assert app._find("/photos").size == 7_001_000

    run(scenario)
