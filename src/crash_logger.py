"""
ClawVolt Crash Logger — Post-Mortem Architecture
=================================================

The core insight: when a GPU TDR happens, the process using the GPU (ClawVolt)
is typically killed by the OS as part of driver reset. The crash watcher thread
never gets to fire because the process is already dead.

Solution — two-phase approach:

PHASE 1 — Live: write a rolling telemetry file to disk every poll cycle.
  Keeps the last 150 seconds of clock/voltage readings on disk at all times.
  Also writes a "session alive" heartbeat file.

PHASE 2 — Post-mortem: on every startup, check if the previous session
  ended cleanly (heartbeat file deleted on exit). If not, query the Windows
  Event Log for TDR/crash events that occurred between last session start
  and now, then build a full crash report from the saved telemetry.

This way a crash is always caught on the NEXT launch regardless of how
the previous session died.
"""

import os
import sys
import time
import json
import threading
import subprocess
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime
from typing import Optional

# ── File paths (set via init, default to app dir) ─────────────────────────────
_DEFAULT_LOG   = "claw_volt_crashes.log"
_TELEMETRY_FILE = "claw_volt_telemetry.json"   # rolling telemetry ring buffer
_HEARTBEAT_FILE = "claw_volt_session.json"      # exists only while running

TELEMETRY_KEEP_SEC = 150   # how many seconds of history to keep on disk
MAX_TELEMETRY_ROWS = 300   # cap at 300 rows (~150s at 0.5s polling)

# ── Windows Event Log via pywin32 ─────────────────────────────────────────────
_EVTLOG_OK = False
try:
    import win32evtlog
    import pywintypes
    _EVTLOG_OK = True
except ImportError:
    pass


def _query_events(log_name: str, event_id: int, after_time: str = "",
                  max_events: int = 10) -> list[dict]:
    """
    Query Windows Event Log for a specific EventID using pywin32 EvtQuery API.

    Time filtering uses timediff(@SystemTime) — the correct Microsoft-documented
    XPath approach. If after_time is given, we calculate elapsed milliseconds
    since that time and use timediff to filter. Falls back to no time filter
    with manual post-filtering if the XPath fails for any reason.

    after_time: ISO 8601 UTC string e.g. "2026-03-17T23:10:04.000000000Z"
    Returns list of {EventID, TimeCreated, Message}.
    """
    if not _EVTLOG_OK:
        return []

    flags = win32evtlog.EvtQueryChannelPath | win32evtlog.EvtQueryReverseDirection

    # Calculate milliseconds since session start for timediff filter
    # timediff(@SystemTime) returns ms since the event — so events AFTER
    # session_start have timediff <= (now - session_start)
    ms_since = None
    if after_time:
        try:
            from datetime import datetime, timezone
            # Parse ISO 8601 — handle both Z and +00:00 suffix
            clean = after_time.rstrip("Z").split("+")[0]
            # Truncate nanoseconds to microseconds (Python max precision)
            if "." in clean:
                parts = clean.split(".")
                clean = parts[0] + "." + parts[1][:6]
            dt = datetime.fromisoformat(clean).replace(tzinfo=timezone.utc)
            ms_since = int((datetime.now(timezone.utc) - dt).total_seconds() * 1000)
            ms_since = max(0, ms_since)
        except Exception:
            ms_since = None

    # Build queries to try in order:
    # 1. With timediff time filter (most accurate)
    # 2. Last 24 hours as a wide safety net
    # 3. No filter at all (manual post-filter)
    queries = []
    if ms_since is not None:
        queries.append(
            f"*[System[(EventID={event_id}) and "
            f"TimeCreated[timediff(@SystemTime) <= {ms_since}]]]"
        )
    # Wide fallback: last 24 hours
    queries.append(
        f"*[System[(EventID={event_id}) and "
        f"TimeCreated[timediff(@SystemTime) <= 86400000]]]"
    )
    # Last resort: no time filter
    queries.append(f"*[System[(EventID={event_id})]]")

    for query in queries:
        try:
            handle = win32evtlog.EvtQuery(log_name, flags, query, None)
            raw_events = win32evtlog.EvtNext(handle, max_events, -1, 0)
            results = []
            for evt in raw_events:
                try:
                    xml_str = win32evtlog.EvtRender(evt, win32evtlog.EvtRenderEventXml)
                    d = _parse_event_xml(xml_str)
                    if d:
                        results.append(d)
                except Exception:
                    continue

            if results:
                # If this was the no-filter fallback, manually filter by after_time
                if after_time and "timediff" not in query:
                    # Normalise after_time for string comparison
                    after_cmp = after_time.rstrip("Z").split("+")[0]
                    if "." in after_cmp:
                        after_cmp = after_cmp.split(".")[0]
                    results = [r for r in results
                               if r.get("TimeCreated", "")[:19] >= after_cmp]
                return results

        except Exception:
            continue

    return []


def _parse_event_xml(xml_str: str) -> dict:
    ns = "http://schemas.microsoft.com/win/2004/08/events/event"
    try:
        root = ET.fromstring(xml_str)
        d = {}
        sys_el = root.find(f"{{{ns}}}System")
        if sys_el is not None:
            eid = sys_el.find(f"{{{ns}}}EventID")
            if eid is not None and eid.text:
                d["EventID"] = int(eid.text)
            tc = sys_el.find(f"{{{ns}}}TimeCreated")
            if tc is not None:
                d["TimeCreated"] = tc.get("SystemTime", "")
        ri = root.find(f"{{{ns}}}RenderingInfo")
        if ri is not None:
            m = ri.find(f"{{{ns}}}Message")
            if m is not None and m.text:
                d["Message"] = m.text.strip()
        if "Message" not in d:
            ed = root.find(f"{{{ns}}}EventData")
            if ed is not None:
                parts = [f"{c.get('Name','')}: {c.text}" for c in ed if c.text]
                d["Message"] = " | ".join(parts)
        return d if "EventID" in d and "TimeCreated" in d else {}
    except Exception:
        return {}


def _find_crash_events(session_start_iso: str) -> list[dict]:
    """Find all GPU crash related events after session_start_iso."""
    events = []
    # TDR recovery (most common for undervolt crash)
    events += _query_events("System", 4101, after_time=session_start_iso, max_events=5)
    # Kernel power / unexpected reboot
    events += _query_events("System", 41,   after_time=session_start_iso, max_events=3)
    # Unexpected shutdown
    events += _query_events("System", 6008, after_time=session_start_iso, max_events=3)
    # Sort by time
    events.sort(key=lambda e: e.get("TimeCreated", ""))
    return events


# ── Crash analysis ─────────────────────────────────────────────────────────────

def _analyse_crash(telemetry: list[dict], volt_changes: list[dict],
                   crash_iso: str, event_msg: str) -> tuple[str, str]:
    if not telemetry:
        return "NO_TELEMETRY", ("No telemetry was recorded before this crash. "
                                "This can happen if ClawVolt was killed immediately on driver reset.")

    latest = telemetry[-1]
    clock  = latest.get("clock_mhz", 0)
    volt   = latest.get("voltage_mv")
    temp   = latest.get("temp_c")
    hot    = latest.get("hotspot_c")

    if volt is None:
        volt = 0

    # Thermal
    if temp is not None and temp >= 90:
        return "THERMAL", (f"GPU temperature was {temp:.0f}°C at crash time. "
                           "Driver likely crashed due to thermal protection. "
                           "Check case airflow and thermal paste.")
    if hot is not None and hot >= 100:
        return "THERMAL_HOTSPOT", (f"GPU hotspot temperature was {hot:.0f}°C. "
                                   "Hotspot thermal shutdown is likely.")

    # Crash time as float for delta calculations
    try:
        from datetime import datetime as _dt
        crash_ts = _dt.fromisoformat(crash_iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        crash_ts = time.time()

    recent_changes = [c for c in volt_changes
                      if crash_ts - c.get("ts", 0) <= 15]

    if len(recent_changes) >= 3:
        return "RAPID_VOLTAGE_SWING", (
            f"{len(recent_changes)} voltage changes in the 15s before crash. "
            "Rapid voltage switching can destabilise the GPU. "
            "Increase hysteresis_count to 3-4 and space thresholds further apart.")

    if clock >= 3100 and volt <= -150:
        change_detail = ""
        if recent_changes:
            secs = crash_ts - recent_changes[-1].get("ts", crash_ts)
            change_detail = f" A voltage change occurred {secs:.1f}s before the crash."
        return "UNDERVOLT_TOO_AGGRESSIVE", (
            f"Clock was {clock} MHz at {volt} mV when crash occurred.{change_detail} "
            "The GPU needed more voltage at this frequency. "
            f"Raise the ≥{3100 if clock < 3200 else 3200} MHz threshold by 20-40 mV.")

    if clock >= 3200 and volt <= -130:
        return "VOLTAGE_INSUFFICIENT_FOR_CLOCK", (
            f"Peak boost ({clock} MHz) with {volt} mV — insufficient voltage for max clocks. "
            "Set the ≥3200 MHz threshold to -100 mV or less aggressive.")

    window_20 = [p for p in telemetry
                 if crash_ts - p.get("ts", 0) <= 20]
    if window_20:
        hi_clk = sum(1 for p in window_20 if p.get("clock_mhz", 0) >= 3100)
        lo_vlt = sum(1 for p in window_20 if (p.get("voltage_mv") or 0) <= -140)
        if hi_clk >= len(window_20) * 0.7 and lo_vlt >= len(window_20) * 0.7:
            return "SUSTAINED_HIGH_CLOCK_LOW_VOLTAGE", (
                "GPU sustained ≥3100 MHz for ~20s at ≤-140 mV. Under sustained load "
                "the silicon needs more voltage than during brief boosts. "
                "Reduce undervolt magnitude by 20-30 mV.")

    if recent_changes:
        secs = crash_ts - recent_changes[-1].get("ts", crash_ts)
        if secs <= 5:
            old_mv = recent_changes[-1].get("old_mv")
            new_mv = recent_changes[-1].get("new_mv", volt)
            return "CRASH_AFTER_VOLTAGE_CHANGE", (
                f"Voltage changed from {old_mv} mV to {new_mv} mV exactly "
                f"{secs:.1f}s before the crash. "
                "Increase hysteresis_count so voltage changes require more confirmation reads.")

    return "UNKNOWN", (
        f"No clear pattern. Clock={clock} MHz, Voltage={volt} mV at last reading. "
        "Review the telemetry below. Common causes: too aggressive undervolt, "
        "power delivery issue, or driver bug unrelated to undervolting.")


def _recommendations(code: str) -> list[str]:
    recs = {
        "UNDERVOLT_TOO_AGGRESSIVE": [
            "1. Raise the voltage offset for your highest-clock threshold by 20-40 mV.",
            "2. Test stability with Unigine Heaven at the new offset for 20+ minutes.",
            "3. Start at -80 mV and step down 10 mV at a time to find your stable floor.",
        ],
        "RAPID_VOLTAGE_SWING": [
            "1. Increase hysteresis_count from 2 to 4.",
            "2. Space threshold clock values further apart (e.g. 100 MHz gaps minimum).",
            "3. Increase poll_interval_sec to 0.75s to reduce sensitivity.",
        ],
        "VOLTAGE_INSUFFICIENT_FOR_CLOCK": [
            "1. Set the ≥3200 MHz threshold to -80 mV or -100 mV (less aggressive).",
            "2. Consider not undervolting at peak boost at all — the savings are minimal.",
        ],
        "SUSTAINED_HIGH_CLOCK_LOW_VOLTAGE": [
            "1. Reduce undervolt by 20-30 mV on all thresholds above 3000 MHz.",
            "2. Ensure case airflow is adequate — heat increases voltage requirements.",
        ],
        "CRASH_AFTER_VOLTAGE_CHANGE": [
            "1. Increase hysteresis_count to 3 or 4.",
            "2. Space your threshold clock values further apart to reduce transitions.",
        ],
        "THERMAL": [
            "1. Improve case airflow.",
            "2. Check GPU fan curve in AMD Adrenalin.",
            "3. Reapply thermal paste if GPU is older.",
        ],
        "THERMAL_HOTSPOT": [
            "1. Check thermal pads on VRAM/VRM.",
            "2. Reduce GPU power limit slightly.",
        ],
        "UNKNOWN": [
            "1. Raise all voltage offsets by 20 mV as a safe baseline and retest.",
            "2. Check Windows Event Viewer > System for additional context.",
            "3. Run AMD Adrenalin diagnostics and check for driver updates.",
        ],
        "NO_TELEMETRY": [
            "1. Ensure ClawVolt was running and monitoring when the crash occurred.",
            "2. The crash may have happened too quickly for any telemetry to be saved.",
        ],
    }
    return recs.get(code, recs["UNKNOWN"])


# ── Report writer ──────────────────────────────────────────────────────────────

def _write_crash_report(log_path: str, crash_no: int, event_id: int,
                        event_msg: str, event_time: str, reason_code: str,
                        explanation: str, telemetry: list[dict],
                        volt_changes: list[dict], config: dict,
                        postmortem: bool = False):
    sep  = "═" * 72
    sep2 = "─" * 72
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode = "POST-MORTEM (detected on next startup)" if postmortem else "LIVE"

    def wrap(text, width=68):
        words, lines, cur = text.split(), [], ""
        for w in words:
            if len(cur) + len(w) + 1 <= width:
                cur = (cur + " " + w).lstrip()
            else:
                if cur: lines.append(cur)
                cur = w
        if cur: lines.append(cur)
        return lines or [""]

    lines = [
        "", sep,
        f"  CLAWVOLT CRASH REPORT #{crash_no}  [{mode}]",
        f"  Detected  : {ts}",
        f"  Event     : Windows Event ID {event_id}",
        f"  Event msg : {event_msg[:80]}",
        f"  Event time: {event_time}",
        sep,
        "",
        f"  REASON CODE : {reason_code}",
        "",
        "  ANALYSIS",
        f"  {sep2}",
        *[f"  {l}" for l in wrap(explanation)],
        "",
        "  ACTIVE CONFIG AT CRASH TIME",
        f"  {sep2}",
        f"  Poll interval : {config.get('poll_interval_sec', '?')}s",
        f"  Hysteresis    : {config.get('hysteresis_count', '?')} reads",
        f"  Idle offset   : {config.get('idle_voltage_offset_mv', '?')} mV",
        "  Thresholds:",
    ]
    for t in sorted(config.get("thresholds", []),
                    key=lambda x: x.get("clock_mhz", 0), reverse=True):
        lines.append(f"    ≥ {t.get('clock_mhz',0):>5} MHz → {t.get('voltage_offset_mv',0):>+5} mV")

    lines += [
        "",
        "  VOLTAGE CHANGE HISTORY (pre-crash)",
        f"  {sep2}",
    ]
    if volt_changes:
        for c in volt_changes[-20:]:
            ts_c  = datetime.fromtimestamp(c.get("ts", 0)).strftime("%H:%M:%S")
            old_s = f"{c['old_mv']:+d}" if c.get("old_mv") is not None else "init"
            new_s = f"{c['new_mv']:+d}" if c.get("new_mv") is not None else "?"
            lines.append(f"  {ts_c}   {old_s} mV → {new_s} mV")
    else:
        lines.append("  No voltage changes recorded.")

    lines += [
        "",
        f"  TELEMETRY SNAPSHOT (last {min(len(telemetry), 30)} samples)",
        f"  {sep2}",
        f"  {'Time':>12}  {'Clock':>8}  {'Voltage':>9}  {'Temp':>6}  {'HotSpot':>8}  {'Power':>7}  {'Fan':>5}",
        f"  {sep2}",
    ]
    for p in telemetry[-30:]:
        t_str  = datetime.fromtimestamp(p.get("ts", 0)).strftime("%H:%M:%S.%f")[:-3]
        clk    = p.get("clock_mhz", 0)
        v      = p.get("voltage_mv")
        temp   = p.get("temp_c")
        hot    = p.get("hotspot_c")
        pwr    = p.get("power_w")
        fan    = p.get("fan_pct")
        v_s    = f"{v:>+6} mV" if v is not None else "      ?"
        t_s    = f"{temp:>5.0f}°" if temp is not None else "     ?"
        h_s    = f"{hot:>6.0f}°" if hot is not None else "      ?"
        p_s    = f"{pwr:>5.0f}W" if pwr is not None else "     ?"
        f_s    = f"{fan:>4}%" if fan is not None else "    ?"
        lines.append(f"  {t_str:>12}  {clk:>6} MHz  {v_s}  {t_s}  {h_s}  {p_s}  {f_s}")

    lines += [
        "",
        "  RECOMMENDATIONS",
        f"  {sep2}",
        *[f"  {r}" for r in _recommendations(reason_code)],
        "",
        sep, "",
    ]

    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ── Main CrashLogger class ────────────────────────────────────────────────────

class CrashLogger:
    def __init__(self, config: dict, log_path: str = _DEFAULT_LOG):
        self.config   = config
        self.log_path = log_path
        self._dir     = os.path.dirname(os.path.abspath(log_path))
        self._telem_path     = os.path.join(self._dir, _TELEMETRY_FILE)
        self._heartbeat_path = os.path.join(self._dir, _HEARTBEAT_FILE)

        self._running       = False
        self._thread        = None
        self._crash_count   = 0
        self._lock          = threading.Lock()
        self._session_start_iso = ""

        # In-memory ring buffer (also flushed to disk)
        self._flight: deque[dict] = deque(maxlen=MAX_TELEMETRY_ROWS)
        self._volt_changes: list[dict] = []

        # Callbacks
        self.on_crash_detected = None   # (crash_no, reason_code, explanation)
        self.on_log_entry      = None   # (msg, level)

    def _log(self, msg: str, level: str = "info"):
        if self.on_log_entry:
            self.on_log_entry(msg, level)

    # ── Telemetry I/O ──────────────────────────────────────────────────────────

    def _flush_telemetry(self):
        """Write current in-memory telemetry to disk (called every poll cycle)."""
        try:
            data = {
                "session_start": self._session_start_iso,
                "telemetry":     list(self._flight),
                "volt_changes":  self._volt_changes[-50:],
                "config":        self.config,
            }
            tmp = self._telem_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self._telem_path)  # atomic replace
        except Exception:
            pass

    def _load_last_telemetry(self) -> dict:
        """Load telemetry saved by the previous session."""
        try:
            with open(self._telem_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _write_heartbeat(self):
        """Write a heartbeat file marking the session as alive."""
        try:
            with open(self._heartbeat_path, "w", encoding="utf-8") as f:
                json.dump({
                    "pid":           os.getpid(),
                    "session_start": self._session_start_iso,
                    "ts":            time.time(),
                }, f)
        except Exception:
            pass

    def _delete_heartbeat(self):
        """Delete heartbeat — signals a clean exit."""
        try:
            if os.path.exists(self._heartbeat_path):
                os.remove(self._heartbeat_path)
        except Exception:
            pass

    def _was_last_session_unclean(self) -> bool:
        """Return True if the previous session did not exit cleanly."""
        return os.path.exists(self._heartbeat_path)

    # ── Post-mortem crash detection ────────────────────────────────────────────

    def check_previous_session(self):
        """
        Called at startup BEFORE writing this session's heartbeat.
        Checks if the last session ended abnormally, queries the Event Log
        for crash events that occurred during that session, and writes a
        post-mortem crash report.
        """
        if not self._was_last_session_unclean():
            return  # Clean exit — nothing to report

        last = self._load_last_telemetry()
        if not last:
            self._log("Previous session ended unexpectedly (no telemetry saved)", "warn")
            self._delete_heartbeat()
            return

        session_start = last.get("session_start", "")
        telemetry     = last.get("telemetry", [])
        volt_changes  = last.get("volt_changes", [])
        config        = last.get("config", self.config)

        self._log("Previous session ended unexpectedly — checking for crash events...", "warn")

        if not _EVTLOG_OK:
            self._log("pywin32 not available — cannot query Event Log for crash events", "warn")
            self._log("Install pywin32: pip install pywin32", "warn")
            self._delete_heartbeat()
            return

        crash_events = _find_crash_events(session_start)
        if not crash_events:
            self._log("No Event Log entry found — writing telemetry-based post-mortem report", "warn")
            self._crash_count += 1
            # Use the last telemetry timestamp as approximate crash time
            crash_iso = session_start
            if telemetry:
                last_ts = telemetry[-1].get("ts", 0)
                crash_iso = datetime.utcfromtimestamp(last_ts).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")
            reason, explanation = _analyse_crash(telemetry, volt_changes, crash_iso, "Driver/process crash")
            _write_crash_report(
                log_path=self.log_path,
                crash_no=self._crash_count,
                event_id=0,
                event_msg="Process killed unexpectedly (no matching Event Log entry found — TDR may not have been logged)",
                event_time=crash_iso,
                reason_code=reason,
                explanation=explanation,
                telemetry=telemetry,
                volt_changes=volt_changes,
                config=config,
                postmortem=True,
            )
            self._log(f"Post-mortem crash report #{self._crash_count} written → {self.log_path}", "info")
        else:
            for evt in crash_events:
                self._crash_count += 1
                event_time = evt.get("TimeCreated", session_start)
                event_msg  = evt.get("Message", "Display driver reset")
                event_id   = evt.get("EventID", 0)

                reason, explanation = _analyse_crash(
                    telemetry, volt_changes, event_time, event_msg)

                self._log(f"⚠ CRASH #{self._crash_count} (post-mortem) — Event {event_id} — {reason}", "error")
                self._log(explanation[:120], "warn")

                _write_crash_report(
                    log_path=self.log_path,
                    crash_no=self._crash_count,
                    event_id=event_id,
                    event_msg=event_msg,
                    event_time=event_time,
                    reason_code=reason,
                    explanation=explanation,
                    telemetry=telemetry,
                    volt_changes=volt_changes,
                    config=config,
                    postmortem=True,
                )
                self._log(f"Post-mortem report #{self._crash_count} written: {self.log_path}", "info")

                if self.on_crash_detected:
                    self.on_crash_detected(self._crash_count, reason, explanation)

        self._delete_heartbeat()

    # ── Live recording ─────────────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._session_start_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.0000000Z")
        self._write_heartbeat()
        self._running = True
        self._log(f"Crash logger active — flight recorder running | pywin32: {_EVTLOG_OK}", "info")

    def stop(self):
        self._running = False
        self._delete_heartbeat()

    def record(self, clock: int, voltage: Optional[int],
               temp: Optional[float] = None, hotspot: Optional[float] = None,
               power: Optional[float] = None, fan: Optional[int] = None,
               note: str = ""):
        """Called every poll cycle — appends to in-memory buffer and flushes to disk."""
        point = {
            "ts":         time.time(),
            "clock_mhz":  clock,
            "voltage_mv": voltage,
            "temp_c":     temp,
            "hotspot_c":  hotspot,
            "power_w":    power,
            "fan_pct":    fan,
            "note":       note,
        }
        with self._lock:
            self._flight.append(point)
            cutoff = time.time() - 60.0
            self._volt_changes = [c for c in self._volt_changes if c.get("ts", 0) >= cutoff]
        self._flush_telemetry()

    def on_voltage_changed(self, old_mv: Optional[int], new_mv: int):
        with self._lock:
            self._volt_changes.append({
                "ts":     time.time(),
                "old_mv": old_mv,
                "new_mv": new_mv,
            })

    def update_config(self, config: dict):
        self.config = dict(config)

    def get_crash_count(self) -> int:
        return self._crash_count

    def get_log_path(self) -> str:
        return os.path.abspath(self.log_path)

    def write_session_header(self, gpu_name: str, config: dict):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        thresholds_str = ", ".join(
            f"≥{t['clock_mhz']}MHz→{t['voltage_offset_mv']}mV"
            for t in sorted(config.get("thresholds", []),
                            key=lambda x: x.get("clock_mhz", 0), reverse=True)
        )
        header = (
            f"\n{'─'*72}\n"
            f"  ClawVolt session started  {ts}\n"
            f"  GPU     : {gpu_name}\n"
            f"  Config  : poll={config.get('poll_interval_sec')}s  "
            f"hyst={config.get('hysteresis_count')}  "
            f"idle={config.get('idle_voltage_offset_mv')}mV\n"
            f"  Thresholds: {thresholds_str}\n"
            f"{'─'*72}\n"
        )
        os.makedirs(os.path.dirname(os.path.abspath(self.log_path)), exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(header)

    def write_session_footer(self, crash_count: int):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        footer = (
            f"  ClawVolt session ended  {ts}  —  {crash_count} crash(es) recorded\n"
            f"{'─'*72}\n"
        )
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(footer)
