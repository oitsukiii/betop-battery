# betop-battery

**Read the battery level of BETOP (北通) gamepads — shown right in the Windows system tray.**

English · [简体中文](README.md)

> ### 🤖 How this project was built
>
> This project was developed with **DeepSeek V4.1 Flash** driving
> [**DeepSeek Harness (DSH)**](https://github.com/deepseek-ai/deepseek-harness)
> in a **vibe coding** workflow — protocol reverse engineering, all code,
> tests and docs included.
>
> The human author set the requirements, verified on real hardware and made
> the calls; the exploration, coding, debugging and writing were done by the
> AI inside DSH. We document this openly in the hope that it serves as a
> reference sample of AI-assisted hardware tooling development.

---

## Why

BETOP's official client **does not show an exact battery percentage** — it only maps LED colors to ranges:

| LED color | Battery |
|---|---|
| White | 76–100% |
| Green | 51–75% |
| Yellow | 26–50% |
| Red | 0–25% |

And when the controller is connected through its **2.4G dongle**, the standard Windows APIs
(XInput / GameInput) report it as a **wired device** — so every tool built on those APIs
(XInputBatteryMeter, etc.) reads nothing.

**This tool talks directly to the receiver's vendor HID interface and reads the exact percentage.**
No official client required.

```
Battery : 100%
Status  : charging
```

## Features

- ✅ **Exact battery percentage** (the official client only gives you a color range)
- ✅ **Charging state** detection
- ✅ **Tray icon** showing the number, with a detailed hover tooltip (**3 styles**)
- ✅ **Settings GUI**: live status, icon style, overlay config, live preview
- ✅ **Overlay HUD**: a framerate-style on-screen battery bar — draggable, click-through capable
- ✅ **Low-battery notification** (threshold and interval configurable)
- ✅ **CLI mode** (`once` / `--json`) for scripting
- ✅ **Data-driven**: adding a new model means adding one JSON file — no code changes
- ✅ **Independent and safe**: it never touches the vendor client, only reads device
  status over HID (the same query the official client sends). No configuration is
  written, no firmware is flashed, no game process is touched — so there is no risk of
  damaging the controller or being flagged as a cheat.
- ✅ **Built-in `probe` tooling** so you can adapt your own controller

## Quick start

### Option 1: One-click installer ⭐ best for non-developers

Download **`betop-battery-oneclick-v*.zip`** from [Releases](/releases),
unzip it and double-click **安装.bat** (install.bat).

It automatically installs Python if missing, installs the dependencies,
creates Desktop / Start Menu shortcuts and an uninstall entry, then launches
the app — **one double-click in total**.

**This path is not affected by Windows Smart App Control**, because it uses the
signed Python already on the system instead of an unsigned executable.

Uninstall: Windows *Settings → Apps → Installed apps* → 「北通手柄电量」.

### Option 2: Prebuilt binaries (no Python needed)

| File | Purpose |
|---|---|
| `betop-battery.exe` | tray version (no console window) |
| `betop-battery-cli.exe` | command line version (keeps console output) |

> ⚠️ Unsigned executables are blocked by Windows Smart App Control
> (there is no per-app allow-list) — see
> [the section below](#about-windows-smart-app-control).
> If that happens, use Option 1 or Option 3.

### Option 3: From source (developers)

```bash
git clone https://github.com/oitsukiii/betop-battery.git
cd betop-battery
pip install -r requirements.txt

# Option A: run without installing (recommended first try)
python run.py once        # read once
python run.py tray        # start the tray icon
# On Windows you can also just double-click betop-battery.bat

# Option B: proper install
pip install -e .
betop-battery once
betop-battery tray
```

On Windows `pip install hidapi` installs a prebuilt wheel — **no C compiler needed**.

### CLI

```bash
betop-battery                    # read once (default)
betop-battery once --json        # machine-readable
betop-battery tray               # tray mode
betop-battery gui                # settings window
betop-battery overlay            # floating overlay HUD
betop-battery devices            # list supported models
betop-battery probe dump         # dump raw frames (debugging)
betop-battery probe suggest      # draft a descriptor for a new model
```

> **Tip:** if the controller is asleep, press any button and retry.
> The official client can stay open — it does not conflict.

## Supported controllers

| Model | Status | Connection | Contributor |
|---|---|---|---|
| **BETOP Kunpeng 20** (BTP-KP20EB) | ✅ Verified | 2.4G dongle | initial release |
| Other BETOP models | 🙋 **waiting for you** | — | [guide](docs/adapt-new-device.md) |

BETOP models share one **protocol family** (the same `report_id + (subcmd<<4|cmd)` frame layout),
so adapting a new model usually means changing a few numbers. **PRs welcome!**

## Settings GUI

```bash
betop-battery gui
```

Three tabs: **General** (interval, low-battery threshold, notifications, device filter),
**Tray icon** (3 styles — number / ring / battery — color scheme, charging marker,
with a live preview rendered from your real battery level), and **Overlay**
(enable, opacity, font size, fields, click-through, position, colors).

You can also open it from the tray menu: **right-click → 设置…**

## HUD (floating overlay)

```bash
betop-battery overlay
```

A semi-transparent bar pinned on screen, similar to a framerate overlay:

```
BETOP Kunpeng 20  ·  Battery 95%  ·  Battery mode
```

- **Drag with the mouse** to move it (position is remembered)
- **Right-click menu**: refresh / click-through / toggle fields / reset position / close
- **Lock layout** (Windows): mouse events pass through, so it never blocks your game
  - ⚠️ While locked the window receives no mouse events; unlock it first to drag again
- **Emoji icons** (🎮 🔋 ⚡) — toggleable; opacity applies to them too
- Opacity, font size and colors are configurable in the GUI

> The overlay and the tray are **separate processes** — closing one does not affect the other.
> If a game runs in exclusive fullscreen the overlay may be hidden; use borderless windowed mode.

### Screenshots

**General tab:**

![GUI - general](docs/images/gui-general.png)

**Tray icon tab** (the preview uses your real battery level):

![GUI - tray icon](docs/images/gui-tray-icon.png)

**Tray icon in the notification area:**

![tray icon](docs/images/tray-icon.png)

## How it works

```
1. find the vendor interface   2. send a status query      3. read fields from the reply
   usage_page = 0xFF00            02 15  (cmd=5, subcmd=1)     byte[2]        = battery 0..100
                                                               byte[4] & 0x0F = charging flag
```

A real captured frame:

```
02 15 64 00 51 01 01 00 ...
│  │  │  │  │
│  │  │  │  └─ byte[4] & 0x0F = 1  → charging
│  │  │  └──── byte[3]
│  │  └─────── byte[2] = 0x64 = 100 → 100%
│  └────────── header = (subcmd 0x1 << 4) | cmd 0x5 → "status report"
└───────────── report_id = 0x02
```

Full frame spec, command table and field semantics: **[docs/protocol.md](docs/protocol.md)**

## Add support for your controller (usually one JSON file)

```bash
betop-battery probe interfaces   # find the vendor interface (usage_page 0xFF00)
betop-battery probe dump         # dump frames; the tool highlights likely battery bytes
betop-battery probe watch        # plug/unplug the charger to spot changing bytes
betop-battery probe suggest      # auto-draft a descriptor
betop-battery once               # verify
```

Step-by-step tutorial: **[docs/adapt-new-device.md](docs/adapt-new-device.md)**
(Chinese; AI agents should read **[AGENTS.md](AGENTS.md)** instead.)

## Project layout

```
src/betop_battery/
├── protocol.py    frame encode/decode (pure functions, no I/O)
├── transport.py   HID I/O (knows bytes, not models)
├── devices.py     device descriptors (JSON-driven)
├── reader.py      orchestration
├── probe.py       debugging / discovery tooling
├── tray.py        tray UI (presentation only)
├── config.py      user settings
└── cli.py         command dispatch
```

Layering follows **high cohesion, low coupling**: `transport` knows nothing about BETOP,
`tray` knows nothing about HID, and adding a device only touches `devices/*.json`.

## Tests

```bash
pip install -e ".[dev]"
pytest -q
```

Unit tests require **no hardware** — they validate field offsets against a real captured frame.

## How was the protocol obtained?

Through **interoperability analysis**: observing how the official Electron client communicates
with the receiver (its UI logic is shipped as JavaScript, which defines the query command and the
field offsets in the reply). The implementation here is **independent**.

- This project contains **no vendor code, binaries or assets**
- It is **not affiliated with, endorsed by, or authorized by BETOP**
- It only reads the state of **your own device**

## ⚠️ Windows Smart App Control (important)

Windows 11's **Smart App Control (SAC)** blocks **unsigned executables**, including one you
just built yourself:

```
'betop-battery.exe' has been blocked by your organization's Device Guard policy
```

**This is a system policy, not a bug**: SAC only allows programs with a trusted signature.
Check whether it is on:

```powershell
Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' |
  Select-Object VerifiedAndReputablePolicyState
# 1 = enforced   0 = off   2 = evaluation
```

| Situation | Recommendation |
|---|---|
| Personal use | **Run from source** (`python run.py tray`) — the Python interpreter is signed and unaffected |
| Distributing an exe | Requires **code signing** (individuals can buy an OV code signing certificate) |
| Users hit the block | Point them to `pip install` / source; do **not** suggest disabling SAC (turning it off is irreversible without reinstalling Windows) |

> Release attachments from GitHub also carry the "downloaded from the internet" mark, so SAC users are blocked there too.

## Known limitations

- **The controller reports 100% while charging.** This is the device's own
  reading, not a parsing bug — captured raw frames:

  | State | frame | `byte[2]` |
  |---|---|---|
  | on battery | `02 15 5D 00 50 …` | `0x5D` = **93%** |
  | charging | `02 15 64 00 51 …` | `0x64` = **100%** |

  We read `byte[2]` exactly the way the vendor client does, so this is
  device-side (the charging circuit raises the measured voltage) and cannot be
  fixed in software. Use the ⚡ charging icon rather than that 100%.
- ⚠️ **Single controller only**: the app picks one matching interface and reads that one.
  With several controllers connected, behaviour may be unexpected. Multi-controller support
  is not implemented yet — feel free to open an issue describing your setup.
- **Unsigned exe may be blocked by Smart App Control** (see above)
- The controller must be awake (press a button)
- The protocol may change with **firmware updates** — if it breaks, please open an issue with `probe dump` output
- Verified on Windows 11 with the 2.4G dongle (Bluetooth mode untested)
- Some firmwares are picky about **write length**; configurable per device

## Contributing

Very welcome — especially **descriptors for other BETOP models**. No coding required, just JSON.

- [Adapt a new model](docs/adapt-new-device.md)
- [Protocol spec](docs/protocol.md)
- Issues and PRs in Chinese or English

## License

[MIT](LICENSE)
