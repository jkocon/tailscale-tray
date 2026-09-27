#!/usr/bin/env python3
"""Prosta ikona Tailscale w zasobniku w stylu klienta na Windows (zamiast rozbudowanego KTailctl).

Menu (lewy lub prawy klik): status (klik = połącz/rozłącz), konta (przełączanie, dodawanie), to urządzenie,
lista urządzeń w sieci, exit nody, ustawienia, About, Exit.

Rozmawia z tailscaled przez jego LocalAPI (unix socket). Zmiana ustawień bez roota wymaga,
żeby użytkownik był operatorem Tailscale: sudo tailscale set --operator=$USER (robi to install.sh).
AppIndicator zamiast QSystemTrayIcon, bo w Plasmie tylko wtedy lewy klik otwiera menu.
"""

from __future__ import annotations

import fcntl
import http.client
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import webbrowser
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator3  # noqa: E402
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

APP_NAME = "Tailscale Tray"
APP_VERSION = "1.0"
SOCKET = "/var/run/tailscale/tailscaled.sock"
POLL_SECONDS = 3
ADMIN_URL = "https://login.tailscale.com/admin/machines"
ICON_DIR = Path(__file__).resolve().with_name("icons")
# Pełne ścieżki, nie nazwy: Plasma ignoruje IconThemePath z płaskim katalogiem i obcina nieznaną
# nazwę do "tailscale-tray" (ikona aplikacji), więc każdy stan wyglądał tak samo.
ICON_ON, ICON_OFF, ICON_EXIT = (
    str(ICON_DIR / f"{name}.svg") for name in ("tailscale-tray-on", "tailscale-tray-disconnected", "tailscale-tray-exit")
)

# Wartości domyślne Tailscale na Linuksie (tailscale up bez flag).
DEFAULT_PREFS = {
    "ShieldsUp": False,
    "CorpDNS": True,
    "RouteAll": False,
    "ExitNodeID": "",
    "ExitNodeIP": "",
    "ExitNodeAllowLANAccess": False,
}


class LocalAPIError(Exception):
    pass


class _UnixHTTPConnection(http.client.HTTPConnection):
    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect(SOCKET)


def api(method: str, path: str, body: dict | None = None):
    try:
        conn = _UnixHTTPConnection("local-tailscaled.sock", timeout=5)
        conn.request(
            method,
            f"/localapi/v0/{path}",
            body=json.dumps(body) if body is not None else None,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
    except OSError as e:
        raise LocalAPIError(f"tailscaled nie odpowiada: {e}") from e
    if resp.status >= 400:
        raise LocalAPIError(data.decode(errors="replace").strip() or f"HTTP {resp.status}")
    return json.loads(data) if data else None


def set_prefs(**prefs) -> None:
    """PATCH prefs; każde pole wymaga flagi <Pole>Set, inaczej tailscaled je ignoruje."""
    body = dict(prefs)
    for key in prefs:
        body[f"{key}Set"] = True
    api("PATCH", "prefs", body)


def idle_once(func, *args) -> None:
    """Wywołaj func raz w wątku GTK. Samo GLib.idle_add powtarza wywołanie, dopóki funkcja zwraca
    True - z webbrowser.open (zwraca True) otwierało to przeglądarkę w pętli i położyło sesję."""
    def _call() -> bool:
        func(*args)
        return False
    GLib.idle_add(_call)


def run(*cmd: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


ACCESS_DENIED = re.compile(r"access denied|permission denied|must be root|operator", re.IGNORECASE)
USER = os.environ.get("USER") or os.getlogin()


def tailscale_admin(*args: str) -> subprocess.CompletedProcess:
    """Polecenie tailscale jako użytkownik; przy braku uprawnień (profil bez operatora) przez pkexec,
    od razu przywracając operatora w profilu, który jest aktywny po poleceniu."""
    res = run("tailscale", *args)
    if res.returncode == 0 or not ACCESS_DENIED.search(res.stdout + res.stderr):
        return res
    script = 'tailscale "$@" && tailscale set --operator="$OPERATOR"'
    return run("pkexec", "env", f"OPERATOR={USER}", "sh", "-c", script, "sh", *args, timeout=120)


def notify(title: str, body: str = "") -> None:
    subprocess.Popen(["notify-send", "-a", APP_NAME, "-i", "tailscale-tray", title, body])


def error(msg: str) -> None:
    subprocess.Popen(["kdialog", "--title", APP_NAME, "--error", msg])


def copy_to_clipboard(text: str) -> None:
    # Na Waylandzie proces bez okna nie ustawi schowka przez GTK - wl-copy tak.
    if shutil.which("wl-copy") and os.environ.get("WAYLAND_DISPLAY"):
        subprocess.run(["wl-copy", text], timeout=5)
    else:
        cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        cb.set_text(text, -1)
        cb.store()
    notify("Copied to clipboard", text)


def unattended_enabled() -> bool:
    # "Run unattended" na Linuksie = tailscaled startuje z systemem, bez logowania użytkownika.
    return run("systemctl", "is-enabled", "tailscaled").stdout.strip() == "enabled"


def profile_label(profile: dict) -> str:
    user = profile.get("UserProfile") or {}
    name, login = user.get("DisplayName", ""), user.get("LoginName", "") or profile.get("Name", "")
    label = f"{name} ({login})" if name and login and name != login else (name or login or profile.get("ID", "?"))
    tailnet = (profile.get("NetworkProfile") or {}).get("DomainName", "")
    if tailnet and tailnet != login:
        label += f"  [{tailnet}]"
    return label


def first_ip(node: dict) -> str:
    ips = node.get("TailscaleIPs") or []
    v4 = [ip for ip in ips if ":" not in ip]
    return (v4 or ips or ["-"])[0]


def node_name(node: dict) -> str:
    dns = (node.get("DNSName") or "").split(".")[0]
    return dns or node.get("HostName") or "?"


class TailscaleTray:
    def __init__(self) -> None:
        self.status: dict | None = None
        self.profiles: list[dict] = []
        self.current_profile = ""
        self.prefs: dict | None = None
        self.error: str = ""
        self.unattended = unattended_enabled()
        self.signature = None
        self.updating = False  # blokuje sygnały 'toggled' przy programowym ustawianiu pozycji
        self.login_proc: subprocess.Popen | None = None

        self.indicator = AppIndicator3.Indicator.new(
            "tailscale-tray", ICON_OFF, AppIndicator3.IndicatorCategory.SYSTEM_SERVICES
        )
        self.indicator.set_title("Tailscale")
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.refresh()
        GLib.timeout_add_seconds(POLL_SECONDS, self.refresh)

    # ---------- stan ----------

    def refresh(self) -> bool:
        try:
            self.status = api("GET", "status")
            self.prefs = api("GET", "prefs")
            self.error = ""
        except LocalAPIError as e:
            self.status, self.prefs, self.error = None, None, str(e)
        try:
            # Osobno: bez operatora w bieżącym profilu tailscaled może odmówić listy kont,
            # a reszta menu (i przełączenie z powrotem) ma nadal działać.
            self.profiles = api("GET", "profiles/") or []
            self.current_profile = (api("GET", "profiles/current") or {}).get("ID", "")
        except LocalAPIError:
            self.profiles, self.current_profile = [], ""
        sig = self.make_signature()
        if sig != self.signature:
            self.signature = sig
            self.update_icon()
            self.indicator.set_menu(self.build_menu())
        return True

    def make_signature(self):
        s, p = self.status or {}, self.prefs or {}
        peers = tuple(
            sorted(
                (node_name(n), first_ip(n), n.get("Online"), n.get("ExitNodeOption"), n.get("ExitNode"), n.get("OS"))
                for n in (s.get("Peer") or {}).values()
            )
        )
        me = s.get("Self") or {}
        keys = ("WantRunning", "ShieldsUp", "CorpDNS", "RouteAll", "ExitNodeID", "ExitNodeAllowLANAccess")
        return (
            self.error,
            s.get("BackendState"),
            node_name(me) if me else "",
            first_ip(me) if me else "",
            self.current_user(),
            peers,
            tuple(p.get(k) for k in keys),
            tuple((pr.get("ID"), profile_label(pr)) for pr in self.profiles),
            self.current_profile,
            self.unattended,
        )

    @property
    def backend(self) -> str:
        return (self.status or {}).get("BackendState", "NoState")

    @property
    def connected(self) -> bool:
        return self.backend == "Running"

    def current_user(self) -> str:
        s = self.status or {}
        uid = (s.get("Self") or {}).get("UserID")
        user = (s.get("User") or {}).get(str(uid)) or {}
        name, login = user.get("DisplayName", ""), user.get("LoginName", "")
        if name and login and name != login:
            return f"{name} ({login})"
        return name or login

    def exit_node(self) -> dict | None:
        for peer in ((self.status or {}).get("Peer") or {}).values():
            if peer.get("ExitNode"):
                return peer
        return None

    def update_icon(self) -> None:
        if self.connected and self.exit_node():
            icon, desc = ICON_EXIT, "Tailscale: connected (exit node)"
        elif self.connected:
            icon, desc = ICON_ON, "Tailscale: connected"
        else:
            icon, desc = ICON_OFF, "Tailscale: disconnected"
        self.indicator.set_icon_full(icon, desc)

    # ---------- menu ----------

    def item(self, label: str, callback=None, sensitive: bool = True) -> Gtk.MenuItem:
        it = Gtk.MenuItem(label=label)
        it.set_use_underline(False)
        if callback:
            it.connect("activate", lambda _w: callback())
        it.set_sensitive(sensitive and (callback is not None))
        return it

    def check(self, label: str, active: bool, callback, sensitive: bool = True) -> Gtk.CheckMenuItem:
        it = Gtk.CheckMenuItem(label=label)
        it.set_use_underline(False)
        it.set_active(active)
        it.set_sensitive(sensitive)
        it.connect("toggled", lambda w: None if self.updating else callback(w.get_active()))
        return it

    def build_menu(self) -> Gtk.Menu:
        self.updating = True
        m = Gtk.Menu()
        add = m.append
        state = self.backend

        # --- status ---
        if self.error:
            add(self.item("Tailscale service is not running - click to start", self.start_service))
        elif state == "Running":
            add(self.check("Connected", True, lambda _a: self.disconnect()))
        elif state == "NeedsLogin":
            add(self.item("Logged out - click to log in…", self.login))
        elif state == "Starting":
            add(self.item("Connecting…"))
        elif state == "NeedsMachineAuth":
            add(self.item("Waiting for admin approval…", lambda: webbrowser.open(ADMIN_URL)))
        else:
            add(self.check("Disconnected - click to connect", False, lambda _a: self.connect()))
        add(Gtk.SeparatorMenuItem())

        # --- użytkownik / konta ---
        add(self.accounts_menu())
        add(Gtk.SeparatorMenuItem())

        # --- to urządzenie + urządzenia w sieci ---
        me = (self.status or {}).get("Self") or {}
        if me:
            ip = first_ip(me)
            add(self.item(f"This device: {node_name(me)} ({ip})", lambda ip=ip: copy_to_clipboard(ip)))
        add(self.devices_menu())
        add(Gtk.SeparatorMenuItem())

        # --- exit nody ---
        add(self.exit_nodes_menu())
        add(Gtk.SeparatorMenuItem())

        # --- ustawienia, about ---
        add(self.settings_menu())
        add(self.item("About", self.about))
        add(Gtk.SeparatorMenuItem())
        add(self.item("Exit", Gtk.main_quit))

        m.show_all()
        self.updating = False
        return m

    def accounts_menu(self) -> Gtk.MenuItem:
        user = self.current_user()
        root = Gtk.MenuItem(label=user or "Not logged in")
        root.set_use_underline(False)
        sub = Gtk.Menu()
        group: list[Gtk.RadioMenuItem] = []
        for pr in sorted(self.profiles, key=lambda x: profile_label(x).lower()):
            pid = pr.get("ID", "")
            it = Gtk.RadioMenuItem.new_with_label(group[0].get_group() if group else None, profile_label(pr))
            it.set_use_underline(False)
            group.append(it)
            it.set_active(pid == self.current_profile)
            it.connect("toggled", lambda w, pid=pid: None if self.updating or not w.get_active() else self.switch_account(pid))
            sub.append(it)
        if self.profiles:
            sub.append(Gtk.SeparatorMenuItem())
        sub.append(self.item("Add another account…", self.login, not self.error))
        sub.append(self.item("Admin console", lambda: webbrowser.open(ADMIN_URL), bool(user)))
        sub.append(Gtk.SeparatorMenuItem())
        sub.append(self.item("Log out", self.logout, bool(user)))
        root.set_submenu(sub)
        return root

    def devices_menu(self) -> Gtk.MenuItem:
        peers = list(((self.status or {}).get("Peer") or {}).values())
        root = Gtk.MenuItem(label=f"Network devices ({sum(1 for p in peers if p.get('Online'))}/{len(peers)} online)")
        sub = Gtk.Menu()
        if not peers:
            sub.append(self.item("No devices"))
        for p in sorted(peers, key=lambda n: (not n.get("Online"), node_name(n).lower())):
            ip = first_ip(p)
            mark = "●" if p.get("Online") else "○"
            os_name = f"  [{p['OS']}]" if p.get("OS") else ""
            sub.append(self.item(f"{mark} {node_name(p)}  {ip}{os_name}", lambda ip=ip: copy_to_clipboard(ip)))
        sub.append(Gtk.SeparatorMenuItem())
        sub.append(self.item("Click a device to copy its IP"))
        root.set_submenu(sub)
        root.set_sensitive(bool(self.status))
        return root

    def exit_nodes_menu(self) -> Gtk.MenuItem:
        peers = [p for p in ((self.status or {}).get("Peer") or {}).values() if p.get("ExitNodeOption")]
        prefs = self.prefs or {}
        current_id = prefs.get("ExitNodeID") or ""
        current = self.exit_node()
        label = f"Exit node: {node_name(current)}" if current else "Exit nodes"
        root = Gtk.MenuItem(label=label)
        sub = Gtk.Menu()

        group: list[Gtk.RadioMenuItem] = []

        def radio(text: str, active: bool, node_id: str, sensitive: bool = True) -> Gtk.RadioMenuItem:
            it = Gtk.RadioMenuItem.new_with_label(group[0].get_group() if group else None, text)
            group.append(it)
            it.set_active(active)
            it.set_sensitive(sensitive)
            it.connect("toggled", lambda w: None if self.updating or not w.get_active() else self.set_exit_node(node_id))
            return it

        sub.append(radio("None", not current_id, ""))
        for p in sorted(peers, key=lambda n: node_name(n).lower()):
            online = bool(p.get("Online"))
            text = f"{node_name(p)}  {first_ip(p)}" + ("" if online else "  (offline)")
            active = p.get("ID") == current_id
            sub.append(radio(text, active, p.get("ID", ""), online or active))
        if not peers:
            sub.append(self.item("No exit nodes in this tailnet"))
        sub.append(Gtk.SeparatorMenuItem())
        sub.append(
            self.check(
                "Allow local network access",
                bool(prefs.get("ExitNodeAllowLANAccess")),
                lambda v: self.apply(ExitNodeAllowLANAccess=v),
            )
        )
        root.set_submenu(sub)
        root.set_sensitive(bool(self.prefs))
        return root

    def settings_menu(self) -> Gtk.MenuItem:
        p = self.prefs or {}
        ok = bool(self.prefs)
        root = Gtk.MenuItem(label="Settings")
        sub = Gtk.Menu()
        sub.append(self.check("Allow incoming connections", not p.get("ShieldsUp"), lambda v: self.apply(ShieldsUp=not v), ok))
        sub.append(self.check("Use Tailscale DNS settings", bool(p.get("CorpDNS")), lambda v: self.apply(CorpDNS=v), ok))
        sub.append(self.check("Use Tailscale subnets", bool(p.get("RouteAll")), lambda v: self.apply(RouteAll=v), ok))
        sub.append(self.check("Run unattended (start with the system)", self.unattended, self.set_unattended))
        sub.append(Gtk.SeparatorMenuItem())
        sub.append(self.item("Reset to defaults", self.reset_defaults, ok))
        root.set_submenu(sub)
        return root

    # ---------- akcje ----------

    def apply(self, **prefs) -> None:
        try:
            set_prefs(**prefs)
        except LocalAPIError as e:
            error(f"Could not change the setting:\n{e}")
        self.signature = None  # przebuduj menu, także po błędzie (cofa odhaczenie)
        self.refresh()

    def connect(self) -> None:
        self.apply(WantRunning=True)

    def disconnect(self) -> None:
        self.apply(WantRunning=False)

    def set_exit_node(self, node_id: str) -> None:
        if node_id:
            self.apply(ExitNodeID=node_id)
        else:
            self.apply(ExitNodeID="", ExitNodeIP="")

    def set_unattended(self, enabled: bool) -> None:
        verb = "enable" if enabled else "disable"
        res = run("pkexec", "systemctl", verb, "tailscaled")
        if res.returncode != 0 and res.returncode != 126:  # 126 = anulowane okno pkexec
            error(f"systemctl {verb} tailscaled failed:\n{res.stderr.strip()}")
        self.unattended = unattended_enabled()
        self.signature = None
        self.refresh()

    def start_service(self) -> None:
        res = run("pkexec", "systemctl", "start", "tailscaled")
        if res.returncode not in (0, 126):
            error(f"Could not start tailscaled:\n{res.stderr.strip()}")
        self.refresh()

    def reset_defaults(self) -> None:
        answer = run(
            "kdialog", "--title", APP_NAME, "--yesno",
            "Reset Tailscale settings to defaults?\n\n"
            "Allow incoming connections: on\nUse Tailscale DNS: on\nUse Tailscale subnets: off\n"
            "Exit node: none\nAllow local network access: off",
        )
        if answer.returncode == 0:
            self.apply(**DEFAULT_PREFS)

    def switch_account(self, profile_id: str) -> None:
        # Każdy profil pamięta własne WantRunning; jak w kliencie na Windows połączenie ma przejść
        # na nowe konto. 'tailscale switch' kończy się kodem 1 ("Tailscale is stopped"), gdy docelowy
        # profil był rozłączony, więc o powodzeniu decyduje aktywny profil, nie kod wyjścia.
        was_connected = self.connected
        res = None
        try:
            api("POST", f"profiles/{profile_id}")
        except LocalAPIError:
            res = tailscale_admin("switch", profile_id)  # np. bieżący profil bez operatora
        try:
            switched = (api("GET", "profiles/current") or {}).get("ID") == profile_id
        except LocalAPIError:
            switched = False
        if switched and was_connected:
            try:
                set_prefs(WantRunning=True)
            except LocalAPIError as e:
                error(f"Switched account, but could not connect:\n{e}")
        elif not switched and not (res and res.returncode == 126):  # 126 = anulowane okno pkexec
            detail = (res.stderr or res.stdout).strip() if res else "tailscaled refused the switch"
            error(f"Could not switch account:\n{detail}")
        self.signature = None
        self.refresh()

    def logout(self) -> None:
        user = self.current_user()
        answer = run("kdialog", "--title", APP_NAME, "--yesno",
                     f"Log out {user}?\n\nThis device will leave the tailnet until you log in again.")
        if answer.returncode != 0:
            return
        res = tailscale_admin("logout")
        if res.returncode not in (0, 126):
            error(f"Could not log out:\n{(res.stderr or res.stdout).strip()}")
        self.signature = None
        self.refresh()

    def login(self) -> None:
        if self.login_proc and self.login_proc.poll() is None:
            return
        # Nowe konto = nowy profil z domyślnymi ustawieniami, BEZ operatora. Zwykły użytkownik
        # traciłby w nim uprawnienia w połowie logowania (tak się stało 2026-09-28), więc logowanie
        # idzie przez pkexec z --operator, żeby nowy profil od razu był sterowalny z traya.
        self.login_proc = subprocess.Popen(
            ["pkexec", "tailscale", "login", f"--operator={USER}", "--timeout=10m"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        threading.Thread(target=self._watch_login, args=(self.login_proc,), daemon=True).start()

    def _watch_login(self, proc: subprocess.Popen) -> None:
        output = []
        opened = False
        for line in proc.stdout:
            output.append(line)
            m = re.search(r"https://login\S+", line)
            if m and not opened:
                opened = True  # przeglądarka tylko raz na logowanie
                idle_once(webbrowser.open, m.group(0))
        proc.wait()
        if proc.returncode not in (0, 126):
            idle_once(error, "Login failed:\n" + "".join(output[-10:]).strip())
        idle_once(self.force_refresh)

    def force_refresh(self) -> bool:
        self.signature = None
        self.refresh()
        return False

    def about(self) -> None:
        ver = run("tailscale", "version").stdout.splitlines()
        tail = (self.status or {}).get("CurrentTailnet") or {}
        text = (
            f"{APP_NAME} {APP_VERSION}\n"
            "A small Tailscale tray client in the style of the Windows app.\n\n"
            f"Tailscale: {ver[0] if ver else '?'}\n"
            f"Tailnet: {tail.get('Name', '-')}\n"
            f"State: {self.backend}"
        )
        subprocess.Popen(["kdialog", "--title", f"About {APP_NAME}", "--icon", "tailscale-tray", "--msgbox", text])


def single_instance_lock():
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/tailscale-tray-{os.getuid()}"
    os.makedirs(runtime, exist_ok=True)
    lock = open(os.path.join(runtime, "tailscale-tray.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    return lock


def main() -> None:
    lock = single_instance_lock()
    if lock is None:
        notify("Tailscale Tray is already running", "The icon is in the system tray.")
        return
    GLib.set_prgname("tailscale-tray")
    TailscaleTray()
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    Gtk.main()


if __name__ == "__main__":
    main()
