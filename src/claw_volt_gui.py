#!/usr/bin/env python3
"""
ClawVolt GUI - Dynamic GPU Voltage Controller
AMD RX 9070 XT (RDNA4) · Windows · ADLX bridge
"""

import tkinter as tk
from tkinter import messagebox, filedialog
import subprocess, threading, time, json, sys, os
from datetime import datetime
from collections import deque
from crash_logger import CrashLogger

# ── Path resolution — works both as .py script and PyInstaller .exe ───────────
def _app_dir() -> str:
    """Return the folder containing this app (exe folder when frozen)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

APP_DIR = _app_dir()

def _resolve(p: str) -> str:
    """
    Make a relative path absolute.
    When frozen by PyInstaller, bundled data files live in sys._MEIPASS
    (a temp extraction folder). We check there first, then fall back to
    the exe's own directory (for files copied next to the exe like adlx_bridge.exe).
    """
    if os.path.isabs(p):
        return p
    # Check PyInstaller's extracted bundle temp dir first
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = os.path.join(meipass, p)
        if os.path.exists(candidate):
            return candidate
    # Fall back to directory containing the exe / script
    return os.path.join(APP_DIR, p)

DEFAULT_CONFIG = {
    "bridge_path": "adlx_bridge.exe",
    "poll_interval_sec": 0.5,
    "hysteresis_count": 2,
    "thresholds": [
        {"clock_mhz": 3200, "voltage_offset_mv": -120},
        {"clock_mhz": 3100, "voltage_offset_mv": -160},
        {"clock_mhz": 3000, "voltage_offset_mv": -140},
    ],
    "idle_voltage_offset_mv": -100,
}

C = {
    "bg": "#1a1a2e", "bg2": "#16213e", "bg3": "#0f3460",
    "accent": "#e94560", "text": "#eaeaea", "dim": "#8888aa",
    "green": "#00ff88", "yellow": "#ffd700", "red": "#ff4444",
    "blue": "#4488ff", "cyan": "#00ccff",
    "gbg": "#0a0a1a", "ggrid": "#1a1a3a",
    "th": ["#ff6600", "#ffaa00", "#ffe000", "#4488ff"],
}

# ── Bridge wrappers ────────────────────────────────────────────────────────────

# CREATE_NO_WINDOW — prevents console popup and subprocess hanging in frozen exe
_NO_WINDOW = 0x08000000

def _run(bridge, args, timeout=3.0):
    try:
        r = subprocess.run(
            [bridge] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Bridge call timed out"
    except FileNotFoundError:
        return -1, "", f"Bridge not found: {bridge}"
    except Exception as e:
        return -1, "", str(e)

def bridge_clock(b):
    rc, out, _ = _run(b, ["--get-clock"])
    if rc == 0 and out.startswith("CLOCK:"):
        try: return int(out.split(":")[1])
        except: pass
    return None

def bridge_set_voltage(b, mv):
    rc, out, _ = _run(b, ["--set-voltage", str(mv)])
    return rc == 0 and "VOLTAGE_OFFSET_APPLIED" in out

def bridge_reset(b):
    rc, out, _ = _run(b, ["--set-auto"])
    return rc == 0

def bridge_info(b):
    rc, out, _ = _run(b, ["--info"], timeout=5.0)
    info = {}
    if rc == 0:
        for line in out.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                info[k.strip()] = v.strip()
    return info

def target_offset(clock, cfg):
    for t in sorted(cfg["thresholds"], key=lambda x: x["clock_mhz"], reverse=True):
        if clock >= t["clock_mhz"]:
            return t["voltage_offset_mv"]
    return cfg["idle_voltage_offset_mv"]


# ── VRAM bridge wrappers ──────────────────────────────────────────────────────

TIMING_NAMES = {0: "Default", 1: "Fast", 2: "Fast L2", 3: "Automatic", 4: "Level 1", 5: "Level 2"}
TIMING_LIST = [TIMING_NAMES[i] for i in range(6)]

def bridge_vram_info(b):
    rc, out, _ = _run(b, ["--vram-info"], timeout=5.0)
    info = {}
    if rc == 0:
        for line in out.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                info[k.strip()] = v.strip()
    return info

def bridge_get_vram_freq(b):
    rc, out, _ = _run(b, ["--get-vram-freq"])
    info = {}
    if rc == 0:
        for line in out.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                info[k.strip()] = v.strip()
    return info

def bridge_set_vram_freq(b, mhz):
    rc, out, err = _run(b, ["--set-vram-freq", str(mhz)])
    return rc == 0 and "VRAM_FREQ_SET" in out, (err or out)

def bridge_get_vram_timing(b):
    rc, out, _ = _run(b, ["--get-vram-timing"])
    info = {}
    if rc == 0:
        for line in out.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                info[k.strip()] = v.strip()
    return info

def bridge_set_vram_timing(b, index):
    rc, out, err = _run(b, ["--set-vram-timing", str(index)])
    return rc == 0 and "VRAM_TIMING_SET" in out, (err or out)

# ── Power bridge wrappers ─────────────────────────────────────────────────────

def bridge_power_info(b):
    rc, out, _ = _run(b, ["--power-info"], timeout=5.0)
    info = {}
    if rc == 0:
        for line in out.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                info[k.strip()] = v.strip()
    return info

def bridge_set_power_limit(b, pct):
    rc, out, err = _run(b, ["--set-power-limit", str(pct)])
    return rc == 0 and "POWER_LIMIT_SET" in out, (err or out)


# ── Main Application ───────────────────────────────────────────────────────────

class App:
    def __init__(self):
        self.cfg = {**DEFAULT_CONFIG, "thresholds": [dict(t) for t in DEFAULT_CONFIG["thresholds"]]}
        self.running = False
        self.thread = None

        # Shared state — written only from monitor thread via after()
        self.last_clock = 0
        self.last_vram_clock = 0
        self.current_offset = None
        self.pending_offset = None
        self.pending_count = 0
        self.stat_changes = 0
        self.clock_history = deque(maxlen=120) # thread-safe append
        self.crash_logger: CrashLogger | None = None
        # VRAM and Power state
        self._vram_freq_min = 0
        self._vram_freq_max = 3000
        self._vram_freq_step = 1
        self._vram_timing_supported = False
        self._power_min = -50
        self._power_max = 50
        self._power_step = 1

        self.root = tk.Tk()
        self.root.title("ClawVolt — Dynamic GPU Voltage Controller")
        self.root.configure(bg=C["bg"])
        self.root.minsize(820, 640)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        # Set window icon
        _icon = _resolve("icon.ico")
        if os.path.exists(_icon):
            try:
                self.root.iconbitmap(_icon)
            except Exception:
                pass

        self._build()
        # Start the GUI refresh timer immediately — it runs forever
        self._schedule_refresh()

    # ── UI Construction ────────────────────────────────────────────────────────

    def _build(self):
        self._toolbar()
        body = tk.Frame(self.root, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # Left panel with scrollbar
        left_container = tk.Frame(body, bg=C["bg"], width=290)
        left_container.pack(side="left", fill="y", padx=(0, 6))
        left_container.pack_propagate(False)

        # Create canvas and scrollbar for left panel
        self.left_canvas = tk.Canvas(left_container, bg=C["bg"], highlightthickness=0, width=270)
        self.left_scrollbar = tk.Scrollbar(left_container, orient="vertical", command=self.left_canvas.yview)
        self.left_scrollable = tk.Frame(self.left_canvas, bg=C["bg"])

        self.left_scrollable.bind(
            "<Configure>",
            lambda e: self.left_canvas.configure(scrollregion=self.left_canvas.bbox("all"))
        )

        self.left_canvas.create_window((0, 0), window=self.left_scrollable, anchor="nw")
        self.left_canvas.configure(yscrollcommand=self.left_scrollbar.set)

        # Pack scrollbar and canvas
        self.left_scrollbar.pack(side="right", fill="y")
        self.left_canvas.pack(side="left", fill="both", expand=True)

        # Enable mousewheel scrolling
        self.left_scrollable.bind("<Enter>", lambda e: self._bind_mousewheel())
        self.left_scrollable.bind("<Leave>", lambda e: self._unbind_mousewheel())

        right = tk.Frame(body, bg=C["bg"])
        right.pack(side="left", fill="both", expand=True)

        self._panel_gpu(self.left_scrollable)
        self._panel_live(self.left_scrollable)
        self._panel_settings(self.left_scrollable)
        self._panel_thresholds(self.left_scrollable)
        self._panel_vram(self.left_scrollable)
        self._panel_power(self.left_scrollable)
        self._graph(right)
        self._log(right)

    def _bind_mousewheel(self):
        self.left_canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self):
        self.left_canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event):
        self.left_canvas.yview_scroll(int(-1*(event.delta/120)), "units")

    def _toolbar(self):
        bar = tk.Frame(self.root, bg=C["bg3"], height=52)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Label(bar, text="⚡ ClawVolt", font=("Segoe UI", 14, "bold"),
            bg=C["bg3"], fg=C["accent"]).pack(side="left", padx=16)
        tk.Label(bar, text="Dynamic GPU Voltage Controller · RX 9070 XT",
            font=("Segoe UI", 9), bg=C["bg3"], fg=C["dim"]).pack(side="left")
        bf = tk.Frame(bar, bg=C["bg3"])
        bf.pack(side="right", padx=10)
        self.btn_start = tk.Button(bf, text="▶ Start", command=self._start,
            bg=C["green"], fg="#000", font=("Segoe UI", 9, "bold"),
            relief="flat", padx=14, pady=5, cursor="hand2")
        self.btn_start.pack(side="left", padx=4, pady=8)
        self.btn_stop = tk.Button(bf, text="⏹ Stop", command=self._stop,
            bg=C["bg2"], fg=C["dim"], font=("Segoe UI", 9, "bold"),
            relief="flat", padx=14, pady=5, cursor="hand2", state="disabled")
        self.btn_stop.pack(side="left", padx=4, pady=8)
        tk.Button(bf, text="💾", command=self._save_cfg,
            bg=C["bg2"], fg=C["dim"], font=("Segoe UI", 10),
            relief="flat", padx=8, pady=5, cursor="hand2").pack(side="left", padx=2, pady=8)
        tk.Button(bf, text="📂", command=self._load_cfg,
            bg=C["bg2"], fg=C["dim"], font=("Segoe UI", 10),
            relief="flat", padx=8, pady=5, cursor="hand2").pack(side="left", padx=2, pady=8)
        tk.Button(bf, text="🪵 Log", command=self._open_crash_log,
            bg=C["bg2"], fg=C["dim"], font=("Segoe UI", 8),
            relief="flat", padx=8, pady=5, cursor="hand2").pack(side="left", padx=2, pady=8)

    def _frame(self, parent, title):
        f = tk.LabelFrame(parent, text=f" {title} ", font=("Segoe UI", 9, "bold"),
            bg=C["bg"], fg=C["dim"], bd=1, relief="solid", labelanchor="nw")
        f.pack(fill="x", padx=4, pady=(0, 5))
        return f

    def _panel_gpu(self, p):
        f = self._frame(p, "GPU")
        self.lbl_gpu = tk.Label(f, text="Not connected", font=("Segoe UI", 10, "bold"),
            bg=C["bg"], fg=C["text"])
        self.lbl_gpu.pack(anchor="w", padx=8, pady=(4, 0))
        self.lbl_status = tk.Label(f, text="Status: idle", font=("Segoe UI", 8),
            bg=C["bg"], fg=C["dim"])
        self.lbl_status.pack(anchor="w", padx=8, pady=(0, 4))

    def _panel_live(self, p):
        f = self._frame(p, "Live")
        def row(label, color):
            r = tk.Frame(f, bg=C["bg"])
            r.pack(fill="x", padx=8, pady=2)
            tk.Label(r, text=label, font=("Segoe UI", 8), bg=C["bg"],
                fg=C["dim"], width=14, anchor="w").pack(side="left")
            v = tk.StringVar(value="---")
            lbl = tk.Label(r, textvariable=v, font=("Consolas", 12, "bold"),
                bg=C["bg"], fg=color)
            lbl.pack(side="left")
            return v
        self.var_clock = row("Core Clock", C["green"])
        self.var_vram_clock = row("VRAM Clock", C["cyan"])
        self.var_voltage = row("Voltage Offset", C["yellow"])
        self.var_pending = row("Pending →", C["dim"])
        self.var_changes = row("Changes", C["blue"])
        self.var_crashes = row("Crashes", C["red"])
        tk.Frame(f, bg=C["bg"], height=2).pack()

    def _panel_settings(self, p):
        f = self._frame(p, "Settings")
        def row(label, default, w=22):
            r = tk.Frame(f, bg=C["bg"])
            r.pack(fill="x", padx=8, pady=2)
            tk.Label(r, text=label, font=("Segoe UI", 8), bg=C["bg"],
                fg=C["dim"], width=16, anchor="w").pack(side="left")
            v = tk.StringVar(value=str(default))
            tk.Entry(r, textvariable=v, font=("Consolas", 9),
                bg=C["bg2"], fg=C["text"], insertbackground=C["text"],
                relief="flat", width=w).pack(side="left", padx=4)
            return v
        self.v_bridge = row("Bridge path", self.cfg["bridge_path"])
        self.v_poll = row("Poll (s)", self.cfg["poll_interval_sec"], 6)
        self.v_hyst = row("Hysteresis", self.cfg["hysteresis_count"], 6)
        self.v_idle = row("Idle offset (mV)", self.cfg["idle_voltage_offset_mv"], 6)
        tk.Frame(f, bg=C["bg"], height=2).pack()

    def _panel_thresholds(self, p):
        f = self._frame(p, "Thresholds (≥ MHz → mV)")
        hdr = tk.Frame(f, bg=C["bg2"])
        hdr.pack(fill="x", padx=6, pady=(4, 0))
        for col, w in [("≥ Clock MHz", 10), ("Offset mV", 10), ("", 3)]:
            tk.Label(hdr, text=col, font=("Segoe UI", 8, "bold"),
                bg=C["bg2"], fg=C["dim"], width=w, anchor="w").pack(side="left", padx=4)
        self.th_frame = tk.Frame(f, bg=C["bg"])
        self.th_frame.pack(fill="x", padx=6, pady=2)
        tk.Button(f, text="+ Add Row", command=self._add_threshold_row,
            bg=C["bg3"], fg=C["text"], font=("Segoe UI", 8),
            relief="flat", padx=8, pady=2, cursor="hand2").pack(anchor="w", padx=6, pady=(2, 6))
        self.th_rows = []
        for t in self.cfg["thresholds"]:
            self._add_threshold_row(t["clock_mhz"], t["voltage_offset_mv"])

    def _add_threshold_row(self, clock=3000, mv=-100):
        row = tk.Frame(self.th_frame, bg=C["bg"])
        row.pack(fill="x", pady=1)
        color = C["th"][len(self.th_rows) % len(C["th"])]
        tk.Label(row, text="●", font=("Segoe UI", 10), bg=C["bg"], fg=color).pack(side="left", padx=4)
        cv = tk.StringVar(value=str(clock))
        mv_v = tk.StringVar(value=str(mv))
        tk.Entry(row, textvariable=cv, font=("Consolas", 9),
            bg=C["bg2"], fg=C["text"], insertbackground=C["text"],
            relief="flat", width=8).pack(side="left", padx=2)
        tk.Entry(row, textvariable=mv_v, font=("Consolas", 9),
            bg=C["bg2"], fg=C["yellow"], insertbackground=C["text"],
            relief="flat", width=8).pack(side="left", padx=2)
        pair = [cv, mv_v]
        def remove(r=row, pair=pair):
            r.destroy()
            if pair in self.th_rows: self.th_rows.remove(pair)
        tk.Button(row, text="✕", command=remove,
            bg=C["bg"], fg=C["dim"], font=("Segoe UI", 8),
            relief="flat", padx=2, cursor="hand2").pack(side="left", padx=2)
        self.th_rows.append(pair)

    def _panel_vram(self, p):
        f = self._frame(p, "VRAM Tuning")

        # Frequency row
        freq_row = tk.Frame(f, bg=C["bg"])
        freq_row.pack(fill="x", padx=8, pady=(6, 2))
        tk.Label(freq_row, text="Max Freq (MHz)", font=("Segoe UI", 8),
            bg=C["bg"], fg=C["dim"], width=14, anchor="w").pack(side="left")
        self.v_vram_freq = tk.StringVar(value="0")
        self._vram_freq_entry = tk.Entry(freq_row, textvariable=self.v_vram_freq,
            font=("Consolas", 9), bg=C["bg2"], fg=C["green"],
            insertbackground=C["text"], relief="flat", width=7)
        self._vram_freq_entry.pack(side="left", padx=4)
        self._vram_freq_range_lbl = tk.Label(freq_row, text="range: ---",
            font=("Segoe UI", 7), bg=C["bg"], fg=C["dim"])
        self._vram_freq_range_lbl.pack(side="left", padx=4)

        # Timing row
        timing_row = tk.Frame(f, bg=C["bg"])
        timing_row.pack(fill="x", padx=8, pady=(2, 2))
        tk.Label(timing_row, text="Mem Timing", font=("Segoe UI", 8),
            bg=C["bg"], fg=C["dim"], width=14, anchor="w").pack(side="left")
        self.v_vram_timing = tk.StringVar(value="Default")
        self._timing_menu = tk.OptionMenu(timing_row, self.v_vram_timing, *TIMING_LIST)
        self._timing_menu.config(bg=C["bg2"], fg=C["yellow"], font=("Segoe UI", 8),
            relief="flat", highlightthickness=0, activebackground=C["bg3"])
        self._timing_menu["menu"].config(bg=C["bg2"], fg=C["yellow"], font=("Segoe UI", 8))
        self._timing_menu.pack(side="left", padx=4)
        self._vram_timing_lbl = tk.Label(timing_row, text="(not supported)",
            font=("Segoe UI", 7), bg=C["bg"], fg=C["dim"])
        self._vram_timing_lbl.pack(side="left", padx=4)

        # Apply button
        btn_row = tk.Frame(f, bg=C["bg"])
        btn_row.pack(fill="x", padx=8, pady=(2, 6))
        tk.Button(btn_row, text="Apply VRAM", command=self._apply_vram,
            bg=C["accent"], fg="white", font=("Segoe UI", 8, "bold"),
            relief="flat", padx=10, pady=3, cursor="hand2").pack(side="left")
        tk.Button(btn_row, text="Reset", command=self._reset_vram,
            bg=C["bg2"], fg=C["dim"], font=("Segoe UI", 8),
            relief="flat", padx=8, pady=3, cursor="hand2").pack(side="left", padx=4)

    def _panel_power(self, p):
        f = self._frame(p, "Power Limit")

        row = tk.Frame(f, bg=C["bg"])
        row.pack(fill="x", padx=8, pady=(6, 2))
        tk.Label(row, text="Limit (%)", font=("Segoe UI", 8),
            bg=C["bg"], fg=C["dim"], width=14, anchor="w").pack(side="left")
        self.v_power_limit = tk.StringVar(value="0")
        tk.Entry(row, textvariable=self.v_power_limit, font=("Consolas", 9),
            bg=C["bg2"], fg=C["yellow"], insertbackground=C["text"],
            relief="flat", width=7).pack(side="left", padx=4)
        self._power_range_lbl = tk.Label(row, text="range: ---",
            font=("Segoe UI", 7), bg=C["bg"], fg=C["dim"])
        self._power_range_lbl.pack(side="left", padx=4)

        info_row = tk.Frame(f, bg=C["bg"])
        info_row.pack(fill="x", padx=8, pady=(0, 2))
        tk.Label(info_row, text="0% = stock TDP  +20 = 120% TDP  -20 = 80% TDP",
            font=("Segoe UI", 7), bg=C["bg"], fg=C["dim"]).pack(anchor="w")

        btn_row = tk.Frame(f, bg=C["bg"])
        btn_row.pack(fill="x", padx=8, pady=(2, 6))
        tk.Button(btn_row, text="Apply Power", command=self._apply_power,
            bg=C["accent"], fg="white", font=("Segoe UI", 8, "bold"),
            relief="flat", padx=10, pady=3, cursor="hand2").pack(side="left")
        tk.Button(btn_row, text="Reset", command=self._reset_power,
            bg=C["bg2"], fg=C["dim"], font=("Segoe UI", 8),
            relief="flat", padx=8, pady=3, cursor="hand2").pack(side="left", padx=4)

    def _apply_vram(self):
        if not hasattr(self, '_active_bridge'):
            messagebox.showwarning("Not Running", "Start monitoring first before applying VRAM settings.")
            return
        bridge = self._active_bridge
        # Apply frequency
        try:
            freq = int(self.v_vram_freq.get())
            ok, msg = bridge_set_vram_freq(bridge, freq)
            if ok:
                self.log(f"VRAM max freq set to {freq} MHz", "change")
            else:
                self.log(f"VRAM freq set failed: {msg}", "error")
        except ValueError:
            self.log("Invalid VRAM frequency value", "error")
            return
        # Apply timing
        if self._vram_timing_supported:
            timing_name = self.v_vram_timing.get()
            timing_idx = next((k for k, v in TIMING_NAMES.items() if v == timing_name), 0)
            ok, msg = bridge_set_vram_timing(bridge, timing_idx)
            if ok:
                self.log(f"VRAM memory timing set to {timing_name}", "change")
            else:
                self.log(f"VRAM timing set failed: {msg}", "warn")

    def _reset_vram(self):
        """Reset VRAM to defaults by resetting factory tuning."""
        if not hasattr(self, '_active_bridge'):
            messagebox.showwarning("Not Running", "Start monitoring first.")
            return
        bridge = self._active_bridge
        rc, out, err = _run(bridge, ["--set-auto"])
        if rc == 0:
            self.log("VRAM reset to factory defaults", "info")
            self._load_vram_state(bridge)
        else:
            self.log(f"Reset failed: {err or out}", "error")

    def _apply_power(self):
        if not hasattr(self, '_active_bridge'):
            messagebox.showwarning("Not Running", "Start monitoring first before applying power settings.")
            return
        bridge = self._active_bridge
        try:
            pct = int(self.v_power_limit.get())
        except ValueError:
            self.log("Invalid power limit value", "error")
            return
        ok, msg = bridge_set_power_limit(bridge, pct)
        if ok:
            self.log(f"Power limit set to {pct:+d}% of TDP", "change")
        else:
            self.log(f"Power limit set failed: {msg}", "error")

    def _reset_power(self):
        if not hasattr(self, '_active_bridge'):
            messagebox.showwarning("Not Running", "Start monitoring first.")
            return
        ok, msg = bridge_set_power_limit(self._active_bridge, 0)
        if ok:
            self.log("Power limit reset to stock (0%)", "info")
            self.v_power_limit.set("0")
        else:
            self.log(f"Power reset failed: {msg}", "error")

    def _load_vram_state(self, bridge):
        """Query VRAM state from bridge and update UI fields."""
        info = bridge_vram_info(bridge)
        if not info or info.get("VRAM_TUNING_SUPPORTED", "0") == "0":
            return
        # Update frequency
        freq_range = info.get("VRAM_FREQ_RANGE", "")
        if freq_range:
            try:
                parts = freq_range.split(",")[0].split("-")
                self._vram_freq_min = int(parts[0])
                self._vram_freq_max = int(parts[1])
                self._vram_freq_range_lbl.configure(
                    text=f"{self._vram_freq_min}–{self._vram_freq_max} MHz")
            except Exception:
                pass
        if "VRAM_MAX_FREQ" in info:
            self.v_vram_freq.set(info["VRAM_MAX_FREQ"])
        elif "VRAM_DEFAULT_FREQ" in info:
            self.v_vram_freq.set(info["VRAM_DEFAULT_FREQ"])
        # Update timing
        timing_supported = info.get("VRAM_TIMING_SUPPORTED", "0") == "1"
        self._vram_timing_supported = timing_supported
        if timing_supported:
            current = info.get("VRAM_CURRENT_TIMING", "0:Default")
            timing_name = current.split(":")[-1] if ":" in current else "Default"
            display_name = {"DEFAULT": "Default", "FAST": "Fast", "FAST_L2": "Fast L2",
                "AUTOMATIC": "Automatic", "LEVEL_1": "Level 1", "LEVEL_2": "Level 2"}.get(timing_name, "Default")
            self.v_vram_timing.set(display_name)
            self._vram_timing_lbl.configure(text="supported", fg=C["green"])
            self._timing_menu.configure(state="normal")
        else:
            self._vram_timing_lbl.configure(text="not supported", fg=C["dim"])
            self._timing_menu.configure(state="disabled")

    def _load_power_state(self, bridge):
        """Query power limit state and update UI."""
        info = bridge_power_info(bridge)
        if not info or info.get("POWER_TUNING_SUPPORTED", "0") == "0":
            self._power_range_lbl.configure(text="not supported")
            return
        pwr_range = info.get("POWER_LIMIT_RANGE", "")
        if pwr_range:
            try:
                parts = pwr_range.split("-")
                lo, hi = int(parts[0]), int(parts[1])
                self._power_min, self._power_max = lo, hi
                self._power_range_lbl.configure(text=f"{lo:+d}% to {hi:+d}%")
            except Exception:
                pass
        if "POWER_LIMIT_PCT" in info:
            self.v_power_limit.set(info["POWER_LIMIT_PCT"])

    def _graph(self, parent):
        f = tk.LabelFrame(parent, text=" Core Clock History ", font=("Segoe UI", 9, "bold"),
            bg=C["bg"], fg=C["dim"], bd=1, relief="solid")
        f.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self.canvas = tk.Canvas(f, bg=C["gbg"], bd=0, highlightthickness=0, height=170)
        self.canvas.pack(fill="both", expand=True, padx=4, pady=4)

    def _log(self, parent):
        f = tk.LabelFrame(parent, text=" Session Log ", font=("Segoe UI", 9, "bold"),
            bg=C["bg"], fg=C["dim"], bd=1, relief="solid")
        f.pack(fill="both", expand=True, padx=4)
        sb = tk.Scrollbar(f, bg=C["bg2"])
        sb.pack(side="right", fill="y")
        self.log_txt = tk.Text(f, height=8, font=("Consolas", 8),
            bg=C["bg2"], fg=C["text"], insertbackground=C["text"],
            relief="flat", state="disabled", yscrollcommand=sb.set)
        self.log_txt.pack(fill="both", expand=True, padx=2, pady=2)
        sb.config(command=self.log_txt.yview)
        self.log_txt.tag_configure("info", foreground=C["dim"])
        self.log_txt.tag_configure("change", foreground=C["green"])
        self.log_txt.tag_configure("warn", foreground=C["yellow"])
        self.log_txt.tag_configure("error", foreground=C["red"])
        self.log_txt.tag_configure("ts", foreground="#444466")

    # ── Logging ────────────────────────────────────────────────────────────────

    def log(self, msg, level="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_txt.configure(state="normal")
        self.log_txt.insert("end", f"[{ts}] ", "ts")
        self.log_txt.insert("end", msg + "\n", level)
        self.log_txt.configure(state="disabled")
        self.log_txt.see("end")

    # ── Config helpers ─────────────────────────────────────────────────────────

    def _ui_cfg(self):
        cfg = dict(self.cfg)
        cfg["bridge_path"] = self.v_bridge.get()
        try: cfg["poll_interval_sec"] = float(self.v_poll.get())
        except: pass
        try: cfg["hysteresis_count"] = int(self.v_hyst.get())
        except: pass
        try: cfg["idle_voltage_offset_mv"] = int(self.v_idle.get())
        except: pass
        cfg["thresholds"] = []
        for cv, mv_v in self.th_rows:
            try:
                cfg["thresholds"].append({
                    "clock_mhz": int(cv.get()),
                    "voltage_offset_mv": int(mv_v.get())
                })
            except: pass
        return cfg

    def _save_cfg(self):
        p = filedialog.asksaveasfilename(defaultextension=".json",
            filetypes=[("JSON", "*.json")], initialfile="claw_volt_config.json")
        if p:
            with open(p, "w") as f: json.dump(self._ui_cfg(), f, indent=2)
            self.log(f"Config saved: {p}")

    def _open_crash_log(self):
        """Open the crash log file in the default text editor."""
        log_path = os.path.join(APP_DIR, "claw_volt_crashes.log")
        if not os.path.exists(log_path):
            messagebox.showinfo("No Log Yet",
                "No crash log file found yet.\n"
                "Crashes will be logged to:\n" + log_path)
            return
        try:
            os.startfile(log_path)
        except Exception as e:
            messagebox.showerror("Open Failed", f"Could not open log file:\n{e}\n\nPath: {log_path}")

    def _load_cfg(self):
        p = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if not p:
            return
        try:
            with open(p) as f2:
                cfg = json.load(f2)

            # Update scalar settings fields
            self.cfg = cfg
            self.v_bridge.set(cfg.get("bridge_path", "adlx_bridge.exe"))
            self.v_poll.set(str(cfg.get("poll_interval_sec", 0.5)))
            self.v_hyst.set(str(cfg.get("hysteresis_count", 2)))
            self.v_idle.set(str(cfg.get("idle_voltage_offset_mv", -100)))

            # Destroy all existing threshold rows and rebuild from loaded config
            for widget in self.th_frame.winfo_children():
                widget.destroy()
            self.th_rows = []
            for t in cfg.get("thresholds", []):
                self._add_threshold_row(t["clock_mhz"], t["voltage_offset_mv"])

            self.log(f"Config loaded: {os.path.basename(p)}", "info")

        except Exception as e:
            messagebox.showerror("Load Error", str(e))

    # ── Graph ──────────────────────────────────────────────────────────────────

    def _draw_graph(self):
        cv = self.canvas
        cv.delete("all")
        W = cv.winfo_width()
        H = cv.winfo_height()
        if W < 20 or H < 20:
            return

        pl, pr, pt, pb = 50, 16, 8, 22
        cv.create_rectangle(pl, pt, W - pr, H - pb, fill=C["gbg"], outline=C["ggrid"])

        clocks = list(self.clock_history)
        if not clocks:
            cv.create_text(W // 2, H // 2, text="Waiting for data...",
                fill=C["dim"], font=("Segoe UI", 9))
            return

        mn = max(0, min(clocks) - 150)
        mx = max(clocks) + 150
        rng = max(mx - mn, 1)
        gw = W - pl - pr
        gh = H - pt - pb

        def gy(v):
            return pt + (1 - (v - mn) / rng) * gh

        # Threshold lines — read cached config, not UI (safe from any thread)
        cfg = self._ui_cfg()
        for i, t in enumerate(sorted(cfg["thresholds"], key=lambda x: x["clock_mhz"])):
            freq = t["clock_mhz"]
            if mn <= freq <= mx:
                y = gy(freq)
                col = C["th"][i % len(C["th"])]
                cv.create_line(pl, y, W - pr, y, fill=col, dash=(4, 4), width=1)
                cv.create_text(pl - 4, y, text=str(freq), fill=col,
                    font=("Consolas", 7), anchor="e")

        # Y grid labels
        step = 200 if (mx - mn) > 600 else 100
        for tick in range(int(mn // step) * step, int(mx // step + 2) * step, step):
            if mn <= tick <= mx:
                y = gy(tick)
                cv.create_line(pl, y, pl + 4, y, fill=C["ggrid"])
                cv.create_text(pl - 4, y, text=str(tick), fill=C["dim"],
                    font=("Consolas", 7), anchor="e")

        n = len(clocks)
        if n < 2:
            return

        # Build points
        pts = [(pl + (i / (n - 1)) * gw, gy(v)) for i, v in enumerate(clocks)]

        # Filled area
        poly = [(pl, H - pb)] + pts + [(pts[-1][0], H - pb)]
        cv.create_polygon([v for p in poly for v in p], fill="#00223a", outline="")

        # Line
        cv.create_line([v for p in pts for v in p],
            fill=C["cyan"], width=2, smooth=True)

        # Latest dot + label
        lx, ly = pts[-1]
        cv.create_oval(lx - 4, ly - 4, lx + 4, ly + 4, fill=C["green"], outline="")
        cv.create_text(min(lx + 8, W - pr - 60), ly,
            text=f"{clocks[-1]} MHz",
            fill=C["green"], font=("Consolas", 8, "bold"), anchor="w")

        cv.create_text(W // 2, H - 6, text=f"← {n} samples",
            fill=C["dim"], font=("Segoe UI", 7))

    # ── GUI refresh loop — runs every 250ms regardless of monitoring state ─────

    def _schedule_refresh(self):
        self._refresh()
        # Always reschedule — never stops while window is open
        self.root.after(250, self._schedule_refresh)

    def _refresh(self):
        """Update all live labels and redraw graph. Called every 250ms on main thread."""
        if self.last_clock:
            self.var_clock.set(f"{self.last_clock} MHz")

        if self.last_vram_clock:
            self.var_vram_clock.set(f"{self.last_vram_clock} MHz")
        else:
            self.var_vram_clock.set("--- MHz")

        if self.current_offset is not None:
            self.var_voltage.set(f"{self.current_offset:+d} mV")

        if self.pending_offset is not None:
            self.var_pending.set(
                f"{self.pending_offset:+d} mV "
                f"({self.pending_count}/{self.cfg.get('hysteresis_count', 2)})"
            )
        else:
            self.var_pending.set("stable")

        self.var_changes.set(str(self.stat_changes))
        if self.crash_logger:
            self.var_crashes.set(str(self.crash_logger.get_crash_count()))
        self._draw_graph()

    # ── Monitor thread ─────────────────────────────────────────────────────────

    def _start(self):
        if self.running:
            return
        cfg = self._ui_cfg()
        bridge = _resolve(cfg["bridge_path"])
        cfg["bridge_path"] = bridge # store resolved path

        if not os.path.exists(bridge):
            messagebox.showerror("Bridge Not Found",
                f"Cannot find:\n{bridge}\n\nCheck the bridge path in Settings.")
            return

        # Disable button immediately so user can't double-click
        self.btn_start.configure(state="disabled", bg=C["bg2"], fg=C["dim"])
        self.lbl_status.configure(text="Status: connecting...", fg=C["yellow"])
        self.log("Connecting to GPU...", "info")

        # Run connection + startup in background so GUI doesn't freeze
        def _connect():
            info = bridge_info(bridge)

            if not info:
                self.root.after(0, lambda: (
                    messagebox.showerror("ADLX Error",
                        "Bridge failed to initialize.\nEnsure AMD Adrenalin is installed and run as Admin."),
                    self.btn_start.configure(state="normal", bg=C["green"], fg="black"),
                    self.lbl_status.configure(text="Status: idle", fg=C["dim"]),
                ))
                return

            gpu_name = info.get("GPU_NAME", "Unknown GPU")

            if info.get("TUNING_MANUAL_GFX_SUPPORTED", "0") == "0":
                self.root.after(0, lambda: (
                    messagebox.showerror("Not Supported", "Manual GFX tuning not supported on this GPU."),
                    self.btn_start.configure(state="normal", bg=C["green"], fg="black"),
                    self.lbl_status.configure(text="Status: idle", fg=C["dim"]),
                ))
                return

            # All good — continue startup on main thread
            self.root.after(0, lambda: self._start_monitoring(cfg, bridge, gpu_name))

        threading.Thread(target=_connect, daemon=True).start()

    def _start_monitoring(self, cfg, bridge, gpu_name):
        """Called on main thread after connection confirmed."""
        self.lbl_gpu.configure(text=gpu_name)
        self.lbl_status.configure(text="Status: active", fg=C["green"])
        self.log(f"Connected: {gpu_name}", "info")
        self._active_bridge = bridge # store for VRAM/power apply buttons

        # Start crash logger
        self.crash_logger = CrashLogger(cfg, log_path=os.path.join(APP_DIR, "claw_volt_crashes.log"))
        self.crash_logger.on_log_entry = lambda msg, lvl: self.root.after(0, lambda m=msg, l=lvl: self.log(m, l))

        def _on_crash(n, code, expl):
            def _show():
                self.log(f"⚠ CRASH #{n} — {code}", "error")
                self.log(expl[:120], "warn")
                self.var_crashes.set(str(self.crash_logger.get_crash_count()))
                messagebox.showwarning(
                    f"GPU Crash #{n} Detected",
                    f"Reason: {code}\n\n{expl[:300]}\n\nFull report written to:\nclaw_volt_crashes.log\n\nClick '🪵 Log' to open."
                )
            self.root.after(0, _show)

        self.crash_logger.on_crash_detected = _on_crash
        self.crash_logger.write_session_header(gpu_name, cfg)

        # Check if previous session ended abnormally (post-mortem detection)
        # Must happen BEFORE start() writes the new heartbeat
        self.crash_logger.check_previous_session()

        self.crash_logger.start()

        # Load VRAM and power state into UI (non-blocking — small timeout)
        def _load_hw_state():
            self._load_vram_state(bridge)
            self._load_power_state(bridge)
        threading.Thread(target=_load_hw_state, daemon=True).start()

        self.cfg = cfg
        self.running = True
        self.current_offset = None
        self.pending_offset = None
        self.pending_count = 0
        self.stat_changes = 0
        self.last_clock = 0
        self.last_vram_clock = 0
        self.clock_history.clear()

        self.btn_stop.configure(state="normal", bg=C["red"], fg="white")

        self.thread = threading.Thread(target=self._monitor, daemon=True)
        self.thread.start()

    def _stop(self):
        if not self.running:
            return
        self.running = False

    def _monitor(self):
        """Background thread — polls clock, applies voltage, feeds crash logger."""
        cfg = self.cfg
        bridge = cfg["bridge_path"]
        poll = cfg["poll_interval_sec"]
        hyst = cfg["hysteresis_count"]

        current_offset = None
        pending_offset = None
        pending_count = 0
        fail_count = 0 # consecutive bridge failures
        MAX_FAILS = 10 # log an error after this many in a row

        # Log the resolved bridge path so it's visible in the session log
        self.root.after(0, lambda: self.log(f"Bridge: {bridge}", "info"))

        try:
            while self.running:
                t0 = time.time()

                # ── Read core clock ───────────────────────────────────────────────
                rc, out, err = _run(bridge, ["--get-clock"])
                if rc == 0 and out.startswith("CLOCK:"):
                    try:
                        clock = int(out.split(":")[1])
                    except ValueError:
                        clock = None
                else:
                    clock = None

                if clock is None:
                    fail_count += 1
                    if fail_count == 1 or fail_count % MAX_FAILS == 0:
                        msg = err or out or "No output from bridge"
                        self.root.after(0, lambda m=msg, n=fail_count:
                            self.log(f"Clock read failed (×{n}): {m}", "error"))
                    time.sleep(poll)
                    continue

                # ── Successful read ──────────────────────────────────────────
                if fail_count > 0:
                    self.root.after(0, lambda: self.log("Clock reads recovered", "info"))
                    fail_count = 0

                self.clock_history.append(clock)
                self.last_clock = clock

                # ── Read VRAM clock (non-blocking, best effort) ───────────────────
                vram_info = bridge_vram_info(bridge)
                if vram_info and "VRAM_MAX_FREQ" in vram_info:
                    try:
                        self.last_vram_clock = int(vram_info["VRAM_MAX_FREQ"])
                    except:
                        pass

                want = target_offset(clock, cfg)

                # Feed crash logger
                if self.crash_logger:
                    self.crash_logger.record(clock, current_offset)

                # ── Voltage logic ────────────────────────────────────────────
                if want != current_offset:
                    if want == pending_offset:
                        pending_count += 1
                    else:
                        pending_offset = want
                        pending_count = 1

                    self.pending_offset = pending_offset
                    self.pending_count = pending_count

                    if pending_count >= hyst:
                        # Apply voltage
                        rc2, out2, err2 = _run(bridge, ["--set-voltage", str(want)])
                        ok = rc2 == 0 and "VOLTAGE_OFFSET_APPLIED" in out2

                        if ok:
                            prev = current_offset
                            current_offset = want
                            self.current_offset = want
                            self.stat_changes += 1
                            pending_offset = None
                            pending_count = 0
                            self.pending_offset = None
                            self.pending_count = 0
                            if self.crash_logger:
                                self.crash_logger.on_voltage_changed(prev, want)
                            p, w, cl = prev, want, clock
                            self.root.after(0, lambda p=p, w=w, cl=cl:
                                self.log(
                                    f"Clock {cl} MHz "
                                    f"{'init' if p is None else f'{p:+d}mV'} → {w:+d}mV",
                                    "change"
                                ))
                        else:
                            detail = err2 or out2 or "no output"
                            self.root.after(0, lambda cl=clock, d=detail:
                                self.log(f"Voltage set failed at {cl} MHz: {d}", "error"))
                else:
                    pending_offset = None
                    pending_count = 0
                    self.pending_offset = None
                    self.pending_count = 0

                elapsed = time.time() - t0
                time.sleep(max(0, poll - elapsed))

        except Exception as e:
            # Catch any unhandled exception in the loop so it's visible in the log
            import traceback
            tb = traceback.format_exc()
            self.root.after(0, lambda tb=tb:
                self.log(f"Monitor thread crashed: {tb}", "error"))

        # Cleanup
        try:
            _run(bridge, ["--set-auto"])
        except Exception:
            pass
        self.root.after(0, self._stopped)

    def _stopped(self):
        self.lbl_status.configure(text="Status: stopped", fg=C["dim"])
        self.btn_start.configure(state="normal", bg=C["green"], fg="black")
        self.btn_stop.configure(state="disabled", bg=C["bg2"], fg=C["dim"])
        self.log("Stopped. GPU restored to auto tuning.", "warn")
        if self.crash_logger:
            crash_count = self.crash_logger.get_crash_count()
            self.crash_logger.stop()
            self.crash_logger.write_session_footer(crash_count)
            self.log(f"Crash log: {self.crash_logger.get_log_path()}", "info")
            self.crash_logger = None

    # ── Close ──────────────────────────────────────────────────────────────────

    def _on_close(self):
        self.running = False
        # Give monitor thread 1s to clean up, then destroy
        self.root.after(800, self.root.destroy)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
