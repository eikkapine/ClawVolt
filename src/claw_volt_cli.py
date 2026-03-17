#!/usr/bin/env python3
"""
ClawVolt CLI - Dynamic GPU Voltage Controller
Reads live GPU core clock via ADLX bridge and applies voltage offsets
based on configurable clock thresholds.

Target GPU: AMD RX 9070 XT (RDNA4, Windows)

Requirements:
  - adlx_bridge.exe compiled and present in same directory (or PATH)
  - AMD Adrenalin driver installed
  - Python 3.8+

Usage:
  python claw_volt_cli.py                    # Start with default thresholds
  python claw_volt_cli.py --config my.json   # Use custom threshold config
  python claw_volt_cli.py --info             # Print GPU info and exit
  python claw_volt_cli.py --reset            # Reset to auto tuning mode and exit
"""

import subprocess
import time
import sys
import os
import json
import argparse
import signal
from datetime import datetime
from typing import Optional
from crash_logger import CrashLogger

# ===========================================================================
# Configuration defaults
# These match the values you described. Edit here or use --config <file>.
# ===========================================================================
DEFAULT_CONFIG = {
    # Path to the compiled ADLX bridge executable
    "bridge_path": "adlx_bridge.exe",

    # How often to poll the GPU clock (seconds)
    "poll_interval_sec": 0.4,

    # Number of consecutive reads that must agree before changing voltage
    # Prevents flickering when the clock is bouncing around a threshold
    "hysteresis_count": 2,

    # Voltage thresholds: list of [min_clock_mhz, voltage_offset_mv]
    # Sorted descending — first match wins
    # If clock is below ALL thresholds, the last entry's offset is used (lowest offset = least aggressive)
    "thresholds": [
        { "clock_mhz": 3200, "voltage_offset_mv": -120 },
        { "clock_mhz": 3100, "voltage_offset_mv": -160 },
        { "clock_mhz": 3000, "voltage_offset_mv": -140 },
    ],

    # Fallback offset applied when clock is below all thresholds (idle/low load)
    "idle_voltage_offset_mv": -100,

    # Enable verbose output
    "verbose": True,
}

# ANSI colors for terminal output
class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    CYAN    = "\033[96m"
    RED     = "\033[91m"
    DIM     = "\033[2m"
    BLUE    = "\033[94m"

# ===========================================================================
# Bridge communication
# ===========================================================================

def run_bridge(bridge_path: str, args: list, timeout: float = 3.0) -> tuple[int, str, str]:
    """
    Run the ADLX bridge with the given args.
    Returns (returncode, stdout, stderr)
    """
    try:
        result = subprocess.run(
            [bridge_path] + args,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Bridge call timed out"
    except FileNotFoundError:
        return -1, "", f"Bridge not found: {bridge_path}"
    except Exception as e:
        return -1, "", str(e)


def get_clock(bridge_path: str) -> Optional[int]:
    """Read current GPU core clock. Returns MHz or None on failure."""
    rc, out, err = run_bridge(bridge_path, ["--get-clock"])
    if rc == 0 and out.startswith("CLOCK:"):
        try:
            return int(out.split(":")[1])
        except ValueError:
            pass
    return None


def set_voltage_offset(bridge_path: str, offset_mv: int) -> bool:
    """Apply voltage offset. Returns True on success."""
    rc, out, err = run_bridge(bridge_path, ["--set-voltage", str(offset_mv)])
    return rc == 0 and "VOLTAGE_OFFSET_APPLIED" in out


def reset_to_auto(bridge_path: str) -> bool:
    """Switch GPU back to auto tuning mode."""
    rc, out, err = run_bridge(bridge_path, ["--set-auto"])
    return rc == 0


def switch_to_manual(bridge_path: str) -> bool:
    """Switch GPU to manual tuning mode."""
    rc, out, err = run_bridge(bridge_path, ["--set-manual"])
    return rc == 0


def get_gpu_info(bridge_path: str) -> dict:
    """Get GPU info from bridge --info output."""
    rc, out, err = run_bridge(bridge_path, ["--info"], timeout=5.0)
    info = {}
    if rc == 0:
        for line in out.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                info[key] = val
    return info

# ===========================================================================
# Threshold logic
# ===========================================================================

def get_target_offset(clock_mhz: int, config: dict) -> int:
    """
    Determine the target voltage offset for a given clock.
    Thresholds are evaluated highest-first; first match wins.
    """
    thresholds = sorted(
        config["thresholds"],
        key=lambda t: t["clock_mhz"],
        reverse=True
    )
    for t in thresholds:
        if clock_mhz >= t["clock_mhz"]:
            return t["voltage_offset_mv"]
    return config["idle_voltage_offset_mv"]

# ===========================================================================
# Main controller loop
# ===========================================================================

class ClawVoltCLI:
    def __init__(self, config: dict):
        self.cfg = config
        self.bridge = config["bridge_path"]
        self.running = False
        self.current_offset = None       # Last applied offset
        self.pending_offset = None       # Offset we want to apply
        self.pending_count = 0           # Hysteresis counter
        self.stats = {
            "reads": 0,
            "failures": 0,
            "changes": 0,
            "start_time": time.time()
        }
        self.crash_logger: Optional[CrashLogger] = None
        # Register Ctrl+C handler for clean shutdown
        signal.signal(signal.SIGINT, self._handle_exit)
        signal.signal(signal.SIGTERM, self._handle_exit)

    def _handle_exit(self, sig, frame):
        print(f"\n{C.YELLOW}Caught exit signal — restoring auto tuning mode...{C.RESET}")
        self.running = False

    def log(self, msg: str, level: str = "info"):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        prefix = {
            "info":    f"{C.DIM}[{ts}]{C.RESET} {C.CYAN}INFO{C.RESET}",
            "change":  f"{C.DIM}[{ts}]{C.RESET} {C.GREEN}VOLT{C.RESET}",
            "warn":    f"{C.DIM}[{ts}]{C.RESET} {C.YELLOW}WARN{C.RESET}",
            "error":   f"{C.DIM}[{ts}]{C.RESET} {C.RED}ERR {C.RESET}",
        }.get(level, f"[{ts}]")
        print(f"{prefix}  {msg}")

    def print_banner(self):
        print(f"""
{C.BOLD}{C.CYAN}╔══════════════════════════════════════════════════════╗
║           ClawVolt — Dynamic GPU Voltage Control     ║
║           AMD RX 9070 XT (RDNA4) · Windows · ADLX   ║
╚══════════════════════════════════════════════════════╝{C.RESET}
""")
        print(f"  {C.BOLD}Bridge:{C.RESET}         {self.bridge}")
        print(f"  {C.BOLD}Poll interval:{C.RESET}  {self.cfg['poll_interval_sec']}s")
        print(f"  {C.BOLD}Hysteresis:{C.RESET}     {self.cfg['hysteresis_count']} consecutive reads")
        print(f"\n  {C.BOLD}Thresholds:{C.RESET}")
        for t in sorted(self.cfg["thresholds"], key=lambda x: x["clock_mhz"], reverse=True):
            print(f"    ≥ {t['clock_mhz']:>5} MHz  →  {t['voltage_offset_mv']:>+5} mV")
        print(f"    < {self.cfg['thresholds'][-1]['clock_mhz']:>4} MHz  →  {self.cfg['idle_voltage_offset_mv']:>+5} mV  (idle)")
        print()

    def check_bridge(self) -> bool:
        """Verify bridge is accessible and ADLX initializes OK."""
        self.log(f"Checking bridge at: {self.bridge}")
        info = get_gpu_info(self.bridge)
        if not info:
            self.log(f"Bridge not responding or ADLX failed to init", "error")
            self.log(f"Ensure adlx_bridge.exe is in this directory and AMD drivers are installed", "error")
            return False
        gpu_name = info.get("GPU_NAME", "unknown")
        manual_supported = info.get("TUNING_MANUAL_GFX_SUPPORTED", "0")
        self.log(f"GPU detected: {C.BOLD}{gpu_name}{C.RESET}")
        self.log(f"Manual GFX tuning supported: {manual_supported}")
        if manual_supported == "0":
            self.log("Manual GFX tuning not supported on this GPU!", "error")
            return False
        return True

    def start(self):
        self.print_banner()

        if not self.check_bridge():
            return 1

        # Switch to manual tuning mode
        self.log("Switching to manual GFX tuning mode...")
        if not switch_to_manual(self.bridge):
            self.log("Failed to switch to manual mode", "error")
            return 1
        self.log(f"Manual mode {C.GREEN}active{C.RESET}")

        # Start crash logger
        gpu_name = get_gpu_info(self.bridge).get("GPU_NAME", "Unknown GPU")
        self.crash_logger = CrashLogger(self.cfg, log_path="claw_volt_crashes.log")
        self.crash_logger.on_log_entry = self.log

        def _on_crash(n, code, expl):
            print(f"\n{C.RED}{'━'*60}{C.RESET}")
            print(f"{C.RED}  ⚠  CRASH #{n} DETECTED — {code}{C.RESET}")
            print(f"{C.YELLOW}  {expl[:110]}{C.RESET}")
            print(f"{C.RED}{'━'*60}{C.RESET}\n")

        self.crash_logger.on_crash_detected = _on_crash
        self.crash_logger.write_session_header(gpu_name, self.cfg)
        self.crash_logger.start()

        # Get initial clock and apply initial offset
        initial_clock = get_clock(self.bridge)
        if initial_clock is None:
            self.log("Failed to read initial clock", "error")
            reset_to_auto(self.bridge)
            return 1

        initial_offset = get_target_offset(initial_clock, self.cfg)
        self.log(f"Initial clock: {C.BOLD}{initial_clock}{C.RESET} MHz → applying {C.BOLD}{initial_offset:+d}mV{C.RESET}")
        if not set_voltage_offset(self.bridge, initial_offset):
            self.log("Failed to apply initial voltage offset", "error")
            reset_to_auto(self.bridge)
            return 1
        self.current_offset = initial_offset
        self.stats["changes"] += 1

        print(f"\n  {C.DIM}Press Ctrl+C to stop and restore auto tuning{C.RESET}\n")
        self.running = True

        # ── Main loop ────────────────────────────────────────────────────
        while self.running:
            loop_start = time.time()

            clock = get_clock(self.bridge)
            self.stats["reads"] += 1

            if clock is None:
                self.stats["failures"] += 1
                if self.cfg["verbose"]:
                    self.log("Clock read failed", "warn")
                time.sleep(self.cfg["poll_interval_sec"])
                continue

            # Feed crash logger flight recorder
            if self.crash_logger:
                self.crash_logger.record(clock, self.current_offset)

            target_offset = get_target_offset(clock, self.cfg)

            if target_offset != self.current_offset:
                # Hysteresis: only switch if we've seen this target N times in a row
                if target_offset == self.pending_offset:
                    self.pending_count += 1
                else:
                    self.pending_offset = target_offset
                    self.pending_count = 1

                if self.pending_count >= self.cfg["hysteresis_count"]:
                    # Commit the change
                    ok = set_voltage_offset(self.bridge, target_offset)
                    if ok:
                        self.log(
                            f"Clock {C.BOLD}{clock:>5}{C.RESET} MHz  "
                            f"{C.DIM}{self.current_offset:>+5}mV{C.RESET} → "
                            f"{C.GREEN}{target_offset:>+5}mV{C.RESET}  "
                            f"({'+' if target_offset > self.current_offset else ''}{target_offset - self.current_offset}mV)",
                            "change"
                        )
                        if self.crash_logger:
                            self.crash_logger.on_voltage_changed(self.current_offset, target_offset)
                        self.current_offset = target_offset
                        self.stats["changes"] += 1
                        self.pending_count = 0
                        self.pending_offset = None
                    else:
                        self.log(f"Voltage set failed at {clock} MHz", "error")
                else:
                    if self.cfg["verbose"]:
                        self.log(
                            f"Clock {C.BOLD}{clock:>5}{C.RESET} MHz  "
                            f"holding {C.BOLD}{self.current_offset:>+5}mV{C.RESET}  "
                            f"(pending {target_offset:+d}mV, "
                            f"hysteresis {self.pending_count}/{self.cfg['hysteresis_count']})",
                            "info"
                        )
            else:
                # Offset matches, reset pending state
                self.pending_offset = None
                self.pending_count = 0
                if self.cfg["verbose"]:
                    self.log(
                        f"Clock {C.BOLD}{clock:>5}{C.RESET} MHz  "
                        f"voltage {C.CYAN}{self.current_offset:>+5}mV{C.RESET}  "
                        f"{C.DIM}(stable){C.RESET}",
                        "info"
                    )

            # Sleep for the remainder of the poll interval
            elapsed = time.time() - loop_start
            sleep_time = max(0, self.cfg["poll_interval_sec"] - elapsed)
            time.sleep(sleep_time)

        # ── Cleanup ────────────────────────────────────────────────────
        print()
        self.log("Restoring GPU to auto tuning mode...")
        if reset_to_auto(self.bridge):
            self.log(f"Auto mode {C.GREEN}restored{C.RESET}")
        else:
            self.log("Failed to restore auto mode — please reset manually in Adrenalin", "warn")

        # Stop crash logger
        crash_count = 0
        if self.crash_logger:
            crash_count = self.crash_logger.get_crash_count()
            self.crash_logger.stop()
            self.crash_logger.write_session_footer(crash_count)
            self.log(f"Crash log: {self.crash_logger.get_log_path()}")

        # Print session stats
        uptime = time.time() - self.stats["start_time"]
        print(f"""
  {C.DIM}Session summary:{C.RESET}
    Runtime:   {uptime:.0f}s
    Reads:     {self.stats['reads']}
    Failures:  {self.stats['failures']}
    Changes:   {self.stats['changes']}
    Crashes:   {crash_count}
""")
        return 0


# ===========================================================================
# Entry point
# ===========================================================================

def load_config(path: str) -> dict:
    with open(path, "r") as f:
        user_cfg = json.load(f)
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(user_cfg)
    return cfg


def main():
    parser = argparse.ArgumentParser(
        description="ClawVolt — Dynamic GPU Voltage Controller for AMD RX 9070 XT"
    )
    parser.add_argument(
        "--config", "-c",
        help="Path to JSON config file",
        default=None
    )
    parser.add_argument(
        "--bridge",
        help="Path to adlx_bridge.exe",
        default=None
    )
    parser.add_argument(
        "--info",
        action="store_true",
        help="Print GPU info and exit"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset GPU to auto tuning mode and exit"
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress per-poll verbose output"
    )
    parser.add_argument(
        "--save-config",
        help="Save current config to a JSON file and exit"
    )

    args = parser.parse_args()

    # Load config
    if args.config:
        try:
            cfg = load_config(args.config)
        except Exception as e:
            print(f"Error loading config: {e}", file=sys.stderr)
            return 1
    else:
        cfg = dict(DEFAULT_CONFIG)

    # Override with CLI args
    if args.bridge:
        cfg["bridge_path"] = args.bridge
    if args.quiet:
        cfg["verbose"] = False

    # Save config template
    if args.save_config:
        with open(args.save_config, "w") as f:
            json.dump(cfg, f, indent=2)
        print(f"Config saved to: {args.save_config}")
        return 0

    bridge = cfg["bridge_path"]

    # Info mode
    if args.info:
        info = get_gpu_info(bridge)
        if not info:
            print("Failed to get GPU info. Is adlx_bridge.exe available?", file=sys.stderr)
            return 1
        print("\nGPU Information:")
        for k, v in info.items():
            print(f"  {k:40s} {v}")
        print()
        return 0

    # Reset mode
    if args.reset:
        print("Resetting GPU to auto tuning mode...")
        if reset_to_auto(bridge):
            print("Done — GPU is back in auto tuning mode.")
        else:
            print("Failed. Check that adlx_bridge.exe is available.", file=sys.stderr)
            return 1
        return 0

    # Run the controller
    controller = ClawVoltCLI(cfg)
    return controller.start()


if __name__ == "__main__":
    sys.exit(main())
