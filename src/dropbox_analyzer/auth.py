import json
import os

import dropbox
from dropbox.exceptions import AuthError

from .paths import config_dir

SCOPES = [
    "account_info.read",
    "files.metadata.read",
    "files.content.write",
    "sharing.read",
]


def _token_file():
    return config_dir() / "token.json"


def _config_file():
    return config_dir() / "config.json"


def _write_private(path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2))
    path.chmod(0o600)


def get_app_key() -> str:
    env_key = os.environ.get("DROPBOX_APP_KEY")
    if env_key:
        return env_key

    config_path = _config_file()
    if config_path.exists():
        config = json.loads(config_path.read_text())
        if config.get("app_key"):
            return config["app_key"]

    print("No Dropbox app key configured.")
    print("Create an app at https://www.dropbox.com/developers/apps")
    print("  - Scoped access, Full Dropbox")
    print(f"  - Permissions tab: enable {', '.join(SCOPES)}")
    app_key = input("App key: ").strip()
    if not app_key:
        raise SystemExit("An app key is required.")
    _write_private(config_path, {"app_key": app_key})
    return app_key


def _run_oauth_flow(app_key: str) -> str:
    flow = dropbox.DropboxOAuth2FlowNoRedirect(
        app_key,
        use_pkce=True,  # so users don't need an app secret
        token_access_type="offline",
        scope=SCOPES,
    )
    authorize_url = flow.start()
    print("Open this link to authorize the app with Dropbox:")
    print(f"\n  {authorize_url}\n")
    auth_code = input("Paste the authorization code here: ").strip()
    result = flow.finish(auth_code)
    _write_private(
        _token_file(),
        {
            "app_key": app_key,
            "refresh_token": result.refresh_token,
            "scopes": SCOPES,
        },
    )
    return result.refresh_token


def _explain_new_scopes(missing_scopes: list[str]) -> None:
    print("This version needs more Dropbox permissions than your saved login has:")
    for scope in missing_scopes:
        print(f"  - {scope}")
    print("Enable them on your app's Permissions tab at")
    print("https://www.dropbox.com/developers/apps (click Submit), then log in again.")
    input("Press Enter once they're enabled... ")


def _client_from(app_key: str, refresh_token: str) -> dropbox.Dropbox:
    return dropbox.Dropbox(oauth2_refresh_token=refresh_token, app_key=app_key)


def get_client() -> dropbox.Dropbox:
    token_path = _token_file()
    if token_path.exists():
        saved = json.loads(token_path.read_text())
        granted_scopes = set(saved.get("scopes", []))
        missing_scopes = [scope for scope in SCOPES if scope not in granted_scopes]
        if missing_scopes:
            _explain_new_scopes(missing_scopes)
        else:
            client = _client_from(saved["app_key"], saved["refresh_token"])
            try:
                client.users_get_current_account()
                return client
            except AuthError:
                print("Saved Dropbox login is no longer valid; logging in again.")

    app_key = get_app_key()
    refresh_token = _run_oauth_flow(app_key)
    return _client_from(app_key, refresh_token)


def logout() -> None:
    _token_file().unlink(missing_ok=True)
