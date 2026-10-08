# tailscale-tray

A small **Tailscale** icon for the Linux system tray, modelled on the official Windows client and
much lighter than a full GUI such as KTailctl. Connect, switch accounts, pick an exit node or copy
a device's IP straight from the tray menu.

> **Polish documentation:** [README.pl.md](README.pl.md)

Sibling projects with the same look and behaviour:
[twingate-tray](https://github.com/jkocon/twingate-tray) and
[netbird-tray](https://github.com/jkocon/netbird-tray).

---

## Features

The menu opens on a left **or** right click:

| Menu entry | What it does |
|---|---|
| **Status** | *Connected* / *Disconnected – click to connect*. One click connects or disconnects. When logged out it opens the login page in the browser; when the machine waits for admin approval it opens the admin console. |
| **Account** (submenu) | All Tailscale accounts (profiles) on this machine – click one to switch; an active connection moves to the new account. *Add another account…* (browser login), *Admin console*, *Manage subnet routes…* (admin console filtered to machines that advertise routes – route approval deliberately stays in the browser, no API keys on the laptop), *Log out*. |
| **This device** | Name and Tailscale IP – click to copy the IP. |
| **Network devices** | Every peer with online/offline mark, IP and OS – click to copy its IP. |
| **Exit nodes** | Pick an exit node (offline ones are greyed out) or *None*; *Allow local network access*. |
| **Settings** | *Allow incoming connections* (shields up), *Use Tailscale DNS settings* (accept-dns), *Use Tailscale subnets* (accept-routes), *Run unattended* (tailscaled starts with the system), *Reset to defaults*. |
| **About**, **Exit** | Version information; quit the tray. |

### Tray icon

| Icon | State |
|---|---|
| bright "T" | connected |
| grey dots only | disconnected |
| green arrow | connected, traffic goes through an exit node |

---

## Requirements

- Linux desktop with a **StatusNotifierItem** tray: KDE Plasma works out of the box; GNOME needs the
  *AppIndicator and KStatusNotifierItem Support* extension.
- **Tailscale** (`tailscaled` running).
- Runtime helpers: `kdialog` (dialogs), `notify-send` (libnotify), `xdg-open` (xdg-utils),
  `wl-copy` (wl-clipboard) – or `xclip`/`xsel` on X11 – for copying IPs.
- To build: Rust 1.85+ (`cargo`).

`install.sh` targets Arch-based systems (CachyOS, Arch, EndeavourOS): it installs the runtime
helpers with `pacman`. On other distributions build with `cargo build --release` and copy the
files listed below by hand.

---

## Installation

```bash
git clone https://github.com/jkocon/tailscale-tray.git
cd tailscale-tray
sudo ./install.sh
```

`install.sh` (run as root; exit code 10 = Tailscale is not installed, nothing done):

1. installs the runtime helpers with `pacman`,
2. builds the binary **as your normal user** with `build.sh` (cargo never runs as root),
3. installs `/usr/local/lib/tailscale-tray/tailscale-tray` and its state icons,
4. adds autostart (`/etc/xdg/autostart/tailscale-tray.desktop`), a menu entry and the app icon,
5. makes the first regular user (UID 1000) the **Tailscale operator**
   (`tailscale set --operator=<user>`), so the tray can change settings without root.

The tray starts at the next login, or run `/usr/local/lib/tailscale-tray/tailscale-tray` now.
Only one instance runs per session; starting it again just shows a notification.

### Command line

```
tailscale-tray            start the tray icon
tailscale-tray --dump     query tailscaled once and print the menu as text (no icon, for testing)
tailscale-tray --version
```

---

## How it works

- Talks to `tailscaled` through its **LocalAPI** (HTTP over `/var/run/tailscale/tailscaled.sock`),
  polling every 3 seconds; settings are changed with `PATCH /localapi/v0/prefs`.
- Commands that need more rights than an operator has (switching to a profile without an
  operator, logging out, adding an account) go through **pkexec**, and the operator is restored
  in the new profile right away – a freshly added Tailscale profile has no operator and would
  otherwise lose access halfway through the login.
- *Run unattended* is `systemctl enable/disable tailscaled` via pkexec.
- Icon and menu use **ksni** (StatusNotifierItem + DBusMenu over D-Bus, no GTK). State icons are
  passed as absolute paths to copies with a content hash in the file name: Plasma ignores a flat
  `IconThemePath` and caches icons by path, so a changed icon would otherwise not show up.

---

## Building and testing

```bash
cargo build --release
cargo test
cargo run -- --dump        # menu for the current tailscaled state, without a tray icon
```

### Layout

```
src/main.rs        tray state, menu, actions, polling
src/localapi.rs    LocalAPI client (HTTP over the unix socket)
src/common/        shared with twingate-tray and netbird-tray: commands with timeouts, kdialog,
                   notifications, clipboard, icons, single-instance lock, poller, ksni menu helpers
icons/             state icons (on, disconnected, exit)
build.sh           builds as a regular user, used by install.sh
install.sh         system installation (Arch-based)
```

`src/common/` is the same copy in all three tray repositories – apply a fix there to all of them.

---

## License

MIT – see [LICENSE](LICENSE).
