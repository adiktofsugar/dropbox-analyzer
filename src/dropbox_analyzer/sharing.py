"""Who owns each shared folder, and who it's shared with."""

from dataclasses import dataclass

import dropbox


@dataclass
class SharedFolder:
    shared_folder_id: str
    name: str
    access_type: str  # "owner", "editor", "viewer", ...
    owner_names: list[str]
    team_folder: bool

    @property
    def you_own_it(self) -> bool:
        return self.access_type == "owner"

    @property
    def owner_text(self) -> str:
        if self.you_own_it:
            return "You"
        if self.owner_names:
            return ", ".join(self.owner_names)
        if self.team_folder:
            return "Your team"
        return "Someone else"


@dataclass
class Member:
    name: str
    email: str | None
    access_type: str
    is_you: bool = False
    kind: str = "user"  # "user", "group", or "invitee"


def access_name(access_level) -> str:
    """'owner', 'editor', etc. from a sharing.AccessLevel union."""
    return access_level._tag


def _from_metadata(metadata) -> SharedFolder:
    return SharedFolder(
        shared_folder_id=metadata.shared_folder_id,
        name=metadata.name,
        access_type=access_name(metadata.access_type),
        owner_names=list(metadata.owner_display_names or []),
        team_folder=metadata.is_team_folder,
    )


def list_shared_folders(client: dropbox.Dropbox) -> dict[str, SharedFolder]:
    """Every shared folder the account is a member of, by shared_folder_id."""
    folders = {}
    result = client.sharing_list_folders()
    while True:
        for metadata in result.entries:
            folders[metadata.shared_folder_id] = _from_metadata(metadata)
        if not result.cursor:
            break
        result = client.sharing_list_folders_continue(result.cursor)
    return folders


def get_shared_folder(client: dropbox.Dropbox, shared_folder_id: str) -> SharedFolder:
    metadata = client.sharing_get_folder_metadata(shared_folder_id)
    return _from_metadata(metadata)


def list_members(
    client: dropbox.Dropbox, shared_folder_id: str, my_account_id: str
) -> list[Member]:
    members = []
    result = client.sharing_list_folder_members(shared_folder_id)
    while True:
        for user_membership in result.users:
            user = user_membership.user
            members.append(
                Member(
                    name=user.display_name,
                    email=user.email,
                    access_type=access_name(user_membership.access_type),
                    is_you=user.account_id == my_account_id,
                )
            )
        for group_membership in result.groups:
            group = group_membership.group
            members.append(
                Member(
                    name=f"{group.group_name} ({group.member_count or '?'} members)",
                    email=None,
                    access_type=access_name(group_membership.access_type),
                    kind="group",
                )
            )
        for invitee_membership in result.invitees:
            invitee = invitee_membership.invitee
            email = invitee.get_email() if invitee.is_email() else None
            members.append(
                Member(
                    name=email or "Pending invite",
                    email=None,
                    access_type=access_name(invitee_membership.access_type),
                    kind="invitee",
                )
            )
        if not result.cursor:
            break
        result = client.sharing_list_folder_members_continue(result.cursor)

    # Owner first, then you, then everyone else by name.
    members.sort(key=lambda m: (m.access_type != "owner", not m.is_you, m.name.lower()))
    return members
