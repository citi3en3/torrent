# YY Torrent

A standalone Windows BitTorrent client: PySide6 on top of libtorrent-rasterbar,
packaged as a single installed program that handles `.torrent` files and
`magnet:` links.

It uses **the same download engine as qBittorrent** — qBittorrent is itself a Qt
GUI wrapped around libtorrent — so peer discovery, DHT, encryption and piece
selection are all handled by the same battle-tested library. What differs is the
interface on top.

## Features

- Live torrent list: progress, speed, seeds/peers, ETA, ratio, sortable and filterable
- **Add dialog** with a file tree — deselect anything you don't want and it is
  never downloaded (libtorrent priority `0`, applied at add time)
- One save folder, pre-filled with the last one you used and changeable per torrent
- Magnet support with a cancellable "fetching metadata" wait
- Single instance: selecting ten `.torrent` files in Explorer opens one window
- System tray with notifications, minimise/close to tray
- **qBittorrent import** — brings existing torrents across with piece-level
  progress intact; nothing re-downloads or re-hashes

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

.\scripts\build_exe.ps1        # produces dist\YYTorrent\
.\scripts\install.ps1          # installs + shortcuts + associations
```

`install.ps1` is per-user — it writes to `%LOCALAPPDATA%\Programs\YYTorrent`
and `HKEY_CURRENT_USER`, so it never needs administrator rights. Add
`-StartWithWindows` to start minimised to the tray at login.

To remove: `.\scripts\uninstall.ps1` (add `-PurgeData` to drop torrent state
too). Downloaded files are never touched.

## About file associations

`magnet:` links are claimed outright and work immediately.

`.torrent` is different, and worth being clear about: **Windows 10 and 11
cryptographically sign the "default app" choice**, and that signature cannot be
produced by user code. No program can make itself the default handler for an
extension — not this one, not qBittorrent, not anything. What the installer does
achieve is registering YY Torrent properly, so it appears under *Open with* and
in *Settings → Default apps*.

To finish the association, once:

> Right-click any `.torrent` → **Open with** → **Choose another app** →
> YY Torrent → tick **Always use this app**

The app shows a banner with a shortcut to that Settings page until it owns the
association.

## Migrating from qBittorrent

On first run, if a qBittorrent library is found the import dialog lists what it
found and imports on confirmation.

qBittorrent's `.fastresume` files *are* libtorrent resume data (plus some
private `qBt-*` keys that libtorrent ignores), so progress, save paths and file
selections all carry over — nothing re-downloads and nothing re-hashes.
qBittorrent's own files are only ever read, never modified, so the import is
safe to run before uninstalling it, and safe to repeat.

**Import and verify before uninstalling qBittorrent.** Afterwards, its stale
ProgID makes Windows re-prompt for a `.torrent` default, which is the tidiest
moment to select YY Torrent permanently.

## Layout

```text
src/torrentapp/   (internal package name; the app is "YY Torrent")
  engine/        libtorrent session, alerts, resume data, qBittorrent import
  ui/            widgets, table model, dialogs, theme
  bridge.py      Qt adapter over the engine
  ipc.py         single-instance argument hand-off
  shell_integration.py   registry associations
scripts/         build, install, uninstall, icon generation, headless smoke test
tests/           pytest suite
```

**`engine/` never imports Qt.** That keeps the download core runnable headless —
from `scripts/smoke_engine.py` and from pytest — and keeps alert handling
testable in isolation from the widgets that display it. The Qt adapter lives in
`bridge.py`, which is the one place timers turn engine events into signals.

## Development

```powershell
.\.venv\Scripts\python.exe run.py            # run from source
.\.venv\Scripts\python.exe -m pytest         # 51 tests
.\.venv\Scripts\python.exe scripts\make_icon.py
```

Registry writes can be previewed without making them:

```powershell
.\.venv\Scripts\python.exe run.py --register-shell --dry-run
```

### Verifying the engine end to end

The single most important property is that a restart resumes rather than
re-hashing. `scripts/smoke_engine.py` proves it without any GUI:

```powershell
# download to ~3%, then stop cleanly
.\.venv\Scripts\python.exe scripts\smoke_engine.py <magnet-or-torrent> `
    --save-path D:\tmp\dl --data-dir D:\tmp\state --stop-at 0.03

# restart: must report ~3%, not 0%, and must not sit in "checking"
.\.venv\Scripts\python.exe scripts\smoke_engine.py `
    --save-path D:\tmp\dl --data-dir D:\tmp\state --seconds 30
```

## Configuration

`%APPDATA%\YYTorrent\`

| File | Purpose |
| --- | --- |
| `settings.json` | save folder, ports, limits, behaviour |
| `resume/` | fast-resume data, one file per torrent |
| `torrents/` | `.torrent` copies, so restarts never refetch metadata |
| `logs/` | rotating log |

`download_root` is the one save folder. Set `remember_last_save_path` to `false`
if you would rather the add dialog always start there instead of at whatever you
picked last.
