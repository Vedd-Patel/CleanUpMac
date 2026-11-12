# Tidy

A small local web page that shows what is taking up space on your Mac: where it
lives, which app writes it, when it last changed, and whether it is safe to
delete. You tick the items you want gone and confirm. Nothing is ever deleted
without that confirmation.

It started as a list of shell commands I kept rerunning every few weeks
whenever the disk filled up (Docker build cache, simulators, DerivedData,
WhatsApp media, ...). This is that list with a UI on top.

- Runs entirely on your machine. No accounts, no network calls, no analytics.
- Needs only the `python3` that ships with the Xcode command line tools.
  No `pip install`, no `npm install`.
- Read-only until you press **Delete** and confirm.

---

## Starting it

From the folder:

```sh
./tidy.sh
```

Your browser opens at `http://127.0.0.1:8765/` and a scan starts right away.
Leave the terminal open while you use the page. Press `ctrl-c` there to stop.

If you prefer Finder, double-click **Tidy.command**. The first time, macOS
may block it because it was not downloaded from the App Store; right-click it,
choose **Open**, then **Open** again. After that a double-click is enough.

Options:

| Flag | What it does |
| --- | --- |
| `--port 9000` | Use another port. If the one you pick is busy, the next free one is used. |
| `--no-browser` | Don't open a browser tab, just print the address. |
| `-v` | Log every request to the terminal. |

### Running it from anywhere

Add an alias to `~/.zshrc`:

```sh
alias tidy="$HOME/Desktop/CleanUpMac/tidy.sh"
```

Open a new terminal and type `tidy`.

### Requirements

- macOS (it uses `du`, `pgrep`, `osascript`, `open`, `tmutil`).
- Python 3.8 or newer. Check with `python3 --version`. If it is missing,
  `xcode-select --install` gives you one.
- Optional: Docker Desktop running, if you want Docker numbers. Without it
  the Docker rows say "Docker is not running" and the rest works normally.

---

## Using the page

### The top bar

Shows free space, total size, and how much can be freed without losing
anything (the sum of everything marked **Safe**). When you tick items, the
red part of the bar shows how much of the used space they account for.

The scan takes from a few seconds to a couple of minutes depending on how
big `~/Library` is. Rows fill in as they are measured; you can start reading
before it is done.

### The three labels

| Label | Meaning | Examples |
| --- | --- | --- |
| **Safe** | Caches and build output. The owning tool rebuilds them when needed. You lose time on the next build, never data. | DerivedData, Docker build cache, npm cache, Chrome cache |
| **Check first** | Real data. Once deleted it is gone, and it does **not** go to the Trash. | WhatsApp media, Downloads folders, Steam games, Xcode archives, the Trash itself |
| **Look only** | Shown so you know where the space went, but there is no Delete button. Either it needs sudo, or the owning app should do it. The details tell you how. | Simulator runtimes, Docker's virtual disk, Time Machine snapshots, unknown large folders |

Use the filter buttons to show one label at a time. Items under 10 MB are
hidden by default; untick the box on the right to see everything.

### Row details

Click a row (anywhere except the checkbox) to expand it:

- **What it is**: what the folder actually holds.
- **Used by**: the app or tool that writes it.
- **Where**: the exact path or paths.
- **Last changed**: newest modification time of the folder and its direct
  contents. Something last changed eight months ago is usually a leftover.
- **After deleting**: what you will notice afterwards.
- **What Delete does**: either "empties the folder", "deletes the folder",
  or the exact command that will run (for example `docker builder prune -af`).
- **Before deleting**: apps that will be asked to quit first.

**Show in Finder** opens the folder so you can look inside before deciding.

If a row says something like *"WhatsApp is open"*, that app is running.
For apps where it matters (WhatsApp, Slack, Discord, Steam), Tidy asks the app
to quit before deleting. For the rest it is just a heads-up.

### Deleting

1. Tick the items you want removed. A bar appears at the bottom with the count
   and total size.
2. Press **Delete…**. A dialog lists every item and its size.
3. If any item is **Check first**, the Delete button stays disabled until you
   tick *"I understand, delete them anyway"*.
4. Press **Delete**. Items are cleaned one after another and each line shows
   how much it freed, or why it failed. One failure does not stop the others.

You can also delete a single item from its expanded row with **Delete this…**.
It goes through the same dialog.

Press **Rescan** at any point to measure everything again.

---

## What it looks at

Grouped the same way as on the page.

**Docker**: build cache, unused images, dangling volumes (via `docker` itself),
old installer files, and the size of the virtual disk.

**Xcode and iOS**: DerivedData, simulator data (erased with
`xcrun simctl erase all`, the simulators remain), simulator caches,
DeviceSupport, archives, SwiftUI previews, SwiftPM cache, installed runtimes.

**Build tools**: Go build and module caches, Gradle, npm, Yarn, pnpm, pip,
Cargo, Homebrew, CocoaPods, Android emulators, `~/.cache`, Rust toolchains.

**Editors and browsers**: VS Code updater downloads and caches, Chrome and
Safari caches.

**Apps**: WhatsApp media (the chat database is left alone), Slack, Discord,
Spotify, Steam, app logs, the Trash.

**System**: everything else in `~/Library/Caches`, and local Time Machine
snapshots.

**Downloads**: anything in `~/Downloads` over 300 MB. If it is a git
repository, the row says whether it has uncommitted changes.

**Large folders**: any folder over 1 GB in your home folder or `~/Library`
that is not covered above. If it contains items that are covered, only the
leftover size is shown, so nothing is counted twice.

---

## How deleting is kept safe

All deletion goes through one file, `tidy/actions.py`, and every path is
checked before anything is removed:

- It must resolve (after following symlinks) to somewhere inside your home
  folder.
- It must not be your home folder or `~/Desktop`, `~/Documents`,
  `~/Downloads`, `~/Pictures`, `~/Movies`, `~/Music`, `~/Library`,
  `~/Applications` or `~/Public` themselves. Things *inside* them are fine,
  the folders themselves never.
- It must be a path the scan reported for that item. The page sends item ids,
  never paths, so the browser cannot ask for an arbitrary folder to be deleted.
- Symlinks are removed as links. Their targets are never followed.
- Commands like `go clean -cache` run with a fixed argument list, never
  through a shell built from input.

The server only listens on `127.0.0.1`, so other machines on your network
cannot reach it. Every request that changes something must carry a random
token that is generated each time Tidy starts and embedded in the page. Requests
from other websites are rejected too, so a page open in another tab cannot
trigger a delete.

Nothing needs `sudo`, and Tidy never asks for it.

---

## Things that come back

Most of this regrows, and that is normal. Builds refill DerivedData and the
Gradle, Go and Docker caches. Using the simulator fills simulator data again.
WhatsApp downloads media as people send it. Expect to run Tidy every few weeks
if you build a lot.

The Docker build cache is usually the fastest-growing item. If it keeps getting
large, you can also limit it in Docker Desktop under Settings > Docker Engine:

```json
"builder": { "gc": { "enabled": true, "defaultKeepStorage": "10GB" } }
```

---

## Adding your own items

Everything Tidy knows about lives in `tidy/catalog.py` as a list of plain
dictionaries. To add something, copy an entry and change it:

```python
dict(
    id="unity-cache",                      # unique, used by the page
    name="Unity cache",                    # shown on the row
    group="Apps",                          # one of the groups in GROUP_ORDER
    paths=["~/Library/Unity/cache"],       # one or more paths
    what="Downloaded packages and asset cache for Unity projects.",
    used_by="Unity Hub, Unity Editor",
    after="Projects re-import assets on next open.",
    level="safe",                          # safe | careful | info
    action=("empty",),                     # see below
    apps=["Unity"],                        # process names to flag as open (optional)
    quit=["Unity"],                        # apps to quit before deleting (optional)
),
```

Actions:

| Action | Effect |
| --- | --- |
| `("empty",)` | Delete everything inside the folder, keep the folder. |
| `("remove",)` | Delete the folder itself. |
| `("run", ["tool", "arg"])` | Run that command instead of deleting files. Use this when the tool has its own clean command. |
| `("none",)` | No Delete button. Put instructions in `how="..."`. |

Restart Tidy and the new row appears. Text in backticks in `what`, `after` and
`how` is shown as code on the page.

---

## Files

```
tidy.sh            starts everything
Tidy.command       same, for double-clicking in Finder
tidy/
  catalog.py       the list of things Tidy knows about
  scan.py          measures them, finds big Downloads and unknown folders
  actions.py       the only code that deletes, with the path checks
  server.py        the local HTTP server and its four API routes
web/
  index.html       the page
  style.css
  app.js
```

---

## Troubleshooting

**The browser didn't open.** Copy the address printed in the terminal into
your browser.

**"Lost contact with the server".** The terminal running `tidy.sh` was closed
or stopped. Start it again and reload the page.

**Docker rows show "Docker is not running".** Open Docker Desktop, wait for it
to start, then press Rescan.

**Some sizes look smaller than Finder says.** Finder counts some things
differently, for example iCloud files and APFS clones. Tidy uses `du`, which
counts actual disk blocks.

**A folder could not be fully emptied.** Usually an app was holding files open,
or macOS protects part of it. Quit the app and try again. Leftovers are small.

**Free space did not go up as much as expected.** macOS sometimes holds on to
freed space in local snapshots for a while. Check the Time Machine snapshots
row, and give it a few minutes.

**Port already in use.** Tidy picks the next free port automatically. Use
`--port` to choose one yourself.
