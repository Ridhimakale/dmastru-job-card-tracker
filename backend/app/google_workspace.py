"""Small Google OAuth and Drive API client for Docs/Sheets work products."""
import json
import os
import secrets
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import HTTPException

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_API = "https://www.googleapis.com/drive/v3"
SCOPE = "https://www.googleapis.com/auth/drive.file https://www.googleapis.com/auth/userinfo.email"

# Accept both the documented OAuth-prefixed names and the shorter names used
# by earlier local setup instructions. Environment variables are read at call
# time so a restarted backend picks up the current PowerShell configuration.
ENV_ALIASES = {
    "GOOGLE_OAUTH_CLIENT_ID": ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_CLIENT_ID"),
    "GOOGLE_OAUTH_CLIENT_SECRET": ("GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_CLIENT_SECRET"),
    "GOOGLE_OAUTH_REDIRECT_URI": ("GOOGLE_OAUTH_REDIRECT_URI", "GOOGLE_REDIRECT_URI", "GOOGLE_CALLBACK_URL"),
}


def _setting(name: str) -> str | None:
    return next((os.getenv(alias) for alias in ENV_ALIASES.get(name, (name,)) if os.getenv(alias)), None)


def configured() -> bool:
    return all(_setting(name) for name in ENV_ALIASES)


def configuration_status() -> dict[str, bool]:
    """Expose only whether each required setting exists, never its value."""
    return {
        "client_id": bool(_setting("GOOGLE_OAUTH_CLIENT_ID")),
        "client_secret": bool(_setting("GOOGLE_OAUTH_CLIENT_SECRET")),
        "redirect_uri": bool(_setting("GOOGLE_OAUTH_REDIRECT_URI")),
    }


def config_value(name: str) -> str:
    value = _setting(name)
    if not value:
        raise HTTPException(503, "Google Docs/Sheets is not configured yet. Set the Google OAuth client settings for this deployment.")
    return value


def authorization_url(state: str) -> str:
    return GOOGLE_AUTH_URL + "?" + urlencode({
        "client_id": config_value("GOOGLE_OAUTH_CLIENT_ID"),
        "redirect_uri": config_value("GOOGLE_OAUTH_REDIRECT_URI"),
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    })


def _request(url: str, *, method: str = "GET", data: bytes | None = None, headers: dict[str, str] | None = None):
    request = Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urlopen(request, timeout=25) as response:
            return response.read(), response.headers.get("Content-Type", "application/octet-stream")
    except HTTPError as exc:
        # Keep Google's safe diagnostic text so the portal can explain common
        # setup issues (for example Drive API disabled or missing permission).
        # Never include request headers, which contain OAuth credentials.
        try:
            payload = json.loads(exc.read(4096))
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            message = error.get("message", "") if isinstance(error, dict) else ""
            details = error.get("errors", []) if isinstance(error, dict) else []
            reason = details[0].get("reason", "") if details and isinstance(details[0], dict) else ""
        except (ValueError, TypeError):
            message, reason = "", ""
        diagnostic = ": ".join(part for part in (str(reason).strip(), str(message).strip()) if part)
        if diagnostic:
            diagnostic = diagnostic[:400]
            raise HTTPException(502, f"Google Workspace request failed ({exc.code}): {diagnostic}") from None
        raise HTTPException(502, f"Google Workspace request failed ({exc.code}).") from None
    except (URLError, TimeoutError):
        raise HTTPException(502, "Could not reach Google Workspace. Please try again.") from None


def exchange_code(code: str) -> dict:
    payload = urlencode({
        "code": code,
        "client_id": config_value("GOOGLE_OAUTH_CLIENT_ID"),
        "client_secret": config_value("GOOGLE_OAUTH_CLIENT_SECRET"),
        "redirect_uri": config_value("GOOGLE_OAUTH_REDIRECT_URI"),
        "grant_type": "authorization_code",
    }).encode()
    raw, _ = _request(GOOGLE_TOKEN_URL, method="POST", data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        result = json.loads(raw)
    except ValueError:
        raise HTTPException(502, "Google returned an invalid authorization response.") from None
    if not result.get("access_token"):
        raise HTTPException(502, "Google did not authorize the Docs/Sheets connection.")
    return result


def user_email(access_token: str) -> str:
    raw, _ = _request("https://www.googleapis.com/oauth2/v2/userinfo", headers={"Authorization": f"Bearer {access_token}"})
    try:
        return str(json.loads(raw).get("email", ""))
    except ValueError:
        return ""


def access_token(refresh_token: str) -> str:
    payload = urlencode({
        "client_id": config_value("GOOGLE_OAUTH_CLIENT_ID"),
        "client_secret": config_value("GOOGLE_OAUTH_CLIENT_SECRET"),
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    raw, _ = _request(GOOGLE_TOKEN_URL, method="POST", data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        token = json.loads(raw).get("access_token")
    except ValueError:
        token = None
    if not token:
        raise HTTPException(401, "Google authorization expired. Reconnect your Google account.")
    return token


def create_file(access: str, name: str, kind: str) -> dict:
    mime = "application/vnd.google-apps.document" if kind == "DOCUMENT" else "application/vnd.google-apps.spreadsheet"
    body = json.dumps({"name": name, "mimeType": mime}).encode()
    raw, _ = _request(f"{DRIVE_API}/files?fields=id,name,mimeType,webViewLink", method="POST", data=body, headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json"})
    try:
        return json.loads(raw)
    except ValueError:
        raise HTTPException(502, "Google created a file but returned an invalid response.") from None


def create_doc_from_upload(access: str, name: str, content: bytes, mime_type: str) -> dict:
    """Import an uploaded Office document as an editable Google Doc."""
    boundary = "codex_" + secrets.token_hex(16)
    metadata = json.dumps({"name": name, "mimeType": "application/vnd.google-apps.document"}).encode()
    body = (
        b"--" + boundary.encode() + b"\r\n"
        + b"Content-Type: application/json; charset=UTF-8\r\n\r\n" + metadata + b"\r\n"
        + b"--" + boundary.encode() + b"\r\n"
        + f"Content-Type: {mime_type}\r\n\r\n".encode() + content + b"\r\n"
        + b"--" + boundary.encode() + b"--"
    )
    raw, _ = _request(
        "https://www.googleapis.com/upload/drive/v3/files?" + urlencode({"uploadType": "multipart", "fields": "id,name,mimeType,webViewLink"}),
        method="POST",
        data=body,
        headers={"Authorization": f"Bearer {access}", "Content-Type": f"multipart/related; boundary={boundary}"},
    )
    try:
        return json.loads(raw)
    except ValueError:
        raise HTTPException(502, "Google imported the reference but returned an invalid response.") from None


def copy_file(access: str, file_id: str, name: str) -> dict:
    body = json.dumps({"name": name}).encode()
    raw, _ = _request(
        f"{DRIVE_API}/files/{file_id}/copy?" + urlencode({"fields": "id,name,mimeType,webViewLink"}),
        method="POST",
        data=body,
        headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json"},
    )
    try:
        return json.loads(raw)
    except ValueError:
        raise HTTPException(502, "Google copied the draft but returned an invalid response.") from None


def export_file(access: str, file_id: str, kind: str) -> tuple[bytes, str]:
    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if kind == "DOCUMENT" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    content, _ = _request(f"{DRIVE_API}/files/{file_id}/export?" + urlencode({"mimeType": mime}), headers={"Authorization": f"Bearer {access}"})
    return content, mime


def delete_file(access: str, file_id: str) -> None:
    """Delete a Drive file created through this application's drive.file scope."""
    _request(f"{DRIVE_API}/files/{file_id}", method="DELETE", headers={"Authorization": f"Bearer {access}"})
