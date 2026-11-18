"""The filesystem calls the stores' safety rests on, in one place.

Every store is reached through a pinned directory handle, and every path
inside it is opened one component at a time relative to such a handle without
following links. Publication is a synced write, a rename in the same directory
and a sync of that directory. Writers are fenced by locks the operating system
drops when a process dies, and private folders are readable by their owner
alone.

On macOS and Linux these are the POSIX calls (``openat`` with ``O_NOFOLLOW``,
``renameat``, ``flock``, owner and mode bits) made exactly as before this module
existed. Windows gets the same guarantees from its own handle-relative calls
(design: foxlight-docs windows-storage-2026-11-17); callers never branch on the
platform.

Every handle here is an ordinary descriptor on both platforms, directories
included, so ``os.close``, ``os.dup``, ``os.fstat``, ``os.fsync`` and
``os.fdopen`` keep working on them. What needs this module is anything that
names a path relative to a pinned directory, lists one, locks, or asks who
may read a file.
"""

from __future__ import annotations

import os
import sys

PRIVATE_DIRECTORY = 0o700
PRIVATE_FILE = 0o600
# Opening never blocks on a FIFO or device; Windows has neither in a store.
NONBLOCK: int = getattr(os, "O_NONBLOCK", 0)

if sys.platform == "win32":  # the NT handle-relative calls
    from ._winfs import *  # noqa: F403
else:  # the POSIX calls, exactly as made before this module
    from ._posixfs import *  # noqa: F403
