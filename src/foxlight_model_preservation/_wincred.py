"""Generic secrets in Windows Credential Manager (``CredWriteW`` and friends).

Credential Manager keeps each secret encrypted for this user on this
computer (``CRED_PERSIST_LOCAL_MACHINE``: it does not roam to other
computers with a domain profile). Secrets cross only process memory: no
command line, file or helper process sees them (design: foxlight-docs
windows-storage-2026-11-17, "Keychain").
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

if sys.platform != "win32":  # pragma: no cover - imported only on Windows
    raise ImportError("Windows Credential Manager only")

_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
# CRED_MAX_CREDENTIAL_BLOB_SIZE: 5 * 512 bytes.
MAX_SECRET_BYTES = 2560
ERROR_NOT_FOUND = 1168


class _Credential(ctypes.Structure):
    _fields_ = (
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    )


_advapi = ctypes.WinDLL("advapi32", use_last_error=True)
_CredWriteW = _advapi.CredWriteW
_CredWriteW.argtypes = (ctypes.POINTER(_Credential), wintypes.DWORD)
_CredWriteW.restype = wintypes.BOOL
_CredReadW = _advapi.CredReadW
_CredReadW.argtypes = (
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(ctypes.POINTER(_Credential)),
)
_CredReadW.restype = wintypes.BOOL
_CredDeleteW = _advapi.CredDeleteW
_CredDeleteW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD)
_CredDeleteW.restype = wintypes.BOOL
_CredFree = _advapi.CredFree
_CredFree.argtypes = (ctypes.c_void_p,)
_CredFree.restype = None


def _failed() -> OSError:
    # Only the Windows error code: never the target or the secret.
    code = ctypes.get_last_error()
    return OSError(code, "Credential Manager", None, code)


def write(target: str, user: str, secret: str, comment: str) -> None:
    """Create or replace the generic credential ``target``."""
    data = secret.encode()
    if len(data) > MAX_SECRET_BYTES:
        raise ValueError("secret too large for Credential Manager")
    blob = (ctypes.c_ubyte * max(len(data), 1)).from_buffer_copy(data or b"\0")
    credential = _Credential(
        Type=_CRED_TYPE_GENERIC,
        TargetName=target,
        Comment=comment,
        CredentialBlobSize=len(data),
        CredentialBlob=ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte)),
        Persist=_CRED_PERSIST_LOCAL_MACHINE,
        UserName=user,
    )
    try:
        if not _CredWriteW(ctypes.byref(credential), 0):
            raise _failed()
    finally:
        ctypes.memset(blob, 0, ctypes.sizeof(blob))


def read(target: str) -> str | None:
    """The secret stored as ``target``, or ``None`` when there is none."""
    found = ctypes.POINTER(_Credential)()
    if not _CredReadW(target, _CRED_TYPE_GENERIC, 0, ctypes.byref(found)):
        if ctypes.get_last_error() == ERROR_NOT_FOUND:
            return None
        raise _failed()
    try:
        credential = found.contents
        data = ctypes.string_at(
            credential.CredentialBlob, credential.CredentialBlobSize
        )
    finally:
        _CredFree(found)
    return data.decode()


def delete(target: str) -> bool:
    """Remove ``target``; ``False`` when nothing was stored."""
    if _CredDeleteW(target, _CRED_TYPE_GENERIC, 0):
        return True
    if ctypes.get_last_error() == ERROR_NOT_FOUND:
        return False
    raise _failed()
