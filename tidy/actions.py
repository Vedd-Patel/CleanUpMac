"""
The only module that deletes anything.

Every path is checked against a short list of rules before `rm` happens:
it must be inside the home folder, it must not be the home folder or one of
its top-level personal folders, and it must be a path the scanner reported
for that item. Commands run with a fixed argument list, never through a
shell string built from user input.
"""

import os
import shutil
import subprocess
import time

from .scan import HOME, run, shorten

PROTECTED = {os.path.join(HOME, n) for n in (
    "Desktop", "Documents", "Downloads", "Pictures", "Movies", "Music",
    "Library", "Applications", "Public",
)} | {HOME}


class Refused(Exception):
    pass


def check(path):
    real = os.path.realpath(path)
    if real in PROTECTED or os.path.normpath(path) in PROTECTED:
        raise Refused(f"refusing to touch {shorten(real)}")
    if not real.startswith(HOME + os.sep):
        raise Refused(f"{shorten(path)} is outside your home folder")


def quit_apps(names):
    """Ask apps to quit the polite way and wait a bit. Returns what was quit."""
    quit = []
    for n in names:
        code, _, _ = run(["pgrep", "-x", n], timeout=5)
        if code != 0:
            continue
        run(["osascript", "-e", f'tell application "{n}" to quit'], timeout=15)
        quit.append(n)
    deadline = time.time() + 10
    while quit and time.time() < deadline:
        if all(run(["pgrep", "-x", n], timeout=5)[0] != 0 for n in quit):
            break
        time.sleep(0.5)
    return quit


def remove_path(p):
    if os.path.islink(p) or not os.path.isdir(p):
        os.unlink(p)
    else:
        shutil.rmtree(p, ignore_errors=True)
        if os.path.exists(p):  # something refused (SIP, in-use); try again later
            shutil.rmtree(p, ignore_errors=True)


def empty(folder):
    check(folder)
    if not os.path.isdir(folder) or os.path.islink(folder):
        return
    for name in os.listdir(folder):
        p = os.path.join(folder, name)
        try:
            remove_path(p)
        except OSError:
            pass


def remove(path):
    check(path)
    if os.path.lexists(path):
        remove_path(path)


def clean(item):
    """Perform the item's action. Returns a dict with what happened; raises Refused."""
    kind = item["action"]
    if kind == "none":
        raise Refused("this item can only be cleaned by hand")

    quit = quit_apps(item.get("quit") or [])
    result = dict(quit=quit)

    if kind == "empty":
        for p in item["real_paths"]:
            empty(p)
    elif kind == "remove":
        for p in item["real_paths"]:
            remove(p)
    elif kind == "run":
        try:
            out = subprocess.run(item["cmd"], capture_output=True, text=True, timeout=1800)
        except FileNotFoundError:
            raise Refused(f"{item['cmd'][0]} is not installed")
        except subprocess.TimeoutExpired:
            raise Refused("the command took longer than 30 minutes and was stopped")
        tail = (out.stdout.strip().splitlines() or out.stderr.strip().splitlines() or [""])[-1]
        result["output"] = tail
        if out.returncode != 0:
            raise Refused(tail or f"{item['cmd'][0]} exited with {out.returncode}")
    else:
        raise Refused(f"unknown action {kind}")
    return result


def reveal(item):
    """Select the item in Finder."""
    for p in item.get("real_paths") or []:
        if os.path.exists(p):
            subprocess.run(["open", "-R", p], check=False)
            return True
    return False
