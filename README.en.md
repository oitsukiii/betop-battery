# betop-battery

**Read the battery level of BETOP (北通) gamepads — shown right in the Windows system tray.**

English · [简体中文](README.md)

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
- ✅ **Tray icon** showing the number, with a detailed hover tooltip
- ✅ **Low-battery notification** (threshold and interval configurable)
- ✅ **CLI mode** (`once` / `--json`) for scripting
- ✅ **Data-driven**: adding a new model means adding one JSON file — no code changes
- ✅ **Built-in `probe` tooling** so you can adapt your own controller

## Quick start

### Prebuilt binary (easiest)

Download `betop-battery.exe` from [Releases](../../releases) and double-click it.

### From source

```bash
git clone https://github.com/REPLACE-ME/betop-battery.git
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

## Known limitations

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
