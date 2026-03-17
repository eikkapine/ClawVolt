<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:1a1a2e,50:0f3460,100:00ccff&height=220&section=header&text=⚡%20ClawVolt&fontSize=72&fontColor=00ff88&fontAlignY=38&desc=Real-time%20Dynamic%20GPU%20Voltage%20Controller&descAlignY=58&descSize=20&descColor=eaeaea&animation=fadeIn" width="100%"/>

# ⚡ ClawVolt

**Real-time Dynamic GPU Voltage Controller for AMD Radeon RX 9070 XT**

*Automatically adjusts voltage offsets based on live core clock — via AMD's official ADLX SDK*

[![Typing SVG](https://readme-typing-svg.demolab.com?font=JetBrains+Mono&weight=600&size=18&duration=3000&pause=800&color=00CCFF&center=true&vCenter=true&multiline=true&repeat=true&width=700&height=60&lines=AMD+RX+9070+XT+%E2%80%A2+RDNA4+%E2%80%A2+Windows+%E2%80%A2+ADLX+SDK;Automatic+voltage+switching+based+on+live+core+clock)](https://github.com/eikkapine/ClawVolt)

<br/>

![Platform](https://img.shields.io/badge/Windows%2010%2F11-0078D6?style=for-the-badge&logo=windows&logoColor=white)
![GPU](https://img.shields.io/badge/RX%209070%20XT-ED1C24?style=for-the-badge&logo=amd&logoColor=white)
![RDNA4](https://img.shields.io/badge/RDNA4-FF6B35?style=for-the-badge&logo=amd&logoColor=white)
![Python](https://img.shields.io/badge/Python%203.12-3776AB?style=for-the-badge&logo=python&logoColor=white)
![C++](https://img.shields.io/badge/C%2B%2B%2017-00599C?style=for-the-badge&logo=cplusplus&logoColor=white)
![ADLX](https://img.shields.io/badge/AMD%20ADLX%20SDK-ED1C24?style=for-the-badge&logo=amd&logoColor=white)
![License](https://img.shields.io/badge/MIT-00ff88?style=for-the-badge)

</div>

<br/>

<img src="https://capsule-render.vercel.app/api?type=rect&color=0:0f3460,100:1a1a2e&height=3" width="100%"/>
---

## 📖 Contents

<div align="center">

| | | |
|:---:|:---:|:---:|
| [🔍 What is ClawVolt?](#-what-is-clawvolt) | [💡 Why it exists](#-why-does-this-exist) | [🏗 Architecture](#-architecture) |
| [✨ Features](#-features) | [📋 Prerequisites](#-prerequisites) | [🔧 Build Guide](#-build-guide) |
| [🚀 Usage](#-usage) | [⚙️ Configuration](#%EF%B8%8F-configuration) | [📈 Thresholds](#-voltage-thresholds) |
| [🪵 Crash Logger](#-crash-logger) | [🔬 ADLX SDK](#-adlx-deep-dive) | [🛡 Safety](#-safety) |

</div>

---

## 🔍 What is ClawVolt?

ClawVolt is a **real-time GPU voltage controller** built specifically for the **AMD Radeon RX 9070 XT** on Windows. It reads your GPU's live core clock every ~500ms and **automatically switches voltage offsets** based on configurable thresholds — using AMD's own official ADLX SDK, the same API that Adrenalin uses internally.

> Unlike Adrenalin's single static undervolt slider, ClawVolt applies **different offsets at different clock ranges**, adapting live to what the GPU is actually doing.

```
   GPU Clock          ClawVolt Applied Offset
   ─────────────────────────────────────────
   >= 3200 MHz  ->   -120 mV  (peak boost — conservative)
   >= 3100 MHz  ->   -160 mV  (high sustained — aggressive)
   >= 3000 MHz  ->   -140 mV  (mid boost — balanced)
   <  3000 MHz  ->   -100 mV  (idle / low load)
```

---

## 💡 Why Does This Exist?

AMD Adrenalin exposes only a **single global voltage offset** — set it once and forget it. The problem:

- **Too conservative** → you leave thermal/power savings on the table during mid-range clocks
- **Too aggressive** → the GPU crashes during peak boost when it needs voltage the most
- **No middle ground** → Adrenalin simply doesn't support per-clock-range offsets

ClawVolt fills this gap by polling the live clock via ADLX and writing back the right offset for the current operating range — all in real time, with hysteresis to prevent flicker.

---

## 🏗 Architecture

```
+----------------------------------------------------------------------+
|                         ClawVolt Stack                               |
|                                                                      |
|   +----------------------+          +----------------------------+   |
|   |   claw_volt_gui.py   |  --OR--  |    claw_volt_cli.py        |   |
|   |   tkinter dashboard  |          |    terminal controller     |   |
|   +----------+-----------+          +-------------+--------------+   |
|              |                                    |                  |
|              +-----------------+------------------+                  |
|                                | subprocess calls                   |
|                                v                                     |
|                   +------------------------+                         |
|                   |    adlx_bridge.exe     |  C++17 / ~500 lines     |
|                   +------------+-----------+                         |
|                                | AMD ADLX C++ API                   |
|                                v                                     |
|                   +------------------------+                         |
|                   |       ADLX.dll         |  ships with Adrenalin   |
|                   +------------+-----------+                         |
|                                | kernel driver interface             |
|                                v                                     |
|                   +------------------------+                         |
|                   |   RX 9070 XT (RDNA4)   |  hardware               |
|                   +------------------------+                         |
|                                                                      |
|   +------------------------------------------------------------------+|
|   |  crash_logger.py  -  background thread                          ||
|   |  Windows Event Log watcher  +  150s flight recorder             ||
|   +------------------------------------------------------------------+|
+----------------------------------------------------------------------+
```

### Why a C++ bridge?

ADLX is a native C++ SDK — Python can't call it directly. The bridge wraps every ADLX operation into a subprocess call with clean machine-parseable output:

```
adlx_bridge.exe --get-clock        ->  CLOCK:3142
adlx_bridge.exe --set-voltage -120 ->  STATUS:VOLTAGE_OFFSET_APPLIED:offset=-120:interface=MGT2_1
adlx_bridge.exe --info             ->  GPU_NAME:AMD Radeon RX 9070 XT ...
```

---

## ✨ Features

### Core Engine

| Feature | Detail |
|:---|:---|
| ⚡ **Dynamic threshold switching** | Define N clock/voltage pairs; switches automatically as GPU boosts and throttles |
| 🔒 **Hysteresis protection** | Requires N consecutive reads before committing — prevents flickering at threshold boundaries |
| 🎯 **Auto interface detection** | Probes MGT2_1 → MGT2 → MGT1, uses the best available for your GPU generation |
| 📐 **Hardware range clamping** | Offset always clamped to −200 to 0 mV (the RX 9070 XT's ADLX-reported safe range) |
| 🔄 **Clean shutdown** | Ctrl+C or window close always calls `ResetToFactory` to restore AMD defaults |

### ADLX Bridge Commands

| Command | Output | Description |
|:---|:---|:---|
| `--get-clock` | `CLOCK:3142` | Live core clock in MHz |
| `--get-voltage` | `VOLTAGE_MV:-120` | Current voltage offset + interface type |
| `--set-voltage -120` | `STATUS:VOLTAGE_OFFSET_APPLIED...` | Write a voltage offset to hardware |
| `--set-auto` | `STATUS:FACTORY_DEFAULTS_RESTORED` | Restore AMD factory tuning |
| `--info` | Full capability dump | GPU name, interface types, VF ranges |

### GUI Highlights

| Feature | Detail |
|:---|:---|
| 📊 **Live clock graph** | 120-sample scrolling history, threshold lines drawn on graph, updates every 250ms |
| 🎛 **Threshold editor** | Add/remove/edit rows live with color-coded dot indicators |
| 💾 **Full preset save/load** | Complete round-trip including all threshold rows — load fully rebuilds the UI |
| 🪵 **Crash log button** | Opens `claw_volt_crashes.log` directly in your default text editor |
| 🚨 **Crash popup** | Modal alert when a crash is detected mid-session with reason + recommendations |
| 📋 **Session log** | Color-coded event stream — green=voltage change, yellow=warn, red=error |

### Crash Logger Highlights

| Feature | Detail |
|:---|:---|
| ✈️ **Flight recorder** | Writes last 150s of telemetry to disk every poll cycle — survives process kill |
| 📬 **Post-mortem detection** | On next startup, checks if last session crashed and reconstructs report from saved data |
| 👁 **Event Log watcher** | Queries for TDR (4101), kernel crash (41), unexpected shutdown (6008) |
| 🧠 **Crash analysis** | 7 reason codes with pattern-matched telemetry classification |
| 📝 **Structured reports** | Reason, analysis, config snapshot, voltage history, telemetry table, recommendations |

---

## 📁 Project Structure

```
ClawVolt/                          <- repo root
|
+-- .github/
|   +-- ISSUE_TEMPLATE/
|       +-- bug_report.md          <- GitHub issue templates
|       +-- feature_request.md
|
+-- assets/
|   +-- icon.ico                   <- app icon (window + exe)
|   +-- icon_preview.png
|
+-- bridge/
|   +-- adlx_bridge.cpp            <- C++ ADLX bridge — compile this first
|   +-- CMakeLists.txt             <- CMake build config
|
+-- build/
|   +-- build_exe_py312.bat        <- one-click exe builder
|   +-- ClawVolt.spec              <- PyInstaller spec
|
+-- src/
|   +-- claw_volt_gui.py           <- Graphical controller (tkinter)
|   +-- claw_volt_cli.py           <- Terminal controller
|   +-- crash_logger.py            <- Crash detection + flight recorder
|
+-- .gitignore
+-- LICENSE                        <- MIT
+-- README.md
```

---

## 📋 Prerequisites

<div align="center">

| Requirement | Version | Notes |
|:---|:---:|:---|
| **Windows 10/11** | 64-bit | — |
| **AMD RX 9070 XT** | RDNA4 | RDNA3/2 may work via MGT1 path |
| **AMD Adrenalin Driver** | Latest | Installs `ADLX.dll` automatically |
| **Python** | **3.12 only** | 3.14+ breaks PyInstaller exe builds |
| **Visual Studio** | 2019 or 2022 | C++ Desktop workload required |
| **CMake** | 3.16+ | For building the C++ bridge |
| **Git** | Any | For cloning the ADLX SDK |
| **AMD ADLX SDK** | Latest | C++ headers only — free from AMD |
| **Administrator rights** | — | Required for voltage writes to the driver |

</div>

---

## 🔧 Build Guide

> **Note:** Total time is approximately 15 minutes on a fresh machine. Read all steps before starting.

---

### Step 1 — Install Python 3.12

> ⚠️ Python 3.14+ breaks PyInstaller exe builds. Use **Python 3.12** specifically.

1. Go to [python.org/downloads/release/python-3128](https://www.python.org/downloads/release/python-3.12.8/)
2. Download **Windows installer (64-bit)**
3. Run installer — check ✅ **"Add Python to PATH"**
4. Verify in a new terminal:

```
py -3.12 --version
```

Expected output: `Python 3.12.x`

---

### Step 2 — Install Visual Studio Build Tools

Required to compile the C++ ADLX bridge.

1. Download [Visual Studio 2022 Build Tools](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022) *(free)*
2. Run the installer
3. Select **"Desktop development with C++"** workload
4. Click **Install**

> If you already have Visual Studio 2019/2022 with the C++ workload installed, skip this step.

---

### Step 3 — Install CMake and Git

Open any terminal and run:

```
winget install Kitware.CMake
winget install Git.Git
```

Close and reopen the terminal, then verify both installed:

```
cmake --version
git --version
```

---

### Step 4 — Install Python dependencies

```
py -3.12 -m pip install pywin32 pyinstaller
```

- `pywin32` — required for the crash logger to read the Windows Event Log
- `pyinstaller` — only needed if you want to build the standalone `.exe`

---

### Step 5 — Clone the ADLX SDK

```
git clone https://github.com/GPUOpen-LibrariesAndSDKs/ADLX.git
```

Note the full path where it cloned — you will need it in the next step.

> `ADLX.dll` is already on your PC from AMD Adrenalin. You only need the SDK for the C++ headers.

---

### Step 6 — Build the C++ bridge

Open a **Developer Command Prompt for VS 2022** (search the Start menu for it — it must be this specific prompt, not a regular terminal).

Navigate to the bridge folder and build:

```
cd ClawVolt\bridge

cmake -B build -DADLX_SDK_DIR="C:\full\path\to\ADLX" -A x64

cmake --build build --config Release
```

Replace `C:\full\path\to\ADLX` with the actual folder where you cloned the SDK in Step 5.

A successful build ends with:
```
adlx_bridge.vcxproj -> ...\bridge\build\Release\adlx_bridge.exe
```

Copy the bridge to the repo root:

```
copy bridge\build\Release\adlx_bridge.exe .
```

---

### Step 7 — Verify the bridge

Run from the repo root (as Administrator):

```
adlx_bridge.exe --info
```

<details>
<summary>✅ Expected output on RX 9070 XT</summary>

```
GPU_NAME:AMD Radeon RX 9070 XT
TUNING_MANUAL_GFX_SUPPORTED:1
INTERFACE_MGT1:0
INTERFACE_MGT2:1
INTERFACE_MGT2_1:1
MGT2_VOLT_RANGE:-200-0,step=1
```

`INTERFACE_MGT2_1:1` confirms RDNA4 single-offset voltage control is available.

</details>

---

### Step 8 — Run ClawVolt

> ⚠️ Right-click your terminal and select **Run as Administrator** before running ClawVolt.

**GUI (recommended):**
```
py -3.12 src\claw_volt_gui.py
```

**Terminal / CLI:**
```
py -3.12 src\claw_volt_cli.py
```

---

### Optional — Build a standalone .exe

If you want a double-clickable `.exe` that works without Python installed:

```
build\build_exe_py312.bat
```

Output folder: `build\dist\ClawVolt\`

Copy `adlx_bridge.exe` into `build\dist\ClawVolt\` alongside `ClawVolt.exe`, then right-click the exe and select **Run as Administrator**.

---

## 🚀 Usage

> ⚠️ **Always run as Administrator.** Voltage writes to the GPU driver require elevated privileges.

### CLI Mode

```
py -3.12 src\claw_volt_cli.py
```

```
╔══════════════════════════════════════════════════════╗
║           ClawVolt — Dynamic GPU Voltage Control     ║
║           AMD RX 9070 XT (RDNA4) · Windows · ADLX   ║
╚══════════════════════════════════════════════════════╝

  Thresholds:
    >= 3200 MHz  ->   -120 mV
    >= 3100 MHz  ->   -160 mV
    >= 3000 MHz  ->   -140 mV
    <  3000 MHz  ->   -100 mV  (idle)

[14:21:01] INFO  GPU: AMD Radeon RX 9070 XT
[14:21:01] INFO  Crash logger active
[14:21:02] VOLT  Clock 3142 MHz   -100mV -> -160mV
[14:21:04] INFO  Clock 3142 MHz   -160mV  (stable)
[14:21:07] VOLT  Clock 3215 MHz   -160mV -> -120mV
```

<details>
<summary>📋 All CLI flags</summary>

| Flag | Description |
|:---|:---|
| *(no flags)* | Run with built-in default config |
| `--config my.json` | Load a saved JSON config file |
| `--info` | Print GPU capabilities and exit |
| `--reset` | Reset GPU to auto tuning and exit |
| `--quiet` | Suppress per-poll verbose lines |
| `--save-config out.json` | Save current config to file |

</details>

---

### GUI Mode

```
py -3.12 src\claw_volt_gui.py
```

```
+----------------------------------------------------------------------+
|  ClawVolt   Dynamic GPU Voltage Controller · RX 9070 XT              |
|                      [Start] [Stop] [Save] [Load] [Log]              |
+-----------------+----------------------------------------------------+
|  GPU            |                                                    |
|  AMD Radeon ... |     Core Clock History  (120 samples, live)        |
|  Status: active |     threshold lines drawn directly on graph        |
+-----------------+                                                    |
|  Live           +----------------------------------------------------+
|  Core Clock --- |                                                    |
|  Voltage    --- |     Session Log                                    |
|  Pending -> --- |     [14:21:01] Connected: AMD Radeon RX 9070 XT    |
|  Changes    --- |     [14:21:01] Crash logger active                 |
|  Crashes    --- |     [14:21:02] 3142 MHz  -100mV -> -160mV          |
+-----------------+     [14:21:07] 3215 MHz  -160mV -> -120mV          |
|  Settings       |                                                    |
|  Bridge path .. |                                                    |
|  Poll (s)  0.5  |                                                    |
|  Hysteresis  2  |                                                    |
|  Idle offset-100|                                                    |
+-----------------+                                                    |
|  Thresholds     |                                                    |
|  * 3200  -120   |                                                    |
|  * 3100  -160   |                                                    |
|  * 3000  -140   |                                                    |
|  [+ Add Row]    |                                                    |
+-----------------+----------------------------------------------------+
```

<details>
<summary>🖱 GUI Button Reference</summary>

| Button | Action |
|:---|:---|
| **Start** | Connect to GPU, start monitoring and crash logger |
| **Stop** | Stop monitoring, restore GPU auto tuning |
| **Save (💾)** | Save full config (all settings + all threshold rows) to JSON |
| **Load (📂)** | Load a JSON preset — completely rebuilds all threshold rows |
| **Log (🪵)** | Open `claw_volt_crashes.log` in your default text editor |

</details>

---

## ⚙️ Configuration

```json
{
  "bridge_path": "adlx_bridge.exe",
  "poll_interval_sec": 0.5,
  "hysteresis_count": 2,
  "idle_voltage_offset_mv": -100,
  "thresholds": [
    { "clock_mhz": 3200, "voltage_offset_mv": -120 },
    { "clock_mhz": 3100, "voltage_offset_mv": -160 },
    { "clock_mhz": 3000, "voltage_offset_mv": -140 }
  ],
  "verbose": true
}
```

| Field | Type | Description |
|:---|:---|:---|
| `bridge_path` | string | Path to `adlx_bridge.exe`. Relative or absolute. |
| `poll_interval_sec` | float | Clock poll frequency. 0.3–1.0s recommended. |
| `hysteresis_count` | int | Consecutive reads required before committing a voltage change. |
| `idle_voltage_offset_mv` | int | Offset applied when clock is below all thresholds. |
| `thresholds` | array | List of `{clock_mhz, voltage_offset_mv}` pairs. Must be between 0 and -200. |
| `verbose` | bool | CLI only — print status on every poll tick. |

Save a config template:
```
py -3.12 src\claw_volt_cli.py --save-config my_config.json
```

Load it:
```
py -3.12 src\claw_volt_cli.py --config my_config.json
```

---

## 📈 Voltage Thresholds

### How matching works

Thresholds are evaluated **highest to lowest** — first match wins:

```
clock = 3142 MHz

  Is 3142 >= 3200?  ->  NO
  Is 3142 >= 3100?  ->  YES  <- match, apply -160 mV
```

### Hysteresis prevents flicker

Without hysteresis a clock oscillating near a boundary would switch voltage every poll:

```
poll 1:  3099 MHz  ->  want -100 mV  (pending 1/2 — not yet committed)
poll 2:  3103 MHz  ->  want -160 mV  (counter reset, pending 1/2)
poll 3:  3097 MHz  ->  want -100 mV  (pending 1/2)
poll 4:  3095 MHz  ->  want -100 mV  (pending 2/2 -> COMMIT -100 mV)
```

### Recommended starting values for RX 9070 XT

| Clock | Offset | Rationale |
|:---:|:---:|:---|
| >= 3200 MHz | -100 mV | Peak boost — GPU needs voltage here; be conservative |
| >= 3100 MHz | -140 mV | High sustained — slightly more headroom |
| >= 3000 MHz | -160 mV | Mid boost — safe to push further |
| idle | -100 mV | Low load — conservative, negligible impact |

> **Tuning method:** Start at -80 mV everywhere. Run Unigine Heaven for 20 minutes. Stable? Lower one threshold by 10 mV and repeat. Crash? Raise that threshold by 20 mV.

---

## 🪵 Crash Logger

### How it works

```
Every poll tick
  -> TelemetryPoint(clock, voltage, temp, hotspot, power, fan)
       -> written to disk (claw_volt_telemetry.json) every cycle
       -> 300-sample circular buffer in memory

On startup
  -> Was previous session's heartbeat file left behind? (means crash)
       -> YES -> load saved telemetry from disk
              -> query Event Log for TDR/crash events during that session
              -> analyse telemetry -> write post-mortem crash report
       -> NO  -> clean exit, nothing to report

While running
  -> heartbeat file exists on disk (deleted on clean exit)
```

> **Why post-mortem?** When a GPU TDR happens, Windows kills every process with an active GPU context — including ClawVolt. The process dies before any in-memory watcher can fire. ClawVolt solves this by writing telemetry to disk every poll cycle and detecting the crash on next launch.

### Crash classification

| Reason Code | Detection Logic |
|:---|:---|
| `UNDERVOLT_TOO_AGGRESSIVE` | Clock >= 3100 MHz + offset <= -150 mV + voltage change within 10s |
| `RAPID_VOLTAGE_SWING` | 3+ voltage changes in 15 seconds before crash |
| `VOLTAGE_INSUFFICIENT_FOR_CLOCK` | Peak boost (>= 3200 MHz) at offset <= -130 mV |
| `SUSTAINED_HIGH_CLOCK_LOW_VOLTAGE` | 70%+ of last-20s samples at >= 3100 MHz and <= -140 mV |
| `CRASH_AFTER_VOLTAGE_CHANGE` | Crash within 5s of a voltage transition |
| `THERMAL` | GPU temperature >= 90°C at crash time |
| `THERMAL_HOTSPOT` | GPU hotspot >= 100°C at crash time |
| `UNKNOWN` | No pattern matched — raw telemetry still preserved |

### Sample crash report

```
========================================================================
  CLAWVOLT CRASH REPORT #1  [POST-MORTEM]
  Detected  : 2026-03-17 23:25:21
  Event     : Windows Event ID 4101
  Event msg : Display driver stopped responding and has recovered
========================================================================

  REASON CODE : UNDERVOLT_TOO_AGGRESSIVE

  ANALYSIS
  -----------------------------------------------------------------------
  Clock was 3142 MHz at -160 mV when the crash occurred. A voltage
  change to -160 mV occurred 3.2s before the crash. The GPU needed
  more voltage to sustain this frequency stably. Raise the >=3100 MHz
  threshold by 20-40 mV.

  ACTIVE CONFIG AT CRASH TIME
  Thresholds: >=3200MHz->-120mV  >=3100MHz->-160mV  >=3000MHz->-140mV

  VOLTAGE CHANGE HISTORY (pre-crash)
  23:24:58   -140 mV -> -160 mV   <- 3.2s before crash

  TELEMETRY SNAPSHOT (last 30 samples)
       Time     Clock   Voltage    Temp   HotSpot    Power    Fan
  -----------------------------------------------------------------------
  23:24:57.0   3124 MHz  -140 mV   72°      85°    218W    63%
  23:24:57.5   3142 MHz  -160 mV   73°      86°    220W    64%

  RECOMMENDATIONS
  1. Raise the voltage offset for >=3100 MHz by 20-40 mV.
  2. Increase hysteresis_count to 3 or 4.
  3. Test with Unigine Heaven for 20+ minutes after each change.
========================================================================
```

---

## 🔬 ADLX Deep Dive

<details>
<summary>Click to expand — Interface call chain</summary>

```
IADLXSystem
  -> GetGPUTuningServices()
       -> IADLXGPUTuningServices
            +- IsSupportedManualGFXTuning(gpu)
            +- GetManualGFXTuning(gpu) -> IADLXInterface
            |    -> QueryInterface(MGT2_1::IID())
            |         -> IADLXManualGraphicsTuning2_1
            |              +- GetGPUVoltageRange()   -> -200 to 0 mV
            |              +- GetGPUVoltageDefault()  -> 0 (RDNA4)
            |              -> SetGPUVoltage(mV)       <- core write
            -> ResetToFactory(gpu)                    <- clean exit

IADLXSystem
  -> GetPerformanceMonitoringServices()
       -> GetCurrentGPUMetrics(gpu)
            -> IADLXGPUMetrics
                 -> GPUClockSpeed()                   <- core read
```

</details>

<details>
<summary>Click to expand — RDNA4 voltage model vs previous generations</summary>

| Generation | Interface | Model |
|:---|:---|:---|
| RDNA2 / RDNA3 | MGT1 | Multi-point VF curve — each frequency/voltage pair settable independently |
| **RDNA4 (9070 XT)** | **MGT2_1** | **Single global offset applied to the entire VF curve** |

On the RX 9070 XT:
- Range exposed by ADLX: **-200 mV to 0 mV**
- `0` = stock voltage (AMD default)
- `-200` = maximum the driver allows
- The value passed to `SetGPUVoltage` **is** the offset — no arithmetic needed

</details>

<details>
<summary>Click to expand — Source files compiled into the bridge</summary>

| File | Purpose |
|:---|:---|
| `bridge/adlx_bridge.cpp` | Application logic — reads clock, writes voltage |
| `SDK/ADLXHelper/Windows/Cpp/ADLXHelper.cpp` | Loads `ADLX.dll` at runtime via `LoadLibrary` |
| `SDK/Platform/Windows/WinAPIs.cpp` | Platform functions: `adlx_load_library`, `adlx_get_proc_address` |

`ADLX.dll` is not linked at compile time — loaded dynamically at runtime. The binary works on any PC with AMD Adrenalin installed.

</details>

---

## 🔧 Troubleshooting

<details>
<summary><b>adlx_bridge.exe --info returns nothing or fails</b></summary>

- Run the terminal as Administrator
- Verify `C:\Windows\System32\ADLX.dll` exists
- Reinstall AMD Adrenalin if the DLL is missing

</details>

<details>
<summary><b>TUNING_MANUAL_GFX_SUPPORTED:0</b></summary>

- Close MSI Afterburner, AMD Adrenalin overlay, or any tool holding the tuning lock
- Reboot and try again

</details>

<details>
<summary><b>--set-voltage returns ADLX_NOT_SUPPORTED</b></summary>

- Update to the latest AMD Adrenalin driver
- Ensure no other application is using Adrenalin Performance Tuning simultaneously

</details>

<details>
<summary><b>GUI graph stays empty after clicking Start</b></summary>

- Confirm you are running as Administrator
- Check the Session Log panel for error messages
- Test manually: `adlx_bridge.exe --get-clock` should return `CLOCK:XXXX`

</details>

<details>
<summary><b>Preset load does not update threshold rows</b></summary>

- Make sure you are using the latest `claw_volt_gui.py`
- Early versions had a bug where only scalar settings were updated on load

</details>

<details>
<summary><b>CMake build errors</b></summary>

| Error | Fix |
|:---|:---|
| `ADLX SDK not found` | `ADLX_SDK_DIR` must point to the repo root containing `SDK\` |
| `unresolved external: adlx_load_library` | `WinAPIs.cpp` missing — use the provided `CMakeLists.txt` |
| `Cannot open include file: IGPUManualGFXTuning1.h` | That file does not exist — all interfaces are in `IGPUManualGFXTuning.h` |

</details>

---

## 🛡 Safety

<div align="center">

| Protection | How it works |
|:---|:---|
| 🚫 **Positive offsets blocked** | Bridge rejects any offset > 0 — cannot accidentally overvolt |
| 📐 **Hardware range clamped** | All writes clamped to ADLX-reported range (-200 to 0 mV) |
| 🔄 **Always restores on exit** | Ctrl+C, window close, and Stop all call `ResetToFactory` |
| 📖 **Crash logger is read-only** | Only reads Event Log and telemetry — never writes voltages |

</div>

> **If ClawVolt crashes without cleanup:** open AMD Adrenalin → Performance → Tuning → click **Reset** to restore defaults immediately.

---

## 📄 License

MIT — free to use, modify, and distribute.

The [ADLX SDK](https://github.com/GPUOpen-LibrariesAndSDKs/ADLX) is distributed under AMD's own open-source license.

---

<div align="center">

*Built with AMD ADLX · Python 3 · C++17 · Not affiliated with or endorsed by AMD*

</div>
