# Shift Notes: Offline Caregiver Handoff Board

A fictional, local-first care handoff demo for two caregivers on separate devices.
Caregivers sign in with a **demo-only identity**, record medications, symptoms,
appointments, tasks, and concerns, then submit and acknowledge shift handoffs.
The latest handoff freezes a summary of completed, changed, and still-open items.

**Prototype only. No real authentication, no real patient information, and no medical advice.**

## Run the app

Python 3.10+ is required. Install dependencies while connected to the internet;
the running app uses no CDN, external API, or internet connection.

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# Windows Git Bash: source .venv/Scripts/activate
# Linux / macOS: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Visit `http://127.0.0.1:8000`. Choose Maria or Alex (or type a display name).
Any password, including a blank one, is accepted. The first launch automatically
creates `data/handoff.db` with three fictional starting items.

For a two-device demo on a **trusted local network**, use
`--host 0.0.0.0` and open `http://<computer-ip>:8000` from the second device.
The Pi 4 Model B can run the same command once Python and dependencies are
installed. The Pi setup guide below is for the **64-bit Raspberry Pi OS desktop
based on Debian Trixie**; it has not yet been run on the Pi.

## Offline Pi hotspot setup (operator guide)

Use a local display and keyboard for the first setup. Creating a hotspot on
the Pi's Wi-Fi adapter disconnects it from its current Wi-Fi network and can
end a remote SSH session. Prepare the app **while still online**; copy this
repository to a directory you own (the commands below use
`~/hw_hackathon_oct_2026`), then run on the Pi:

```bash
sudo apt update
sudo apt install python3-venv
cd "$HOME/hw_hackathon_oct_2026"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Confirm the OS, the Wi-Fi interface name, and NetworkManager before changing
connections. Set the correct Wi-Fi country in the Pi desktop's Control Centre
if it has not been configured. Record the name of the current Wi-Fi connection
from `nmcli connection show`, so you can switch back later.

```bash
cat /etc/os-release
nmcli device status
nmcli connection show
```

On Trixie desktop, NetworkManager manages Wi-Fi. Run the following **locally
on the Pi**, replacing `wlan0` if `nmcli device status` shows another Wi-Fi
interface. Do this once: starting the `hotspot` command again can create a new
profile or password. With no `password` argument, NetworkManager generates a
password rather than placing one in your shell history or this repository.

```bash
sudo nmcli device wifi hotspot ifname wlan0 con-name shift-notes ssid ShiftNotes
sudo nmcli connection modify shift-notes connection.autoconnect yes connection.autoconnect-priority 100
nmcli -g 802-11-wireless-security.key-mgmt connection show shift-notes
sudo nmcli device wifi show-password ifname wlan0
nmcli -g IP4.ADDRESS device show wlan0
```

Check that `key-mgmt` is `wpa-psk` or `sae`; do **not** use an open/WEP hotspot.
The `show-password` command displays the Wi-Fi secret (and may display a join
QR code) on your local screen. Keep that output private. Note the `IP4.ADDRESS`
value, remove its `/prefix`, and open `http://<hotspot-ip>:8000` from devices
joined to `ShiftNotes`. Use the IP address, not `handoff.local`; mDNS is not
configured. If the hotspot is not active after a reboot, use
`sudo nmcli connection up shift-notes` on the Pi and inspect its status.

To test the app before enabling boot startup, run this on the Pi in another
terminal:

```bash
cd "$HOME/hw_hackathon_oct_2026"
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

For optional app autostart, create `/etc/systemd/system/shift-notes.service`
on the Pi with the following contents. Replace every `YOUR_PI_USERNAME` with
the account that owns the repository; adjust the path if you placed it
elsewhere. This runs the web app as that account, not root, and does **not**
configure the hotspot itself.

```ini
[Unit]
Description=Shift Notes fictional care board
After=network.target

[Service]
User=YOUR_PI_USERNAME
WorkingDirectory=/home/YOUR_PI_USERNAME/hw_hackathon_oct_2026
ExecStart=/home/YOUR_PI_USERNAME/hw_hackathon_oct_2026/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

After saving the service file, stop any manually started copy of the app with
Ctrl-C so both processes do not try to use port 8000. Then run on the Pi:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now shift-notes.service
systemctl status shift-notes.service
```

For an offline rehearsal, unplug any Ethernet uplink, confirm the Pi is not
connected to another internet source, join two devices to the hotspot, then
walk through the shift below. If a phone rejects Wi-Fi without internet,
keep it on `ShiftNotes` and disable cellular data for that check. If the app
does not load, check `nmcli device status`, the Pi's `IP4.ADDRESS`, and
`journalctl -u shift-notes.service -b --no-pager`. If switching networks
strands you, use the Pi's local desktop/terminal to disable hotspot autoconnect,
take down the hotspot, and reconnect the previously noted Wi-Fi profile:

```bash
sudo nmcli connection modify shift-notes connection.autoconnect no
sudo nmcli connection down shift-notes
sudo nmcli connection up "YOUR_PREVIOUS_PROFILE_NAME"
```

This is an **HTTP demo with no real authentication**. Use fictional data only
and a trusted, password-protected local hotspot. Without an RTC, the Pi's
clock may be wrong after an offline cold boot; correct timestamps cannot be
guaranteed yet. RTC installation, GPIO controls, and Pi hardware validation
are separate milestones.

## Walk through a shift

1. Sign in as Maria. Mark "8:00 PM medication" done and enter its time.
2. Update Dizziness from mild to moderate. Add or update an open concern.
3. Submit a handoff note. The snapshot includes changes since the previous
   acknowledged handoff (or since the demo's starting state).
4. On another browser/device, sign in as Alex. The board updates automatically
   via Server-Sent Events; a five-second refresh is the fallback.
5. Alex acknowledges the handoff. Maria can then submit the next one.

Changes made *after* a handoff is submitted are not added to its frozen snapshot.
They are included in the next handoff after acknowledgement.

## Reset and test

Only use this on fictional demo data: the reset **replaces all board entries and
handoffs** in the selected database.

```bash
python -m app.seed --reset
python -m pip install -r requirements-dev.txt
python -m pytest
```

If Node is installed on your development machine, also run
`node --test tests/board.test.js` for the frontend refresh tests; Node is not
needed to run the app on the Pi.

Set `HANDOFF_DB` to change the database path; the reset command also accepts
`--db PATH`. The SQLite file and its WAL files are ignored by Git.

## Limits

Identity is stored in a signed demo cookie, not verified against a real user.
Do not expose this app to the public internet or store PHI. Timestamps use the
host's clock; reliable offline time on a Pi will need an RTC in the hardware
phase. The original project brief is in
`Offline Caregiver Handoff Board.html`.
