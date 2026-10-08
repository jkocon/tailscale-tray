#!/usr/bin/env bash
# Instaluje/aktualizuje tailscale-tray w systemie. Uruchom jako root (sudo -A ./install.sh).
# Na X13 robi to automatycznie target/apply.sh z cachyos_sync po każdym nowym commicie w tym repo.
# Działający tray podmienia się dopiero po ponownym zalogowaniu (albo: pkill -x tailscale-tray; /usr/local/lib/tailscale-tray/tailscale-tray &).
# Kod wyjścia 10 = brak Tailscale, nic nie zainstalowano.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
LIB=/usr/local/lib/tailscale-tray
[[ $EUID -eq 0 ]] || { echo "Uruchom przez sudo"; exit 1; }

if ! command -v tailscale >/dev/null; then
    echo "tailscale-tray: brak tailscale - pomijam"
    exit 10
fi

pacman -S --needed --asdeps --noconfirm kdialog wl-clipboard libnotify xdg-utils

# Binarka w Rust (od 2.0; wcześniej tailscale_tray.py z GTK/AppIndicator), budowana jako zwykły użytkownik.
BIN=$("$SRC/build.sh" "$SRC")

install -Dm755 "$BIN" "$LIB/tailscale-tray"
rm -rf "$LIB/tailscale_tray.py" "$LIB/__pycache__"  # wersja w Pythonie
rm -rf "$LIB/icons"  # bez ikon o starych nazwach
install -Dm644 -t "$LIB/icons" "$SRC"/icons/*.svg
install -Dm644 -t /etc/xdg/autostart "$SRC/tailscale-tray.desktop"
install -Dm644 "$SRC/tailscale-tray-launcher.desktop" /usr/local/share/applications/tailscale-tray.desktop
install -Dm644 "$SRC/tailscale-tray.svg" /usr/local/share/icons/hicolor/scalable/apps/tailscale-tray.svg
gtk-update-icon-cache -qtf /usr/local/share/icons/hicolor 2>/dev/null || true

# Zmiana ustawień z traya bez roota wymaga operatora Tailscale (pierwszy zwykły użytkownik).
user=$(getent passwd 1000 | cut -d: -f1)
if [[ -n $user ]] && systemctl is-active --quiet tailscaled; then
    tailscale set --operator="$user" || echo "tailscale-tray: nie udało się ustawić operatora"
fi
