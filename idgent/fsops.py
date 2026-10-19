"""Filesystem actions shared by the explorer and its tests."""

import ctypes
import os
from pathlib import Path
import shutil

from gi.repository import Gio, GLib


class FsOpError(Exception):
    pass


class TrashUnavailable(FsOpError):
    """This location cannot use trash; permanent removal needs explicit consent."""


def validate_name(name: str) -> None:
    if not isinstance(name, str) or not name or name in (".", "..") or "/" in name or "\0" in name:
        raise FsOpError("Invalid name: enter a name without '/' or NUL; '.' and '..' are not allowed.")


def _absolute(path: str) -> str:
    return os.path.abspath(os.fspath(path))


def _error(error: OSError, path: str) -> FsOpError:
    if isinstance(error, FileExistsError):
        return FsOpError(f"Already exists: {path}")
    if isinstance(error, PermissionError):
        return FsOpError(f"Permission denied: {path}")
    if isinstance(error, FileNotFoundError):
        return FsOpError(f"Not found: {path}")
    return FsOpError(f"Cannot modify {path}: {error.strerror or error}")


def create_file(parent: str, name: str) -> str:
    validate_name(name)
    path = _absolute(os.path.join(parent, name))
    try:
        with open(path, "x"):
            pass
    except OSError as error:
        raise _error(error, path) from error
    return path


def create_folder(parent: str, name: str) -> str:
    validate_name(name)
    path = _absolute(os.path.join(parent, name))
    try:
        os.mkdir(path)
    except OSError as error:
        raise _error(error, path) from error
    return path


def _rename_no_replace(source: str, target: str) -> None:
    """Linux renameat2 closes the check/rename race without replacing any entry."""
    library = ctypes.CDLL(None, use_errno=True)
    try:
        operation = library.renameat2
    except AttributeError as error:
        raise FsOpError("Safe rename is unavailable on this system.") from error
    operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    operation.restype = ctypes.c_int
    if operation(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), target)


def rename(path: str, new_name: str) -> str:
    validate_name(new_name)
    source = _absolute(path)
    target = str(Path(source).with_name(new_name))
    try:
        if not os.path.lexists(source):
            raise FileNotFoundError(source)
        if os.path.lexists(target):
            raise FileExistsError(target)
        _rename_no_replace(source, target)
    except OSError as error:
        raise _error(error, target) from error
    return target


def _removal_target(path: str, project_root: str | None) -> str:
    """Apply the same root and symlink protections to both removal modes."""
    target = _absolute(path)
    resolved = os.path.realpath(target)
    # A symlink is an entry to unlink, even when it points at a protected root.
    if (project_root is not None and target == _absolute(project_root)) or target == os.path.abspath(os.sep) or (
        not os.path.islink(target)
        and (resolved == os.path.abspath(os.sep)
             or (project_root is not None and resolved == os.path.realpath(project_root)))
    ):
        raise FsOpError(f"Cannot delete a filesystem or project root: {target}")
    return target


def trash(path: str, project_root: str | None = None) -> str:
    """Move an entry to the system trash. Never silently delete it instead."""
    target = _removal_target(path, project_root)
    try:
        if not Gio.File.new_for_path(target).trash(None):
            raise TrashUnavailable(f"Trash is unavailable for: {target}")
    except GLib.Error as error:
        if error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_SUPPORTED):
            raise TrashUnavailable(f"Trash is unavailable for: {target}") from error
        raise FsOpError(f"Cannot move {target} to trash: {error.message}") from error
    return target


def delete(path: str, project_root: str | None = None) -> str:
    """Remove an entry, protecting the filesystem root and an optional project root."""
    target = _removal_target(path, project_root)
    try:
        if os.path.islink(target) or not os.path.isdir(target):
            os.unlink(target)
        else:
            shutil.rmtree(target)
    except OSError as error:
        raise _error(error, target) from error
    return target
