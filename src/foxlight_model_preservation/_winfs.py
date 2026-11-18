# ruff: noqa: E402 - the platform assert comes before the Windows-only imports
"""Windows side of :mod:`safefs`: the NT handle-relative calls (``ctypes``).

Design: foxlight-docs windows-storage-2026-11-17. Every path inside a store is
opened one component at a time with ``NtCreateFile`` relative to a pinned
directory handle and ``FILE_OPEN_REPARSE_POINT``, so a link (symbolic link,
junction, cloud placeholder or any other reparse point) is opened as itself
and refused, never followed. Renames, links and deletes are
``NtSetInformationFile`` calls on the opened handle with POSIX semantics, so a
file another handle holds open can still be replaced or removed.

Handles are wrapped as C runtime descriptors (``msvcrt.open_osfhandle``), so
``os.close``, ``os.dup``, ``os.fstat``, ``os.fsync`` and ``os.fdopen`` work
on them unchanged; directories too. Every handle shares read, write and
delete, as POSIX descriptors effectively do. After opening an existing entry
its real name is compared with the requested one, closing case and 8.3 short
name aliases. Private folders carry a protected ACL that grants only this
user and SYSTEM, and everything created names this user as its owner, even
when the process runs elevated.
"""

# basedpyright analyzes this module only for Windows.
import sys

assert sys.platform == "win32"  # noqa: S101 - imported only on Windows

import ctypes
import errno
import hashlib
import msvcrt
import os
import shutil
import stat
import time
from collections.abc import Callable
from ctypes import wintypes
from functools import cache
from pathlib import Path

__all__ = [
    "identity",
    "link_at",
    "link_path",
    "list_dir",
    "lock",
    "make_dir",
    "mkdir_at",
    "open_at",
    "open_dir",
    "open_dir_at",
    "open_path",
    "owned",
    "path_owned",
    "path_private",
    "private",
    "readlink_at",
    "TREE_REMOVAL_SAFE",
    "remove_tree",
    "remove_tree_at",
    "rename_at",
    "replace_at",
    "rmdir_at",
    "free_bytes",
    "stat_at",
    "sync_at",
    "unlink_at",
    "unlock",
]

ntdll = ctypes.WinDLL("ntdll")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

# Access rights
DELETE = 0x00010000
READ_CONTROL = 0x00020000
SYNCHRONIZE = 0x00100000
FILE_READ_DATA = FILE_LIST_DIRECTORY = 0x0001
FILE_WRITE_DATA = FILE_ADD_FILE = 0x0002
FILE_APPEND_DATA = FILE_ADD_SUBDIRECTORY = 0x0004
FILE_TRAVERSE = 0x0020
FILE_READ_ATTRIBUTES = 0x0080
FILE_GENERIC_READ = 0x00120089
FILE_GENERIC_WRITE = 0x00120116
SHARE_ALL = 0x7  # read | write | delete

# NtCreateFile dispositions and options
FILE_OPEN, FILE_CREATE, FILE_OPEN_IF = 1, 2, 3
FILE_DIRECTORY_FILE = 0x00000001
FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
FILE_NON_DIRECTORY_FILE = 0x00000040
FILE_OPEN_REPARSE_POINT = 0x00200000
FILE_CREATED = 2

# CreateFileW
OPEN_EXISTING = 3
CREATE_NEW, OPEN_ALWAYS = 1, 4
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

# Information classes
FILE_RENAME_INFORMATION = 10
FILE_LINK_INFORMATION = 11
FILE_DISPOSITION_INFORMATION = 13
FILE_DISPOSITION_INFORMATION_EX = 64
FILE_RENAME_INFORMATION_EX = 65
FILE_LINK_INFORMATION_EX = 72
FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
FILE_FULL_DIRECTORY_INFO = 14
FILE_FULL_DIRECTORY_RESTART_INFO = 15
RENAME_REPLACE_IF_EXISTS = 0x1
RENAME_POSIX_SEMANTICS = 0x2
RENAME_IGNORE_READONLY_ATTRIBUTE = 0x40
LINK_POSIX_SEMANTICS = 0x2
DISPOSITION_DELETE = 0x1
DISPOSITION_POSIX_SEMANTICS = 0x2
DISPOSITION_IGNORE_READONLY_ATTRIBUTE = 0x10

# NTSTATUS values (as unsigned)
STATUS_INVALID_INFO_CLASS = 0xC0000003
STATUS_INVALID_PARAMETER = 0xC000000D
STATUS_ACCESS_DENIED = 0xC0000022
STATUS_OBJECT_NAME_INVALID = 0xC0000033
STATUS_OBJECT_NAME_NOT_FOUND = 0xC0000034
STATUS_OBJECT_NAME_COLLISION = 0xC0000035
STATUS_OBJECT_PATH_NOT_FOUND = 0xC000003A
STATUS_SHARING_VIOLATION = 0xC0000043
STATUS_DELETE_PENDING = 0xC0000056
STATUS_FILE_IS_A_DIRECTORY = 0xC00000BA
STATUS_NOT_SUPPORTED = 0xC00000BB
STATUS_DIRECTORY_NOT_EMPTY = 0xC0000101
STATUS_NOT_A_DIRECTORY = 0xC0000103
STATUS_NOT_SAME_DEVICE = 0xC00000D4
STATUS_CANNOT_DELETE = 0xC0000121
STATUS_NOT_A_REPARSE_POINT = 0xC0000275
UNSUPPORTED = (
    STATUS_INVALID_INFO_CLASS,
    STATUS_INVALID_PARAMETER,
    STATUS_NOT_SUPPORTED,
)
# Defender, the search indexer and backup tools open new files for a moment.
TRANSIENT = (
    STATUS_ACCESS_DENIED,
    STATUS_SHARING_VIOLATION,
    STATUS_DELETE_PENDING,
    STATUS_CANNOT_DELETE,
)
RETRY_SECONDS = 2.0

ERROR_INVALID_PARAMETER = 87
ERROR_ALREADY_EXISTS = 183
ERROR_NO_MORE_FILES = 18
ERROR_LOCK_VIOLATION = 33
ERROR_IO_PENDING = 997

FSCTL_GET_REPARSE_POINT = 0x000900A8
IO_REPARSE_TAG_SYMLINK = 0xA000000C
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003

FILE_PERSISTENT_ACLS = 0x8
SE_FILE_OBJECT = 1
OWNER_SECURITY_INFORMATION = 0x1
DACL_SECURITY_INFORMATION = 0x4
SE_DACL_PROTECTED = 0x1000
ACCESS_ALLOWED_ACE_TYPE = 0
ACCESS_DENIED_ACE_TYPE = 1
SYSTEM_SID = "S-1-5-18"


class UNICODE_STRING(ctypes.Structure):  # noqa: N801 - Windows name
    _fields_ = (
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", wintypes.LPWSTR),
    )


class OBJECT_ATTRIBUTES(ctypes.Structure):  # noqa: N801 - Windows name
    _fields_ = (
        ("Length", wintypes.ULONG),
        ("RootDirectory", wintypes.HANDLE),
        ("ObjectName", ctypes.POINTER(UNICODE_STRING)),
        ("Attributes", wintypes.ULONG),
        ("SecurityDescriptor", wintypes.LPVOID),
        ("SecurityQualityOfService", wintypes.LPVOID),
    )


class IO_STATUS_BLOCK(ctypes.Structure):  # noqa: N801 - Windows name
    _fields_ = (("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t))


class FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):  # noqa: N801 - Windows name
    _fields_ = (("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD))


class OVERLAPPED(ctypes.Structure):
    _fields_ = (
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    )


class SECURITY_ATTRIBUTES(ctypes.Structure):  # noqa: N801 - Windows name
    _fields_ = (
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", wintypes.LPVOID),
        ("bInheritHandle", wintypes.BOOL),
    )


class ACL_SIZE_INFORMATION(ctypes.Structure):  # noqa: N801 - Windows name
    _fields_ = (
        ("AceCount", wintypes.DWORD),
        ("AclBytesInUse", wintypes.DWORD),
        ("AclBytesFree", wintypes.DWORD),
    )


ntdll.NtCreateFile.restype = ctypes.c_long
ntdll.NtCreateFile.argtypes = (
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.DWORD,
    ctypes.POINTER(OBJECT_ATTRIBUTES),
    ctypes.POINTER(IO_STATUS_BLOCK),
    ctypes.c_void_p,
    wintypes.ULONG,
    wintypes.ULONG,
    wintypes.ULONG,
    wintypes.ULONG,
    ctypes.c_void_p,
    wintypes.ULONG,
)
ntdll.NtSetInformationFile.restype = ctypes.c_long
ntdll.NtSetInformationFile.argtypes = (
    wintypes.HANDLE,
    ctypes.POINTER(IO_STATUS_BLOCK),
    ctypes.c_void_p,
    wintypes.ULONG,
    ctypes.c_int,
)
ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG
ntdll.RtlNtStatusToDosError.argtypes = (ctypes.c_long,)
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = (
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
)
kernel32.CreateDirectoryW.restype = wintypes.BOOL
kernel32.CreateDirectoryW.argtypes = (wintypes.LPCWSTR, ctypes.c_void_p)
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
kernel32.GetFileInformationByHandleEx.argtypes = (
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
)
kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
kernel32.GetFinalPathNameByHandleW.argtypes = (
    wintypes.HANDLE,
    wintypes.LPWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
)
kernel32.LockFileEx.restype = wintypes.BOOL
kernel32.LockFileEx.argtypes = (
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(OVERLAPPED),
)
kernel32.UnlockFileEx.restype = wintypes.BOOL
kernel32.UnlockFileEx.argtypes = (
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(OVERLAPPED),
)
kernel32.DeviceIoControl.restype = wintypes.BOOL
kernel32.DeviceIoControl.argtypes = (
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
)
kernel32.GetVolumeInformationByHandleW.restype = wintypes.BOOL
kernel32.GetVolumeInformationByHandleW.argtypes = (
    wintypes.HANDLE,
    wintypes.LPWSTR,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPWSTR,
    wintypes.DWORD,
)
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
advapi32.OpenProcessToken.argtypes = (
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.HANDLE),
)
advapi32.SetTokenInformation.argtypes = (
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
)
advapi32.GetTokenInformation.argtypes = (
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
)
advapi32.ConvertSidToStringSidW.argtypes = (
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.LPWSTR),
)
advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.c_void_p,
)
advapi32.GetSecurityInfo.restype = wintypes.DWORD
advapi32.GetSecurityInfo.argtypes = (
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.DWORD,
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_void_p),
)
advapi32.GetSecurityDescriptorControl.argtypes = (
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.WORD),
    ctypes.POINTER(wintypes.DWORD),
)
advapi32.GetAclInformation.argtypes = (
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.c_int,
)
advapi32.GetAce.argtypes = (
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(ctypes.c_void_p),
)


# Errors -----------------------------------------------------------------------


def _unsigned(status: int) -> int:
    return status & 0xFFFFFFFF


def _nt_error(status: int, name: str) -> OSError:
    status = _unsigned(status)
    known = {
        STATUS_OBJECT_NAME_NOT_FOUND: FileNotFoundError(
            errno.ENOENT, "No such file", name
        ),
        STATUS_OBJECT_PATH_NOT_FOUND: FileNotFoundError(
            errno.ENOENT, "No such file", name
        ),
        STATUS_DELETE_PENDING: FileNotFoundError(errno.ENOENT, "Being deleted", name),
        STATUS_OBJECT_NAME_COLLISION: FileExistsError(
            errno.EEXIST, "File exists", name
        ),
        STATUS_FILE_IS_A_DIRECTORY: IsADirectoryError(
            errno.EISDIR, "Is a directory", name
        ),
        STATUS_NOT_A_DIRECTORY: NotADirectoryError(
            errno.ENOTDIR, "Not a directory", name
        ),
        STATUS_DIRECTORY_NOT_EMPTY: OSError(
            errno.ENOTEMPTY, "Directory not empty", name
        ),
        STATUS_ACCESS_DENIED: PermissionError(errno.EACCES, "Access denied", name),
        STATUS_SHARING_VIOLATION: PermissionError(errno.EACCES, "In use", name),
        STATUS_CANNOT_DELETE: PermissionError(errno.EACCES, "Cannot delete", name),
        STATUS_OBJECT_NAME_INVALID: OSError(errno.EINVAL, "Invalid name", name),
        STATUS_NOT_SAME_DEVICE: OSError(errno.EXDEV, "Another drive", name),
    }
    if status in known:
        return known[status]
    winerror = ntdll.RtlNtStatusToDosError(ctypes.c_long(status).value)
    return OSError(0, f"NTSTATUS 0x{status:08X}", name, winerror)


def _link_error(name: str) -> OSError:
    # What O_NOFOLLOW reports on POSIX for a final symlink.
    return OSError(errno.ELOOP, "Refusing to follow a link", name)


def _check_name(name: str) -> None:
    """One plain component: never a path, a stream or a special name."""
    if (
        not name
        or name in {".", ".."}
        or any(c in name for c in "\\/:\0")
        or name.endswith((".", " "))
    ):
        raise OSError(errno.EINVAL, "Invalid name", name)


# Handles ----------------------------------------------------------------------


def _handle(descriptor: int) -> int:
    return msvcrt.get_osfhandle(descriptor)


def _wrap(handle: int, flags: int = os.O_RDONLY) -> int:
    try:
        return msvcrt.open_osfhandle(handle, flags)
    except BaseException:
        kernel32.CloseHandle(handle)
        raise


def _close(handle: int) -> None:
    kernel32.CloseHandle(handle)


def _attributes(handle: int) -> tuple[int, int]:
    """(attributes, reparse tag) of an open handle."""
    info = FILE_ATTRIBUTE_TAG_INFO()
    if kernel32.GetFileInformationByHandleEx(
        handle,
        FILE_ATTRIBUTE_TAG_INFO_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        return info.FileAttributes, info.ReparseTag
    error = ctypes.get_last_error()
    if error != ERROR_INVALID_PARAMETER:
        raise ctypes.WinError(error)
    # FAT and exFAT have no reparse points and refuse the tag query.
    basic = ctypes.create_string_buffer(40)  # FILE_BASIC_INFO
    if not kernel32.GetFileInformationByHandleEx(handle, 0, basic, len(basic)):
        raise ctypes.WinError(ctypes.get_last_error())
    return ctypes.c_ulong.from_buffer(basic, 32).value, 0


def _final_name(handle: int) -> str:
    size = 512
    while True:
        buffer = ctypes.create_unicode_buffer(size)
        # FILE_NAME_NORMALIZED | VOLUME_NAME_NONE: the long name as stored.
        length = kernel32.GetFinalPathNameByHandleW(handle, buffer, size, 0x4)
        if length == 0:
            raise ctypes.WinError(ctypes.get_last_error())
        if length < size:
            return buffer.value.rstrip("\\").rsplit("\\", 1)[-1]
        size = length + 1


def _nt_open(  # noqa: PLR0913
    parent: int,
    name: str,
    access: int,
    disposition: int,
    options: int,
    *,
    security: int | None = None,
) -> tuple[int, bool]:
    """Open ``name`` relative to the descriptor ``parent``; (handle, created).

    The entry is opened as itself if it is a reparse point; callers decide
    what to do with one. An existing entry must have exactly this name.
    """
    _check_name(name)
    text = ctypes.create_unicode_buffer(name)
    size = len(name.encode("utf-16-le"))
    unicode = UNICODE_STRING(size, size + 2, ctypes.cast(text, wintypes.LPWSTR))
    attributes = OBJECT_ATTRIBUTES(
        ctypes.sizeof(OBJECT_ATTRIBUTES),
        _handle(parent),
        ctypes.pointer(unicode),
        0,
        security,
        None,
    )
    handle = wintypes.HANDLE()
    status_block = IO_STATUS_BLOCK()
    status = ntdll.NtCreateFile(
        ctypes.byref(handle),
        access | SYNCHRONIZE | FILE_READ_ATTRIBUTES,
        ctypes.byref(attributes),
        ctypes.byref(status_block),
        None,
        FILE_ATTRIBUTE_NORMAL,
        SHARE_ALL,
        disposition,
        options | FILE_SYNCHRONOUS_IO_NONALERT | FILE_OPEN_REPARSE_POINT,
        None,
        0,
    )
    if status < 0:
        raise _nt_error(status, name)
    value = handle.value
    assert value is not None  # noqa: S101 - NtCreateFile succeeded
    created = status_block.Information == FILE_CREATED
    if not created:
        try:
            actual = _final_name(value)
        except BaseException:
            _close(value)
            raise
        if actual != name:
            # A case or 8.3 alias of another entry: POSIX would not find it.
            _close(value)
            raise FileNotFoundError(errno.ENOENT, "No such file", name)
    return value, created


def _plain(handle: int, name: str, *, directory: bool | None) -> None:
    """Refuse a reparse point, or the wrong kind of entry; closes on refusal."""
    try:
        attributes, _tag = _attributes(handle)
    except BaseException:
        _close(handle)
        raise
    if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        _close(handle)
        raise _link_error(name)
    if (
        directory is not None
        and bool(attributes & FILE_ATTRIBUTE_DIRECTORY) != directory
    ):
        _close(handle)
        if directory:
            raise NotADirectoryError(errno.ENOTDIR, "Not a directory", name)
        raise IsADirectoryError(errno.EISDIR, "Is a directory", name)


def _win32_open(
    path: Path | str, access: int, disposition: int, flags: int, security: int | None
) -> int:
    attributes = None
    if security is not None:
        attributes = SECURITY_ATTRIBUTES(
            ctypes.sizeof(SECURITY_ATTRIBUTES), security, False
        )
    handle = kernel32.CreateFileW(
        str(path),
        access | SYNCHRONIZE | FILE_READ_ATTRIBUTES,
        SHARE_ALL,
        ctypes.byref(attributes) if attributes is not None else None,
        disposition,
        flags,
        None,
    )
    if handle == INVALID_HANDLE_VALUE or handle is None:
        error = ctypes.get_last_error()
        raise OSError(0, ctypes.FormatError(error), str(path), error)
    return handle


# Security ---------------------------------------------------------------------


@cache
def _user_sid() -> str:
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        needed = wintypes.DWORD()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(
            token, 1, buffer, needed, ctypes.byref(needed)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.c_void_p.from_buffer(buffer).value  # TOKEN_USER.User.Sid
        return _sid_string(sid)
    finally:
        _close(token.value or 0)


def _own_new_objects() -> None:
    """Make this user the default owner of everything the process creates.

    An elevated process otherwise creates files owned by Administrators; on
    POSIX what you create is yours, and the stores' checks expect that.
    """
    token = wintypes.HANDLE()
    # TOKEN_QUERY | TOKEN_ADJUST_DEFAULT
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), 0x0008 | 0x0080, ctypes.byref(token)
    ):
        return
    try:
        needed = wintypes.DWORD()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(
            token, 1, buffer, needed, ctypes.byref(needed)
        ):
            return
        owner = ctypes.c_void_p(ctypes.c_void_p.from_buffer(buffer).value)
        # TOKEN_OWNER is one SID pointer; TokenOwner = 4.
        advapi32.SetTokenInformation(
            token, 4, ctypes.byref(owner), ctypes.sizeof(owner)
        )
    finally:
        _close(token.value or 0)


def _sid_string(sid: int | None) -> str:
    text = wintypes.LPWSTR()
    if not sid or not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return text.value or ""
    finally:
        kernel32.LocalFree(text)


@cache
def _descriptor(sddl: str) -> int:
    """A self-relative security descriptor, kept for the life of the process."""
    pointer = ctypes.c_void_p()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(pointer), None
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    assert pointer.value is not None  # noqa: S101 - conversion succeeded
    return pointer.value


def _private_descriptor() -> int:
    """Owned by this user; only this user and SYSTEM; nothing inherited."""
    user = _user_sid()
    return _descriptor(f"O:{user}D:P(A;OICI;FA;;;{user})(A;OICI;FA;;;SY)")


def _creation_descriptor(mode: int) -> int:
    """Private when ``mode`` gives nobody else access, as on POSIX."""
    return _private_descriptor() if not mode & 0o077 else _owner_descriptor()


def _owner_descriptor() -> int:
    """Owned by this user; access inherited from the folder it is created in."""
    return _descriptor(f"O:{_user_sid()}")


def _security(handle: int, which: int) -> tuple[int | None, int | None, int | None]:
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    result = advapi32.GetSecurityInfo(
        handle,
        SE_FILE_OBJECT,
        which,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if result != 0:
        raise OSError(0, ctypes.FormatError(result), None, result)
    return owner.value, dacl.value, descriptor.value


def _keeps_owners(handle: int) -> bool:
    """Whether the volume stores owners and access lists (not FAT or exFAT)."""
    flags = wintypes.DWORD()
    if not kernel32.GetVolumeInformationByHandleW(
        handle, None, 0, None, None, ctypes.byref(flags), None, 0
    ):
        return True  # can't tell: hold it to the stricter rule
    return bool(flags.value & FILE_PERSISTENT_ACLS)


def _owned_handle(handle: int) -> bool:
    if not _keeps_owners(handle):
        # Nothing on the drive belongs to anyone else, as when macOS mounts
        # an exFAT drive as the user who plugged it in.
        return True
    owner, _dacl, descriptor = _security(handle, OWNER_SECURITY_INFORMATION)
    try:
        return owner is not None and _sid_string(owner) == _user_sid()
    finally:
        kernel32.LocalFree(descriptor)


def _private_handle(handle: int) -> bool:
    try:
        owner, dacl, descriptor = _security(
            handle, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION
        )
    except OSError:
        return False  # a filesystem with no ACLs cannot keep anything private
    try:
        if owner is None or dacl is None or _sid_string(owner) != _user_sid():
            return False
        control = wintypes.WORD()
        revision = wintypes.DWORD()
        if (
            not advapi32.GetSecurityDescriptorControl(
                descriptor, ctypes.byref(control), ctypes.byref(revision)
            )
            or not control.value & SE_DACL_PROTECTED
        ):
            return False
        size = ACL_SIZE_INFORMATION()
        if not advapi32.GetAclInformation(
            dacl, ctypes.byref(size), ctypes.sizeof(size), 2
        ):
            return False
        allowed = {_user_sid(), SYSTEM_SID}
        for index in range(size.AceCount):
            ace = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, index, ctypes.byref(ace)) or ace.value is None:
                return False
            kind = ctypes.c_ubyte.from_address(ace.value).value
            if kind == ACCESS_DENIED_ACE_TYPE:
                continue
            if kind != ACCESS_ALLOWED_ACE_TYPE:
                return False  # any other grant is someone we can't vouch for
            # ACCESS_ALLOWED_ACE: header (4 bytes), mask (4 bytes), then the SID.
            if _sid_string(ace.value + 8) not in allowed:
                return False
        return True
    finally:
        kernel32.LocalFree(descriptor)


# Directories ------------------------------------------------------------------

_DIRECTORY_WRITE = FILE_GENERIC_READ | FILE_GENERIC_WRITE | FILE_TRAVERSE
_DIRECTORY_READ = FILE_GENERIC_READ | FILE_TRAVERSE


def open_dir(path: Path | str, *, follow: bool = False) -> int:
    """Pin the folder at ``path``; a final link is refused unless ``follow``.

    Opened for writing when allowed, so ``os.fsync`` can flush its entries.
    """
    flags = FILE_FLAG_BACKUP_SEMANTICS | (0 if follow else FILE_FLAG_OPEN_REPARSE_POINT)
    try:
        # Write access lets os.fsync flush the folder's entries.
        handle = _win32_open(path, _DIRECTORY_WRITE, OPEN_EXISTING, flags, None)
    except PermissionError:
        handle = _win32_open(path, _DIRECTORY_READ, OPEN_EXISTING, flags, None)
    if follow:
        attributes, _tag = _attributes(handle)
        if not attributes & FILE_ATTRIBUTE_DIRECTORY:
            _close(handle)
            raise NotADirectoryError(errno.ENOTDIR, "Not a directory", str(path))
    else:
        _plain(handle, str(path), directory=True)
    return _wrap(handle)


def open_dir_at(parent: int, name: str) -> int:
    """Pin the folder ``name`` inside ``parent``, never through a link."""
    try:
        handle, _created = _nt_open(
            parent, name, _DIRECTORY_WRITE, FILE_OPEN, FILE_DIRECTORY_FILE
        )
    except PermissionError:
        handle, _created = _nt_open(
            parent, name, _DIRECTORY_READ, FILE_OPEN, FILE_DIRECTORY_FILE
        )
    _plain(handle, name, directory=True)
    return _wrap(handle)


_own_new_objects()


def make_dir(
    path: Path | str, *, parents: bool = False, exist_ok: bool = False
) -> None:
    """Create the private folder ``path`` (and missing parents with ``parents``).

    The folder gets a protected access list for this user and SYSTEM only.
    Missing parents get the default access, as on POSIX.
    """
    target = Path(path)
    if parents and not target.parent.exists():
        # As on POSIX, missing parents get the default access; the folder
        # itself is private.
        target.parent.mkdir(parents=True, exist_ok=True)
    attributes = SECURITY_ATTRIBUTES(
        ctypes.sizeof(SECURITY_ATTRIBUTES), _private_descriptor(), False
    )
    if not kernel32.CreateDirectoryW(str(target), ctypes.byref(attributes)):
        error = ctypes.get_last_error()
        if error == ERROR_ALREADY_EXISTS:
            if exist_ok and target.is_dir():
                return
            raise FileExistsError(errno.EEXIST, "File exists", str(target))
        raise OSError(0, ctypes.FormatError(error), str(target), error)


def mkdir_at(parent: int, name: str, mode: int = 0o700) -> None:
    """Create the folder ``name`` inside ``parent`` (private unless ``mode`` says)."""
    security = _creation_descriptor(mode)
    handle, _created = _nt_open(
        parent,
        name,
        FILE_LIST_DIRECTORY,
        FILE_CREATE,
        FILE_DIRECTORY_FILE,
        security=security,
    )
    _close(handle)


def list_dir(handle: int) -> list[str]:
    """The names in a pinned folder, in no particular order."""
    native = _handle(handle)
    buffer = ctypes.create_string_buffer(64 * 1024)
    names: list[str] = []
    information = FILE_FULL_DIRECTORY_RESTART_INFO
    while True:
        if not kernel32.GetFileInformationByHandleEx(
            native, information, buffer, len(buffer)
        ):
            error = ctypes.get_last_error()
            if error == ERROR_NO_MORE_FILES:
                return names
            raise OSError(0, ctypes.FormatError(error), None, error)
        information = FILE_FULL_DIRECTORY_INFO
        offset = 0
        while True:
            # FILE_FULL_DIR_INFO: NextEntryOffset at 0, FileNameLength at 60,
            # FileName at 68.
            following = ctypes.c_ulong.from_buffer(buffer, offset).value
            length = ctypes.c_ulong.from_buffer(buffer, offset + 60).value
            start = offset + 68
            name = buffer.raw[start : start + length].decode("utf-16-le")
            if name not in {".", ".."}:
                names.append(name)
            if not following:
                break
            offset += following


# Files ------------------------------------------------------------------------


def _file_access(flags: int) -> tuple[int, int]:
    """(NT access, C runtime flags) for ``os.O_*`` flags."""
    mode = flags & (os.O_RDONLY | os.O_WRONLY | os.O_RDWR)
    if mode == os.O_WRONLY:
        access, runtime = FILE_GENERIC_WRITE, 0
    elif mode == os.O_RDWR:
        access, runtime = FILE_GENERIC_READ | FILE_GENERIC_WRITE, 0
    else:
        access, runtime = FILE_GENERIC_READ, os.O_RDONLY
    if flags & os.O_APPEND:
        runtime |= os.O_APPEND
    return access, runtime


def _nt_disposition(flags: int) -> int:
    """Never truncate while opening: the entry is checked first (see _truncate)."""
    if flags & os.O_CREAT and flags & os.O_EXCL:
        return FILE_CREATE
    return FILE_OPEN_IF if flags & os.O_CREAT else FILE_OPEN


def _truncate(descriptor: int, flags: int) -> int:
    """Apply O_TRUNC through a handle already checked to be the requested file."""
    if flags & os.O_TRUNC:
        try:
            os.ftruncate(descriptor, 0)
        except BaseException:
            os.close(descriptor)
            raise
    return descriptor


def open_at(parent: int, name: str, flags: int, mode: int = 0o600) -> int:
    """Open ``name`` inside ``parent`` with ``os.O_*`` flags, never via a link.

    A link, a folder or an alias of another name refuses. A new file is owned
    by this user; when ``mode`` gives nobody else access (the default) it gets
    the private access list (this user and SYSTEM only), as on POSIX.
    """
    access, runtime = _file_access(flags)
    disposition = _nt_disposition(flags)
    handle, _created = _nt_open(
        parent,
        name,
        access,
        disposition,
        FILE_NON_DIRECTORY_FILE,
        security=_creation_descriptor(mode) if flags & os.O_CREAT else None,
    )
    _plain(handle, name, directory=False)
    return _truncate(_wrap(handle, runtime), flags)


def open_path(path: Path | str, flags: int, mode: int = 0o600) -> int:
    """Open ``path`` with ``os.O_*`` flags; a final link is refused."""
    access, runtime = _file_access(flags)
    create = flags & os.O_CREAT
    if create and flags & os.O_EXCL:
        disposition = CREATE_NEW
    else:
        # Never truncate while opening: a link is refused first.
        disposition = OPEN_ALWAYS if create else OPEN_EXISTING
    handle = _win32_open(
        path,
        access,
        disposition,
        FILE_FLAG_OPEN_REPARSE_POINT | FILE_ATTRIBUTE_NORMAL,
        _creation_descriptor(mode) if create else None,
    )
    _plain(handle, str(path), directory=False)
    return _truncate(_wrap(handle, runtime), flags)


def sync_at(parent: int, name: str) -> None:
    """Make the data of the file ``name`` inside ``parent`` durable.

    Windows flushes only through a handle with write access, so the file is
    opened for writing (never created or truncated) just to flush it.
    """
    handle, _created = _nt_open(
        parent, name, FILE_GENERIC_WRITE, FILE_OPEN, FILE_NON_DIRECTORY_FILE
    )
    _plain(handle, name, directory=False)
    descriptor = _wrap(handle, 0)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def free_bytes(where: int | Path | str) -> int:
    """Free space on the drive holding a pinned folder (handle) or a path."""
    if isinstance(where, int):
        with _pinned_path(where) as path:
            return shutil.disk_usage(path).free
    return shutil.disk_usage(where).free


def stat_at(parent: int, name: str, *, follow: bool = False) -> os.stat_result:
    """Stat ``name`` inside ``parent``; any link is described as a link.

    Junctions and other reparse points report ``S_IFLNK`` too, so the stores
    never take one for a plain file or folder.
    """
    if follow:
        # Let the system resolve a final link from here.
        _check_name(name)
        with _pinned_path(parent) as base:
            return os.stat(base / name)
    handle, _created = _nt_open(parent, name, FILE_READ_ATTRIBUTES, FILE_OPEN, 0)
    descriptor = _wrap(handle)
    try:
        info = os.fstat(descriptor)
        attributes, tag = _attributes(handle)
    finally:
        os.close(descriptor)
    if not attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        return info
    # Any reparse point is a link here, junctions included: it is never a
    # plain file or folder to the stores.
    values = list(info)
    values[0] = stat.S_IFLNK | (info.st_mode & 0o7777)
    extra = {
        field: getattr(info, field)
        for field in (
            "st_atime",
            "st_mtime",
            "st_ctime",
            "st_atime_ns",
            "st_mtime_ns",
            "st_ctime_ns",
            "st_file_attributes",
            "st_birthtime",
            "st_birthtime_ns",
        )
        if hasattr(info, field)
    }
    extra["st_reparse_tag"] = tag
    return os.stat_result(values[:10], extra)


class _pinned_path:  # noqa: N801 - used as a context manager
    """The path of a pinned folder, for the rare call that needs one."""

    def __init__(self, parent: int) -> None:
        self.parent = parent

    def __enter__(self) -> Path:
        native = _handle(self.parent)
        size = 1024
        while True:
            buffer = ctypes.create_unicode_buffer(size)
            length = kernel32.GetFinalPathNameByHandleW(native, buffer, size, 0)
            if length == 0:
                raise ctypes.WinError(ctypes.get_last_error())
            if length < size:
                return Path(buffer.value)
            size = length + 1

    def __exit__(self, *_exc: object) -> None:
        return None


def _set_information(handle: int, data: ctypes.Array[ctypes.c_char], kind: int) -> int:
    status_block = IO_STATUS_BLOCK()
    return _unsigned(
        ntdll.NtSetInformationFile(
            handle, ctypes.byref(status_block), data, len(data), kind
        )
    )


def _with_retry(action: Callable[[], int], name: str) -> None:
    """Run ``action`` (returns an NTSTATUS), retrying another program's brief hold."""
    deadline = time.monotonic() + RETRY_SECONDS
    delay = 0.01
    while True:
        status = action()
        if status < 0x80000000:
            return
        if status not in TRANSIENT or time.monotonic() >= deadline:
            raise _nt_error(status, name)
        time.sleep(delay)
        delay = min(delay * 2, 0.2)


def _name_information(flags: int, root: int, name: str) -> ctypes.Array[ctypes.c_char]:
    """FILE_RENAME_INFORMATION(_EX) / FILE_LINK_INFORMATION(_EX), x64 layout."""
    encoded = name.encode("utf-16-le")
    data = ctypes.create_string_buffer(max(20 + len(encoded) + 2, 24))
    ctypes.c_ulong.from_buffer(data, 0).value = flags
    ctypes.c_void_p.from_buffer(data, 8).value = root
    ctypes.c_ulong.from_buffer(data, 16).value = len(encoded)
    ctypes.memmove(ctypes.addressof(data) + 20, encoded, len(encoded))
    return data


def _open_to_change(parent: int, name: str, access: int, options: int) -> int:
    """Open for a rename, link or delete, waiting out another program's brief hold."""
    deadline = time.monotonic() + RETRY_SECONDS
    delay = 0.01
    while True:
        try:
            handle, _created = _nt_open(parent, name, access, FILE_OPEN, options)
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.2)
        else:
            return handle


def _delete(handle: int, name: str) -> None:
    def action() -> int:
        data = ctypes.create_string_buffer(4)
        ctypes.c_ulong.from_buffer(data, 0).value = (
            DISPOSITION_DELETE
            | DISPOSITION_POSIX_SEMANTICS
            | DISPOSITION_IGNORE_READONLY_ATTRIBUTE
        )
        status = _set_information(handle, data, FILE_DISPOSITION_INFORMATION_EX)
        if status in UNSUPPORTED:  # FAT and exFAT: classic delete-on-close
            classic = ctypes.create_string_buffer(1)
            classic[0] = 1
            status = _set_information(handle, classic, FILE_DISPOSITION_INFORMATION)
        return status

    _with_retry(action, name)


def unlink_at(parent: int, name: str) -> None:
    """Remove the file or link ``name`` from ``parent`` (POSIX semantics)."""
    handle = _open_to_change(parent, name, DELETE, 0)
    try:
        attributes, _tag = _attributes(handle)
        if (
            attributes & FILE_ATTRIBUTE_DIRECTORY
            and not attributes & FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise IsADirectoryError(errno.EISDIR, "Is a directory", name)
        _delete(handle, name)
    finally:
        _close(handle)


def rmdir_at(parent: int, name: str) -> None:
    """Remove the empty folder ``name`` from ``parent``."""
    handle = _open_to_change(parent, name, DELETE, FILE_DIRECTORY_FILE)
    try:
        attributes, _tag = _attributes(handle)
        if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
            raise NotADirectoryError(errno.ENOTDIR, "Not a directory", name)
        _delete(handle, name)
    finally:
        _close(handle)


def replace_at(
    source_parent: int, source: str, target_parent: int, target: str
) -> None:
    """Rename between pinned folders, atomically replacing ``target``.

    A file another handle has open is replaced too (POSIX semantics); a brief
    hold by another program is waited out for up to two seconds. A folder
    renamed over an empty folder takes its place, as on POSIX.
    """
    _check_name(target)
    handle = _open_to_change(source_parent, source, DELETE, 0)
    try:
        root = _handle(target_parent)
        directory = bool(_attributes(handle)[0] & FILE_ATTRIBUTE_DIRECTORY)

        def action() -> int:
            flags = (
                RENAME_REPLACE_IF_EXISTS
                | RENAME_POSIX_SEMANTICS
                | RENAME_IGNORE_READONLY_ATTRIBUTE
            )
            status = _set_information(
                handle,
                _name_information(flags, root, target),
                FILE_RENAME_INFORMATION_EX,
            )
            if status in UNSUPPORTED:
                status = _set_information(
                    handle,
                    _name_information(1, root, target),
                    FILE_RENAME_INFORMATION,
                )
            return status

        if directory:
            # POSIX renames a folder over an empty folder; Windows won't
            # replace a folder, so the empty one goes first (a non-empty one
            # refuses here, as on POSIX).
            try:
                rmdir_at(target_parent, target)
            except FileNotFoundError:
                pass
        _with_retry(action, target)
    finally:
        _close(handle)


def rename_at(source_parent: int, source: str, target_parent: int, target: str) -> None:
    # os.rename on POSIX replaces an existing target, as replace does.
    """Rename between pinned folders (``os.rename`` semantics on POSIX: replaces)."""
    replace_at(source_parent, source, target_parent, target)


def link_at(source_parent: int, source: str, target_parent: int, target: str) -> None:
    """Hard-link ``source`` as ``target``; a link is linked, never followed.

    Refuses where the drive has no hard links (FAT, exFAT); callers copy.
    """
    _check_name(target)
    handle, _created = _nt_open(
        source_parent, source, 0, FILE_OPEN, FILE_NON_DIRECTORY_FILE
    )
    try:
        root = _handle(target_parent)
        status = _set_information(
            handle,
            _name_information(LINK_POSIX_SEMANTICS, root, target),
            FILE_LINK_INFORMATION_EX,
        )
        if status in UNSUPPORTED:
            status = _set_information(
                handle, _name_information(0, root, target), FILE_LINK_INFORMATION
            )
        if status >= 0x80000000:
            raise _nt_error(status, target)
    finally:
        _close(handle)


def link_path(source: Path | str, target: Path | str) -> None:
    """Hard-link ``source`` as ``target`` by path; a link is linked, not followed."""
    os.link(source, target, follow_symlinks=False)


def readlink_at(parent: int, name: str) -> str:
    """The target of the link ``name`` inside ``parent``."""
    handle, _created = _nt_open(parent, name, FILE_GENERIC_READ, FILE_OPEN, 0)
    try:
        buffer = ctypes.create_string_buffer(16 * 1024)
        returned = wintypes.DWORD()
        if not kernel32.DeviceIoControl(
            handle,
            FSCTL_GET_REPARSE_POINT,
            None,
            0,
            buffer,
            len(buffer),
            ctypes.byref(returned),
            None,
        ):
            error = ctypes.get_last_error()
            raise OSError(errno.EINVAL, "Not a link", name, error)
        tag = ctypes.c_ulong.from_buffer(buffer, 0).value
        # REPARSE_DATA_BUFFER: tag, data length, reserved (8 bytes), then
        # substitute and print name offsets and lengths.
        if tag == IO_REPARSE_TAG_SYMLINK:
            names = 8 + 12  # + Flags
        elif tag == IO_REPARSE_TAG_MOUNT_POINT:
            names = 8 + 8
        else:
            raise OSError(errno.EINVAL, "Not a link", name)
        offset = ctypes.c_ushort.from_buffer(buffer, 12).value
        length = ctypes.c_ushort.from_buffer(buffer, 14).value
        if not length:  # no print name: use the substitute name
            offset = ctypes.c_ushort.from_buffer(buffer, 8).value
            length = ctypes.c_ushort.from_buffer(buffer, 10).value
        start = names + offset
        target = buffer.raw[start : start + length].decode("utf-16-le")
        return target.removeprefix("\\??\\")
    finally:
        _close(handle)


# remove_tree never follows a link on Windows: every entry is opened as itself.
TREE_REMOVAL_SAFE = True


def remove_tree(path: Path | str) -> None:
    """Remove the folder ``path`` and everything in it, never following a link.

    A link at ``path`` itself is refused, as ``shutil.rmtree`` refuses it.
    """
    target = Path(path)
    parent = open_dir(target.parent, follow=True)
    try:
        if stat.S_ISLNK(stat_at(parent, target.name).st_mode):
            raise OSError(errno.ELOOP, "Cannot remove a tree through a link", str(path))
        remove_tree_at(parent, target.name)
    finally:
        os.close(parent)


def remove_tree_at(parent: int, name: str, *, ignore_errors: bool = False) -> None:
    """Remove the folder ``name`` inside ``parent`` and everything in it.

    Links inside are removed, never followed.
    """
    try:
        _remove_tree(parent, name, ignore_errors=ignore_errors)
    except OSError:
        if not ignore_errors:
            raise


def _remove_tree(parent: int, name: str, *, ignore_errors: bool) -> None:
    handle = _open_to_change(
        parent, name, DELETE | FILE_LIST_DIRECTORY | FILE_TRAVERSE, 0
    )
    try:
        attributes, _tag = _attributes(handle)
        if (
            attributes & FILE_ATTRIBUTE_DIRECTORY
            and not attributes & FILE_ATTRIBUTE_REPARSE_POINT
        ):
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
            handle = 0  # owned by the descriptor now
            try:
                for child in list_dir(descriptor):
                    try:
                        _remove_tree(descriptor, child, ignore_errors=ignore_errors)
                    except OSError:
                        if not ignore_errors:
                            raise
                _delete(_handle(descriptor), name)
            finally:
                os.close(descriptor)
        else:
            _delete(handle, name)  # a file or a link: removed, never followed
    finally:
        if handle:
            _close(handle)


# Locks ------------------------------------------------------------------------


def _region() -> OVERLAPPED:
    # One byte far past the end, so reading the lock file is never blocked.
    return OVERLAPPED(None, None, 0xFFFFFFFF, 0x7FFFFFFF, None)


def lock(handle: int, *, wait: bool = False) -> None:
    """Take the exclusive writer lock on an open file.

    Without ``wait``, raises ``BlockingIOError`` when another holder has it.
    Windows releases the lock if this process dies.
    """
    flags = 0x2 | (0 if wait else 0x1)  # EXCLUSIVE | FAIL_IMMEDIATELY
    region = _region()
    if not kernel32.LockFileEx(_handle(handle), flags, 0, 1, 0, ctypes.byref(region)):
        error = ctypes.get_last_error()
        if error in (ERROR_LOCK_VIOLATION, ERROR_IO_PENDING):
            raise BlockingIOError(errno.EAGAIN, "Lock held by another writer")
        raise OSError(0, ctypes.FormatError(error), None, error)


def unlock(handle: int) -> None:
    """Release the lock from :func:`lock`."""
    region = _region()
    if not kernel32.UnlockFileEx(_handle(handle), 0, 1, 0, ctypes.byref(region)):
        error = ctypes.get_last_error()
        raise OSError(0, ctypes.FormatError(error), None, error)


# Ownership --------------------------------------------------------------------


def owned(handle: int) -> bool:
    """Whether this user owns the open file or folder.

    On a drive with no owners (FAT, exFAT) everything is the user's.
    """
    return _owned_handle(_handle(handle))


def path_owned(path: Path | str, *, follow: bool = True) -> bool:
    """Whether this user owns ``path`` (a final link is followed by default)."""
    flags = FILE_FLAG_BACKUP_SEMANTICS | (0 if follow else FILE_FLAG_OPEN_REPARSE_POINT)
    handle = _win32_open(path, READ_CONTROL, OPEN_EXISTING, flags, None)
    try:
        return _owned_handle(handle)
    finally:
        _close(handle)


def path_private(path: Path | str) -> bool:
    """Whether only this user and SYSTEM can reach ``path`` (link followed)."""
    handle = _win32_open(
        path, READ_CONTROL, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None
    )
    try:
        return _private_handle(handle)
    finally:
        _close(handle)


def private(handle: int) -> bool:
    """Whether only this user and SYSTEM can reach the open file or folder.

    The access list must be protected, grant no one else, and the owner must
    be this user. Nothing on a drive without access lists is private.
    """
    return _private_handle(_handle(handle))


_JSON_SAFE = 2**53


def _fold(value: int) -> int:
    # Windows volume serial numbers and file IDs can exceed what JSON keeps
    # exactly; larger values fold to 53 bits (a collision is ~2**-53).
    if 0 <= value < _JSON_SAFE:
        return value
    digest = hashlib.blake2b(value.to_bytes(16, "big", signed=False), digest_size=8)
    return int.from_bytes(digest.digest(), "big") >> 11


def identity(info: os.stat_result) -> tuple[int, int]:
    """The (volume, file) pair that names one file or folder while it exists.

    Both stay within JSON's exact integer range, so identities can be stored
    in the stores' state files.
    """
    return (_fold(info.st_dev), _fold(info.st_ino))
