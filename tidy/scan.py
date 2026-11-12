"""
Measures everything in the catalog and discovers a few things that are not
in it (big items in Downloads, big folders in the home directory).

A scan runs in a background thread. The web page polls `snapshot()` and
draws rows as they finish, so a slow `du` on a 60 GB Library folder does
not block the rest of the list.
"""

import json
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import catalog

HOME = os.path.expanduser("~")

# Names in the home folder that we never offer to delete and never list as
# "large folders"; they are either broken down elsewhere or obviously yours.
HOME_SKIP = {"Library", "Downloads", "Desktop", "Documents", "Pictures", "Movies",
             "Music", "Applications", "Public", ".Trash"}

# Folders that are common enough to describe, but not safe enough to offer a
# Delete button for. Shown under "Large folders" with this text.
KNOWN = {
    "~/.vscode": "VS Code extensions. Uninstall the ones you don't use from the Extensions panel.",
    "~/.nvm": "Node.js versions installed with nvm. `nvm ls`, then `nvm uninstall <version>` for old ones.",
    "~/.gemini": "Data for Gemini CLI and Antigravity: conversation history, caches, downloaded models.",
    "~/.antigravity-ide": "Extensions for the Antigravity editor.",
    "~/.antigravity": "Extensions and data for Antigravity.",
    "~/.codex": "Codex CLI sessions and logs.",
    "~/.nuget": "NuGet packages for .NET. `dotnet nuget locals all --clear` empties it.",
    "~/.m2": "Maven's local repository. Safe to delete; Maven downloads again.",
    "~/.sonarlint": "SonarLint analyzers and caches. Recreated by the extension.",
    "~/.rustup": "Rust toolchains. See the Rust toolchains row.",
    "~/Library/Application Support/Google": "Chrome profiles: history, passwords, extensions. Clear browsing data from inside Chrome instead.",
    "~/Library/Application Support/Claude": "The Claude desktop app's data and caches.",
    "~/Library/Application Support/Code": "VS Code settings, workspace state and history. Its caches are listed separately.",
    "~/Library/Application Support/Steam": "Steam games and client files.",
    "~/Library/Android": "The Android SDK. Remove old platforms and system images from Android Studio's SDK Manager.",
    "~/Library/Messages": "iMessage history and attachments. Manage it in Settings > General > Storage.",
    "~/Library/Mobile Documents": "iCloud Drive files kept on this Mac.",
    "~/Library/Mail": "Downloaded mail and attachments.",
}

DOWNLOADS_MIN = 300 * 1024 * 1024
LARGE_MIN = 1024 * 1024 * 1024


def expand(p):
    return os.path.expanduser(p)


def shorten(p):
    return "~" + p[len(HOME):] if p.startswith(HOME) else p


def run(cmd, timeout=60):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.returncode, out.stdout, out.stderr
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 127, "", ""


def du(paths):
    """Total apparent size in bytes of the given paths. Missing ones count as 0."""
    paths = [p for p in paths if os.path.lexists(p)]
    if not paths:
        return 0
    code, out, _ = run(["du", "-sk"] + paths, timeout=600)
    total = 0
    for line in out.splitlines():
        m = re.match(r"(\d+)\s", line)
        if m:
            total += int(m.group(1)) * 1024
    return total


def du_children(folder):
    """{child_path: bytes} for every entry directly inside `folder`, in one du call."""
    try:
        names = os.listdir(folder)
    except OSError:
        return {}
    paths = [os.path.join(folder, n) for n in names]
    if not paths:
        return {}
    sizes = {}
    # du takes many args; split so the command line never gets silly
    for i in range(0, len(paths), 200):
        code, out, _ = run(["du", "-sk"] + paths[i:i + 200], timeout=900)
        for line in out.splitlines():
            m = re.match(r"(\d+)\s+(.*)$", line)
            if m:
                sizes[m.group(2)] = int(m.group(1)) * 1024
    return sizes


def last_changed(paths):
    """Newest mtime across the paths and their direct children. Cheap, good enough."""
    newest = 0
    for p in paths:
        try:
            newest = max(newest, os.lstat(p).st_mtime)
            if os.path.isdir(p):
                with os.scandir(p) as it:
                    for e in it:
                        try:
                            newest = max(newest, e.stat(follow_symlinks=False).st_mtime)
                        except OSError:
                            pass
        except OSError:
            pass
    return newest or None


def running_apps(names):
    found = []
    for n in names or []:
        code, _, _ = run(["pgrep", "-x", n], timeout=5)
        if code == 0:
            found.append(n)
    return found


# ---------------------------------------------------------------- special measures

_unit = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12,
         "KIB": 1024, "MIB": 1024 ** 2, "GIB": 1024 ** 3}


def _docker_bytes(s):
    m = re.match(r"([\d.]+)\s*([A-Za-z]+)", s or "")
    return int(float(m.group(1)) * _unit.get(m.group(2).upper(), 1)) if m else 0


_docker_cache = {"at": 0, "rows": None}


def docker_df():
    """Rows from `docker system df`, cached for a few seconds since every Docker item asks."""
    if time.time() - _docker_cache["at"] < 5 and _docker_cache["rows"] is not None:
        return _docker_cache["rows"]
    code, out, _ = run(["docker", "system", "df", "--format", "{{json .}}"], timeout=20)
    rows = None
    if code == 0:
        rows = {}
        for line in out.splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            rows[r.get("Type")] = r
    _docker_cache.update(at=time.time(), rows=rows)
    return rows


def measure_docker(kind):
    rows = docker_df()
    if rows is None:
        return dict(size=None, note="Docker is not running")
    r = rows.get(kind)
    if not r:
        return dict(size=0)
    size = _docker_bytes(r.get("Size"))
    recl = _docker_bytes(r.get("Reclaimable", "").split(" ")[0])
    return dict(size=size, reclaimable=recl,
                note=f"{r.get('TotalCount', '?')} total, {r.get('Active', '?')} in use")


def measure_simruntimes():
    code, out, _ = run(["xcrun", "simctl", "runtime", "list", "-j"], timeout=30)
    if code != 0:
        return dict(size=None, note="Xcode not installed")
    try:
        data = json.loads(out)
    except ValueError:
        return dict(size=None)
    total, names = 0, []
    for r in data.values():
        total += r.get("sizeBytes", 0) or 0
        names.append(f"{r.get('platformIdentifier', '').split('.')[-1]} {r.get('version', '')}".strip())
    return dict(size=total, note=", ".join(names) or "none installed")


def measure_snapshots():
    code, out, _ = run(["tmutil", "listlocalsnapshots", "/"], timeout=20)
    names = [l.strip() for l in out.splitlines() if l.startswith("com.apple")]
    if not names:
        return dict(size=0, note="no snapshots")
    updates = sum(1 for n in names if "os.update" in n)
    note = f"{len(names)} snapshot{'s' if len(names) != 1 else ''}"
    if updates:
        note += f", {updates} from a pending macOS update"
    return dict(size=None, note=note + ". Size is not reported without sudo.")


SPECIAL = {"simruntimes": measure_simruntimes, "snapshots": measure_snapshots}


# ---------------------------------------------------------------- disk

def disk():
    st = os.statvfs("/")
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    return dict(total=total, free=free, used=total - free)


# ---------------------------------------------------------------- the scan

class Scan:
    def __init__(self):
        self.lock = threading.Lock()
        self.items = {}
        self.order = []
        self.running = False
        self.started = None
        self.finished = None
        self.covered = []   # every real path the catalog owns

    # --- public

    def start(self):
        with self.lock:
            if self.running:
                return
            self.running = True
            self.started = time.time()
            self.finished = None
            self.items = {}
            self.order = []
        threading.Thread(target=self._run, daemon=True).start()

    def snapshot(self):
        with self.lock:
            return dict(running=self.running, started=self.started, finished=self.finished,
                        items=[self.items[i] for i in self.order])

    def get(self, item_id):
        with self.lock:
            return self.items.get(item_id)

    def remeasure(self, item_id):
        """Measure one item again after it was cleaned. Returns the updated item."""
        item = self.get(item_id)
        if not item:
            return None
        _docker_cache["at"] = 0
        fresh = self._measure(item)
        with self.lock:
            self.items[item_id].update(fresh)
            return dict(self.items[item_id])

    # --- internals

    def _put(self, item):
        with self.lock:
            if item["id"] not in self.items:
                self.order.append(item["id"])
            self.items[item["id"]] = item

    def _run(self):
        try:
            self._scan_catalog()
            self._scan_downloads()
            self._scan_large()
        finally:
            with self.lock:
                self.running = False
                self.finished = time.time()

    def _scan_catalog(self):
        self.covered = [expand(p) for t in catalog.TARGETS for p in t["paths"]]

        items = []
        for t in catalog.TARGETS:
            real = [expand(p) for p in t["paths"]]
            if t.get("exclude_covered"):
                real = self._uncovered_children(real)
            item = dict(
                id=t["id"], name=t["name"], group=t["group"], what=t["what"],
                used_by=t["used_by"], after=t.get("after", ""), level=t["level"],
                action=t["action"][0], how=t.get("how", ""),
                paths=[shorten(p) for p in (t["paths"] or [])],
                real_paths=real, cmd=t["action"][1] if t["action"][0] == "run" else None,
                apps=t.get("apps", []), quit=t.get("quit", []), measure=t.get("measure"),
                exists=None, size=None, reclaimable=None, note="", last_changed=None,
                running=[], measured=False, error=None,
            )
            items.append(item)
            self._put(item)

        # du several things at once; most are small and finish fast, so the page
        # fills in from the top while the big ones are still counting
        with ThreadPoolExecutor(max_workers=4) as pool:
            for item, fresh in zip(items, pool.map(self._measure, items)):
                with self.lock:
                    self.items[item["id"]].update(fresh)

    def _uncovered_children(self, folders):
        out = []
        for f in folders:
            try:
                names = os.listdir(f)
            except OSError:
                continue
            for n in names:
                p = os.path.join(f, n)
                if not any(p == c or p.startswith(c + os.sep) or c.startswith(p + os.sep)
                           for c in self.covered if c != f):
                    out.append(p)
        return out

    def _measure(self, item):
        fresh = dict(measured=True, running=running_apps(item.get("apps")))
        try:
            m = item.get("measure")
            if m and m.startswith("docker:"):
                fresh.update(measure_docker(m.split(":", 1)[1]))
                fresh["exists"] = fresh.get("size") is not None
            elif m in SPECIAL:
                fresh.update(SPECIAL[m]())
                fresh["exists"] = True
            else:
                paths = item["real_paths"]
                present = [p for p in paths if os.path.lexists(p)]
                fresh["exists"] = bool(present)
                fresh["size"] = du(present) if present else 0
                fresh["last_changed"] = last_changed(present)
                if item["action"] == "empty" and not item.get("measure") and present:
                    n = sum(len(os.listdir(p)) for p in present if os.path.isdir(p))
                    fresh["note"] = f"{n} item{'s' if n != 1 else ''}"
        except Exception as e:  # keep scanning; show the error on the row
            fresh["error"] = str(e)
        return fresh

    def _scan_downloads(self):
        folder = os.path.join(HOME, "Downloads")
        for path, size in sorted(du_children(folder).items(), key=lambda kv: -kv[1]):
            if size < DOWNLOADS_MIN:
                continue
            name = os.path.basename(path)
            is_dir = os.path.isdir(path)
            what = "A folder" if is_dir else "A file"
            what += " sitting in Downloads. Nothing here knows what it is, so open it before deciding."
            used_by = "You"
            if is_dir and os.path.isdir(os.path.join(path, ".git")):
                code, out, _ = run(["git", "-C", path, "status", "--porcelain"], timeout=30)
                changes = len(out.splitlines()) if code == 0 else None
                what = "A git repository in Downloads."
                if changes:
                    what += f" It has {changes} uncommitted change{'s' if changes != 1 else ''}."
                    used_by = "You (uncommitted work)"
                elif changes == 0:
                    what += " Working tree is clean, so everything in it is also on its remote if you pushed."
                    used_by = "You (git, clean)"
            self._put(dict(
                id="dl:" + name, name=name, group="Downloads", what=what, used_by=used_by,
                after="It is deleted, not moved to the Trash.", level="careful",
                action="remove", how="", paths=[shorten(path)], real_paths=[path], cmd=None,
                apps=[], quit=[], measure=None, exists=True, size=size, reclaimable=None,
                note="folder" if is_dir else "file", last_changed=last_changed([path]),
                running=[], measured=True, error=None,
            ))

    def _scan_large(self):
        """Big folders we do not have an entry for. Shown, never deleted from here."""
        roots = [os.path.join(HOME, "Library", "Application Support"),
                 os.path.join(HOME, "Library", "Containers"),
                 os.path.join(HOME, "Library", "Group Containers"),
                 os.path.join(HOME, "Library"),
                 HOME]
        seen = set()
        for root in roots:
            for path, size in sorted(du_children(root).items(), key=lambda kv: -kv[1]):
                if size < LARGE_MIN or path in seen:
                    continue
                name = os.path.basename(path)
                if root == HOME and name in HOME_SKIP:
                    continue
                if path in roots:
                    continue
                if any(path == c or path.startswith(c + os.sep) for c in self.covered):
                    continue
                seen.add(path)
                # folders that hold catalog entries: only count what is left over,
                # otherwise the same gigabytes show up twice on the page
                inside = [c for c in self.covered if c.startswith(path + os.sep)]
                # and folders already reported as large from a shallower root
                inside += [q for q in seen if q != path and q.startswith(path + os.sep)]
                if inside:
                    size -= du(inside)
                    if size < LARGE_MIN:
                        continue
                what = KNOWN.get(shorten(path), "Not in the catalog, so the tool does not know what is inside.")
                if inside:
                    names = [shorten(c) for c in inside]
                    what = ("Contains " + ", ".join(names[:3]) + (", ..." if len(names) > 3 else "")
                            + ", which are listed separately. The size shown is what is left over, and nothing here knows what that is.")
                self._put(dict(
                    id="big:" + shorten(path), name=shorten(path), group="Large folders",
                    what=what, used_by="Unknown", after="", level="info", action="none",
                    how="Look at it in Finder before removing anything.",
                    paths=[shorten(path)], real_paths=[path], cmd=None, apps=[], quit=[],
                    measure=None, exists=True, size=size, reclaimable=None, note="",
                    last_changed=last_changed([path]), running=[], measured=True, error=None,
                ))
