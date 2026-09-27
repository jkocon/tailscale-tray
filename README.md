# tailscale-tray

Prosta ikona Tailscale w zasobniku w stylu klienta na Windows, lżejsza niż KTailctl.
Menu otwiera się lewym lub prawym kliknięciem:

- status: klik łączy albo rozłącza (po wylogowaniu otwiera logowanie w przeglądarce),
- konta: lista kont Tailscale do przełączania (`tailscale switch`), „Add another account…”
  (`tailscale login` w przeglądarce), „Admin console” i „Log out”,
- to urządzenie oraz lista urządzeń w sieci (klik kopiuje IP),
- exit nody do wyboru i „Allow local network access”,
- Settings: Allow incoming connections (shields up), Use Tailscale DNS settings (accept-dns),
  Use Tailscale subnets (accept-routes), Run unattended (tailscaled startuje z systemem,
  przełączane przez pkexec) i Reset to defaults,
- About, Exit.

Ikona w zasobniku: jasne „T” = połączony, same szare kropki = rozłączony, zielona strzałka = ruch
idzie przez exit node.

Rozmawia z tailscaled przez LocalAPI (`/var/run/tailscale/tailscaled.sock`). Zmiana ustawień bez
roota wymaga operatora Tailscale, więc `install.sh` ustawia `tailscale set --operator=<użytkownik 1000>`.

Instalacja: `sudo -A ./install.sh` (do `/usr/local/lib/tailscale-tray`, autostart w `/etc/xdg/autostart`).
Na X13 robi to automatycznie `target/apply.sh`.
