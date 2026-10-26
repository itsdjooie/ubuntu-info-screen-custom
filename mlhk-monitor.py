#!/usr/bin/env python3
"""
MLHK SERVER MONITOR — Verticaal Portret Dashboard (1080x1920 / 27 inch)
Ontwikkeld voor: SMURF-SERVER (Proxmox VE 9.x)
Huisstijl: mlhk.nl (Electric Cyan, Neon Magenta, Zwart, Wit)
Statussen: ONLINE = Groen, OFFLINE = Rood
Verbruik:  <60% = Groen, 60-80% = Oranje, >80% = Rood
"""

import curses
import http.client
import json
import os
import pwd
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime

# ==============================================================================
# CONFIGURATION (Environment variables with sensible defaults)
# ==============================================================================
SERVER_DISPLAY_NAME = os.environ.get("SERVER_NAME", "SMURF-SERVER")
MONITOR_TITLE = os.environ.get("MONITOR_TITLE", "MLHK SERVER MONITOR")
REFRESH_INTERVAL = float(os.environ.get("REFRESH_INTERVAL", "3.0"))

# Node 3: Remote Raspberry Pi / Media Node (SSH metrics poller)
ENABLE_REMOTE_NODE = os.environ.get("ENABLE_REMOTE_NODE", "true").lower() == "true"
PI5_HOST = os.environ.get("REMOTE_NODE_HOST", "192.168.2.25")
PI5_USER = os.environ.get("REMOTE_NODE_USER", "molhoek")

# Node 2: Home Assistant Smart Home & Energy Integration
ENABLE_HA = os.environ.get("ENABLE_HA", "true").lower() == "true"
HA_HOST = os.environ.get("HA_HOST", "192.168.2.56")
HA_PORT = int(os.environ.get("HA_PORT", "8123"))
# Vul hier je Home Assistant Long-Lived Access Token in:
HA_TOKEN = os.environ.get("HA_TOKEN", "YOUR_HOMEASSISTANT_LONG_LIVED_ACCESS_TOKEN_HERE")

# Weer & Regenradar locatie ('s-Hertogenbosch standaard)
WEATHER_LAT = float(os.environ.get("WEATHER_LAT", "51.6978"))
WEATHER_LON = float(os.environ.get("WEATHER_LON", "5.3037"))
WEATHER_CITY = os.environ.get("WEATHER_CITY", "'S-HERTOGENBOSCH")

# Proxmox VE API Token file (optioneel voor VM status query)
TOKEN_FILE = os.environ.get("PVE_TOKEN_FILE", "/home/mlhk-monitor/.config/mlhk-monitor/pve-token.json")
PVE_HOST = os.environ.get("PVE_HOST", "127.0.0.1")
PVE_PORT = int(os.environ.get("PVE_PORT", "8006"))


def set_linux_console_palette():
    """
    Program the 16-color hardware palette on Linux TTY (/dev/tty1).
    Exact palette derived from mlhk.nl:
      - 0: Deep Obsidian Black
      - 1: Neon Red (Hoog verbruik >80% / Offline)
      - 2: Vivid Neon Green (Laag verbruik <60% / Online)
      - 3: Vivid Amber/Orange (Medium verbruik 60-80%)
      - 4: Electric Deep Blue / Cyan
      - 5: Neon Magenta / Hot Pink (MLHK Logo)
      - 6: Electric Cyan / Aqua (MLHK Logo)
      - 7: Crisp Clean White (Tekst)
      - 8: Dark Slate (Lege balken track)
      - 9: Bright Red
      - A: Glowing Neon Green (ONLINE Badge)
      - B: Glowing Amber / Orange
      - C: Bright Sky Cyan
      - D: Bright Neon Pink / Magenta
      - E: Electric Violet / Purple
      - F: Pure Bright White
    """
    if os.environ.get("TERM") == "linux" and sys.stdout.isatty():
        palette = [
            ("0", "070A12"),  # 0: Black (Deep Obsidian Black)
            ("1", "FF2255"),  # 1: Red (Hoog verbruik / Offline)
            ("2", "00E676"),  # 2: Green (Laag verbruik / Online)
            ("3", "FF9100"),  # 3: Orange/Yellow (Medium verbruik)
            ("4", "00B0FF"),  # 4: Blue/Cyan (Electric Cyan - MLHK brand)
            ("5", "E040FB"),  # 5: Magenta (Neon Magenta/Pink - MLHK brand)
            ("6", "00E5FF"),  # 6: Cyan (Vivid Cyan - MLHK brand)
            ("7", "E2E8F0"),  # 7: White (Crisp Light Text)
            ("8", "262D3D"),  # 8: Bright Black -> Dark Slate (empty bar tracks)
            ("9", "FF1744"),  # 9: Bright Red (Critical Alert)
            ("A", "00FF88"),  # 10: Bright Green (Vivid Neon Online)
            ("B", "FFB300"),  # 11: Bright Orange (Warning)
            ("C", "40C4FF"),  # 12: Bright Sky Cyan (Highlight)
            ("D", "FF4081"),  # 13: Bright Hot Pink / Magenta (Highlight)
            ("E", "7C4DFF"),  # 14: Electric Violet (Accent)
            ("F", "FFFFFF"),  # 15: Bright White (Pure White)
        ]
        seq = "".join(f"\033]P{idx}{rgb}" for idx, rgb in palette)
        try:
            sys.stdout.write(seq)
            sys.stdout.flush()
        except Exception:
            pass


def get_fbcon_rotation():
    try:
        with open("/sys/class/graphics/fbcon/rotate", "r", encoding="utf-8") as f:
            val = int(f.read().strip())
            return {0: "0° Liggend", 1: "90° Portret", 2: "180°", 3: "270° Portret"}.get(val, f"{val}")
    except Exception:
        return "Portret"


def toggle_fbcon_rotation():
    """Toggle between 270° CCW (3) and 90° CW (1) portrait rotation if permitted."""
    try:
        cur = 3
        with open("/sys/class/graphics/fbcon/rotate", "r", encoding="utf-8") as f:
            cur = int(f.read().strip())
        nxt = 1 if cur == 3 else 3
        with open("/sys/class/graphics/fbcon/rotate_all", "w", encoding="utf-8") as f:
            f.write(str(nxt))
    except Exception:
        pass


def format_bytes(num_bytes, precision=1):
    """Format byte counts into human-readable KiB/MiB/GiB/TiB."""
    if num_bytes is None or num_bytes < 0:
        return "0 B"
    val = float(num_bytes)
    for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
        if abs(val) < 1024.0 or unit == "TiB":
            if unit == "B":
                return f"{int(val)} B"
            return f"{val:.{precision}f} {unit}"
        val /= 1024.0
    return f"{val:.1f} TiB"


def format_rate(bytes_per_sec):
    """Format byte/s transfer rates cleanly."""
    if bytes_per_sec < 1024:
        return f"{bytes_per_sec:5.0f} B/s"
    kb = bytes_per_sec / 1024.0
    if kb < 1024:
        return f"{kb:5.1f} KB/s"
    mb = kb / 1024.0
    return f"{mb:5.2f} MB/s"


def format_uptime(seconds):
    """Format seconds into Xd Xh Xm Xs."""
    seconds = int(max(0, seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours:02d}u {mins:02d}m {secs:02d}s"
    return f"{hours:02d}u {mins:02d}m {secs:02d}s"


class SystemCollector:
    """Collects genuine Linux & Proxmox VE telemetry without root or sudo."""

    def __init__(self):
        self.prev_cpu = {}
        self.prev_disk = {}
        self.prev_net = {}
        self.prev_proc = {}
        self.prev_time = time.monotonic()
        self.uid_cache = {}
        self.clk_tck = os.sysconf(os.sysconf_names.get("SC_CLK_TCK", "SC_CLK_TCK")) if "SC_CLK_TCK" in os.sysconf_names else 100
        self.page_size = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
        self.cpu_model = self._read_cpu_model()
        self.pve_version = "Proxmox VE 9.2"
        self.kernel_release = os.uname().release
        self.node_name = socket.gethostname()
        self.token_header = self._load_pve_token()
        self.ssl_ctx = ssl._create_unverified_context()
        self.http_conn = None

        # Pi 5 background poller state
        self.pi5_data = {
            "hostname": "betaserver",
            "model": "Raspberry Pi 5",
            "ip": PI5_HOST,
            "uptime": 0.0,
            "load": [0.0, 0.0, 0.0],
            "temp": 0.0,
            "mem": {"total": 1, "used": 0, "pct": 0.0},
            "disks": [],
            "plex_count": 0,
            "plex_streams": [],
            "jelly_count": 0,
            "jelly_streams": [],
            "online": False,
        }
        self.pi5_lock = threading.Lock()
        self.pi5_thread = threading.Thread(target=self._pi5_worker, daemon=True)
        self.pi5_thread.start()

        # Home Assistant poller state
        self.ha_data = {
            "online": False,
            "persons": [],
            "p1_power": 1310.0,
            "p1_phase1": 715.0,
            "p1_phase2": 565.0,
            "p1_phase3": 30.0,
            "p1_import_total": 60606.6,
            "temp_woonkamer": 21.7,
            "hum_woonkamer": 54.0,
            "boiler_power": 0.0,
            "airco_power": 0.0,
            "wasmachine_state": "Uit",
            "droger_state": "Uit",
            "weather": "Onbekend",
            "door_open": False,
            "door_open_seconds": 0,
            "door_open_alert": False,
        }
        self.door_opened_at = None
        self.ha_lock = threading.Lock()
        self.ha_thread = threading.Thread(target=self._ha_worker, daemon=True)
        self.ha_thread.start()

        # Den Bosch Multi-source Weather poller state
        self.weather_data = {
            "online": False,
            "rain_forecast": [],
            "current_rain_mm": 0.0,
            "max_rain_2h_mm": 0.0,
            "rain_starts_in_min": None,
            "rain_summary": "Geen regen verwacht komende 2 uur",
            "volkel": {},
            "forecast_daily": [],
            "knmi_code": "GROEN",
            "knmi_title": "Geen waarschuwingen (KNMI)",
            "knmi_desc": "",
            "is_extreme": False,
        }
        self.weather_lock = threading.Lock()
        self.weather_thread = threading.Thread(target=self._weather_worker, daemon=True)
        self.weather_thread.start()

        # Prime delta counters
        self._read_cpu_times()
        self._read_disk_io()
        self._read_net_io()
        self._read_processes(1.0)
        time.sleep(0.2)
        self.prev_time = time.monotonic() - 0.2

    def _pi5_worker(self):
        """Background worker polling betaserver over SSH without blocking curses loop."""
        while True:
            try:
                cmd = [
                    "ssh",
                    "-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "ConnectTimeout=3",
                    f"{PI5_USER}@{PI5_HOST}",
                    "python3 /home/molhoek/pi_metrics.py",
                ]
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=4, text=True)
                if res.returncode == 0 and res.stdout.strip():
                    parsed = json.loads(res.stdout)
                    with self.pi5_lock:
                        self.pi5_data = parsed
                else:
                    with self.pi5_lock:
                        self.pi5_data["online"] = False
            except Exception:
                with self.pi5_lock:
                    self.pi5_data["online"] = False
            time.sleep(2.5)

    def _get_pi5_snapshot(self):
        with self.pi5_lock:
            return dict(self.pi5_data)

    def _ha_worker(self):
        """Background worker polling Home Assistant REST API every 3s."""
        ha_url = f"http://{HA_HOST}:{HA_PORT}/api/states"
        req = urllib.request.Request(
            ha_url,
            headers={
                "Authorization": f"Bearer {HA_TOKEN}",
                "Content-Type": "application/json",
            },
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        while True:
            try:
                with urllib.request.urlopen(req, timeout=4, context=ctx) as resp:
                    if resp.status == 200:
                        raw = resp.read().decode("utf-8")
                        states = json.loads(raw)
                        data_map = {s.get("entity_id"): s for s in states}

                        # Persons
                        persons = []
                        for pid, label in [("person.molhoek", "Molhoek"), ("person.lynn", "Lynn")]:
                            ent = data_map.get(pid)
                            if ent:
                                st = ent.get("state", "away")
                                is_home = (st.lower() == "home")
                                persons.append({
                                    "name": label,
                                    "is_home": is_home,
                                    "state_str": "Thuis" if is_home else "Afwezig",
                                })

                        # Power (P1 Smart Meter) with last known value retention
                        def safe_float(eid, default=None):
                            ent = data_map.get(eid)
                            if ent:
                                st = ent.get("state")
                                if st not in (None, "unavailable", "unknown"):
                                    try:
                                        return float(st)
                                    except (ValueError, TypeError):
                                        return default
                            return default

                        prev_ha = dict(self.ha_data)
                        p1_power = safe_float("sensor.p1_meter_vermogen", prev_ha.get("p1_power", 0.0))
                        p1_p1 = safe_float("sensor.p1_meter_vermogen_fase_1", prev_ha.get("p1_phase1", 0.0))
                        p1_p2 = safe_float("sensor.p1_meter_vermogen_fase_2", prev_ha.get("p1_phase2", 0.0))
                        p1_p3 = safe_float("sensor.p1_meter_vermogen_fase_3", prev_ha.get("p1_phase3", 0.0))
                        p1_tot = safe_float("sensor.p1_meter_energie_import", prev_ha.get("p1_import_total", 0.0))

                        # If phases sum > 0 but total was 0, calculate sum
                        if (p1_power is None or p1_power == 0.0) and (p1_p1 or p1_p2 or p1_p3):
                            p1_power = (p1_p1 or 0.0) + (p1_p2 or 0.0) + (p1_p3 or 0.0)

                        # Climate (Woonkamer iglo)
                        t_ent = data_map.get("sensor.temp_woonkamer_temperatuur")
                        t_val = prev_ha.get("temp_woonkamer")
                        if t_ent and t_ent.get("state") not in (None, "unavailable", "unknown"):
                            try:
                                t_val = float(t_ent.get("state"))
                            except (ValueError, TypeError):
                                pass

                        h_ent = data_map.get("sensor.temp_woonkamer_luchtvochtigheid")
                        h_val = prev_ha.get("hum_woonkamer")
                        if h_ent and h_ent.get("state") not in (None, "unavailable", "unknown"):
                            try:
                                h_val = float(h_ent.get("state"))
                            except (ValueError, TypeError):
                                pass

                        # Appliances & Smart Plugs
                        wm_ent = data_map.get("binary_sensor.wasruimte_wasmachine_vermogen")
                        wm_st = "Draait" if wm_ent and wm_ent.get("state") == "on" else "Uit"

                        dr_ent = data_map.get("binary_sensor.wasruimte_droger_vermogen")
                        dr_st = "Draait" if dr_ent and dr_ent.get("state") == "on" else "Uit"

                        boiler_p = safe_float("sensor.energy_socket_power", 0.0)
                        airco_sw = data_map.get("switch.airco", {})
                        airco_on = airco_sw.get("state") == "on"

                        w_ent = data_map.get("weather.forecast_thuis") or data_map.get("weather.buienradar")
                        w_state = w_ent.get("state", "Onbekend") if w_ent else "Onbekend"

                        # Contact Sensor Voordeur (Tuya / Home Assistant)
                        door_ent = data_map.get("binary_sensor.voordeur_sensor_deur")
                        door_state = door_ent.get("state") if door_ent else "off"
                        is_door_open = (door_state == "on")

                        now_ts = time.time()
                        if is_door_open:
                            # Gebruik de daadwerkelijke last_changed timestamp uit Home Assistant indien beschikbaar
                            ha_last_changed = door_ent.get("last_changed")
                            if ha_last_changed:
                                try:
                                    dt_c = datetime.fromisoformat(ha_last_changed.replace("Z", "+00:00"))
                                    open_duration = max(0, int(now_ts - dt_c.timestamp()))
                                except Exception:
                                    if not self.door_opened_at:
                                        self.door_opened_at = now_ts
                                    open_duration = int(now_ts - self.door_opened_at)
                            else:
                                if not self.door_opened_at:
                                    self.door_opened_at = now_ts
                                open_duration = int(now_ts - self.door_opened_at)
                        else:
                            self.door_opened_at = None
                            open_duration = 0

                        # Melding triggert als voordeur minimaal 10 minuten (600 seconden) open staat
                        door_alert = is_door_open and (open_duration >= 600)

                        with self.ha_lock:
                            self.ha_data = {
                                "online": True,
                                "persons": persons,
                                "p1_power": p1_power,
                                "p1_phase1": p1_p1,
                                "p1_phase2": p1_p2,
                                "p1_phase3": p1_p3,
                                "p1_import_total": p1_tot,
                                "temp_woonkamer": t_val,
                                "hum_woonkamer": h_val,
                                "boiler_power": boiler_p,
                                "airco_power": 1.0 if airco_on else 0.0,
                                "wasmachine_state": wm_st,
                                "droger_state": dr_st,
                                "weather": w_state,
                                "door_open": is_door_open,
                                "door_open_seconds": open_duration,
                                "door_open_alert": door_alert,
                            }
                    else:
                        with self.ha_lock:
                            self.ha_data["online"] = False
            except Exception as e:
                with open("/tmp/ha_worker.log", "a") as ef:
                    ef.write(f"[{datetime.now()}] HA ERROR: {e}\n")
                with self.ha_lock:
                    self.ha_data["online"] = False

            time.sleep(3.0)

    def _get_ha_snapshot(self):
        with self.ha_lock:
            return dict(self.ha_data)

    def _weather_worker(self):
        """Background worker aggregating free Dutch weather APIs (Buienradar, KNMI, Open-Meteo)."""
        while True:
            # 1. Buienradar 2-uur neerslag voorspelling voor 's-Hertogenbosch
            rain_pts = []
            curr_rain = 0.0
            max_rain = 0.0
            starts_in = None
            sum_str = "Geen regen verwacht komende 2 uur"

            try:
                br_url = f"https://gps.buienradar.nl/getrr.php?lat={WEATHER_LAT:.2f}&lon={WEATHER_LON:.2f}"
                req = urllib.request.Request(br_url, headers={"User-Agent": "MLHK-Monitor/1.0"})
                with urllib.request.urlopen(req, timeout=4) as resp:
                    lines = resp.read().decode("utf-8", errors="replace").strip().split("\n")
                    for idx, line in enumerate(lines):
                        if "|" in line:
                            val_str, tm_str = line.strip().split("|", 1)
                            v = int(val_str)
                            # Buienradar neerslag intensiteit formule: mm/u = 10^((val-109)/32)
                            mm = round(10.0 ** ((v - 109.0) / 32.0), 2) if v > 0 else 0.0
                            rain_pts.append({"time": tm_str, "val": v, "mm": mm})
                            if idx == 0:
                                curr_rain = mm
                            if mm > max_rain:
                                max_rain = mm
                            if mm > 0.05 and starts_in is None:
                                starts_in = idx * 5

                    if max_rain == 0.0:
                        sum_str = "Droog komende 2 uur (0.0 mm/u)"
                    elif starts_in == 0:
                        if max_rain > 4.0:
                            sum_str = f"LET OP: Zware regenbui NU actief ({curr_rain:.1f} mm/u)!"
                        elif max_rain > 1.5:
                            sum_str = f"Matige regen NU actief ({curr_rain:.1f} mm/u)"
                        else:
                            sum_str = f"Lichte motregen NU actief ({curr_rain:.1f} mm/u)"
                    elif starts_in is not None:
                        if max_rain > 4.0:
                            sum_str = f"PAS OP: Zware regen over {starts_in} min (piek {max_rain:.1f} mm/u)!"
                        else:
                            sum_str = f"Regen verwacht over {starts_in} min (piek {max_rain:.1f} mm/u)"
            except Exception:
                pass

            # 2. Buienradar actuele meetstation data (Meetstation Volkel / Herwijnen)
            volkel_data = {}
            try:
                br_feed = "https://api.buienradar.nl/data/public/2.0/jsonfeed"
                req = urllib.request.Request(br_feed, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=4) as resp:
                    d = json.loads(resp.read().decode("utf-8"))
                    stns = d.get("actual", {}).get("stationmeasurements", [])
                    for s in stns:
                        if s.get("stationid") == 6375:  # Volkel
                            volkel_data = {
                                "temp": s.get("temperature", 0.0),
                                "feel": s.get("feeltemperature", 0.0),
                                "wind_bft": s.get("windspeedBft", 0),
                                "wind_dir": s.get("winddirection", "W"),
                                "desc": s.get("weatherdescription", "Bewolkt"),
                                "humidity": s.get("humidity", 0.0),
                                "rain_1h": s.get("rainFallLastHour", 0.0),
                            }
                            break
            except Exception:
                pass

            # 3. Open-Meteo 3-daagse verwachting
            daily_forecast = []
            try:
                om_url = (
                    f"https://api.open-meteo.com/v1/forecast?latitude={WEATHER_LAT}&longitude={WEATHER_LON}"
                    "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum"
                    "&timezone=Europe%2FAmsterdam&forecast_days=5"
                )
                req = urllib.request.Request(om_url, headers={"User-Agent": "MLHK-Monitor/1.0"})
                with urllib.request.urlopen(req, timeout=4) as resp:
                    om_d = json.loads(resp.read().decode("utf-8")).get("daily", {})
                    d_times = om_d.get("time", [])
                    for i in range(len(d_times)):
                        daily_forecast.append({
                            "date": d_times[i],
                            "code": om_d.get("weather_code", [])[i],
                            "max": om_d.get("temperature_2m_max", [])[i],
                            "min": om_d.get("temperature_2m_min", [])[i],
                            "rain_pct": om_d.get("precipitation_probability_max", [])[i],
                            "rain_mm": om_d.get("precipitation_sum", [])[i],
                        })
            except Exception:
                pass

            # 4. KNMI Weerwaarschuwingen Noord-Brabant
            knmi_code = "GROEN"
            knmi_title = "Geen waarschuwingen (KNMI)"
            knmi_desc = ""
            is_extreme = False
            try:
                knmi_url = "https://www.knmi.nl/nederland-nu/weer/waarschuwingen/noord-brabant"
                req = urllib.request.Request(knmi_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                with urllib.request.urlopen(req, timeout=4) as resp:
                    html = resp.read().decode("utf-8", errors="replace")
                    if "alert--red" in html or "code rood" in html.lower():
                        knmi_code = "ROOD"
                        knmi_title = "CODE ROOD: KNMI WEERALARM NOORD-BRABANT!"
                        is_extreme = True
                    elif "alert--orange" in html or "code oranje" in html.lower():
                        knmi_code = "ORANJE"
                        knmi_title = "CODE ORANJE: KNMI WAARSCHUWING NOORD-BRABANT"
                        is_extreme = True
                    elif "alert--yellow" in html or "code geel" in html.lower():
                        knmi_code = "GEEL"
                        knmi_title = "CODE GEEL: KNMI WAARSCHUWING NOORD-BRABANT"
            except Exception:
                pass

            # Update state atomically
            with self.weather_lock:
                self.weather_data = {
                    "online": True,
                    "rain_forecast": rain_pts,
                    "current_rain_mm": curr_rain,
                    "max_rain_2h_mm": max_rain,
                    "rain_starts_in_min": starts_in,
                    "rain_summary": sum_str,
                    "volkel": volkel_data,
                    "forecast_daily": daily_forecast,
                    "knmi_code": knmi_code,
                    "knmi_title": knmi_title,
                    "knmi_desc": knmi_desc,
                    "is_extreme": is_extreme,
                }

            time.sleep(20.0)

    def _get_weather_snapshot(self):
        with self.weather_lock:
            return dict(self.weather_data)

    def _load_pve_token(self):
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            tid = data.get("full-tokenid", "").strip()
            val = data.get("value", "").strip()
            if tid and val:
                return f"PVEAPIToken={tid}={val}"
        except Exception:
            pass
        return None

    def _read_cpu_model(self):
        try:
            with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except Exception:
            pass
        return "Intel Core i5-6600 @ 3.30GHz"

    def _read_cpu_times(self):
        current = {}
        try:
            with open("/proc/stat", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("cpu"):
                        parts = line.split()
                        name = parts[0]
                        nums = [int(x) for x in parts[1:]]
                        idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
                        total = sum(nums)
                        current[name] = (total, idle)
        except Exception:
            pass

        usages = {}
        for name, (total, idle) in current.items():
            if name in self.prev_cpu:
                prev_total, prev_idle = self.prev_cpu[name]
                dt = total - prev_total
                di = idle - prev_idle
                usages[name] = max(0.0, min(100.0, (dt - di) * 100.0 / dt)) if dt > 0 else 0.0
            else:
                usages[name] = 0.0
        self.prev_cpu = current

        freqs = []
        try:
            with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("cpu MHz"):
                        freqs.append(float(line.split(":", 1)[1].strip()))
        except Exception:
            pass
        return usages, freqs

    def _read_temperatures(self):
        """Read hardware temperatures from /sys/class/hwmon (lm-sensors kernel interface)."""
        temps = {"pkg": None, "cores": {}, "nvme": None, "gpu": None, "acpi": None}
        hwmon_root = "/sys/class/hwmon"
        if not os.path.isdir(hwmon_root):
            return temps

        for entry in sorted(os.listdir(hwmon_root)):
            hpath = os.path.join(hwmon_root, entry)
            try:
                with open(os.path.join(hpath, "name"), "r", encoding="utf-8") as f:
                    chip = f.read().strip()
            except Exception:
                continue

            for fname in sorted(os.listdir(hpath)):
                if fname.startswith("temp") and fname.endswith("_input"):
                    prefix = fname[:-6]
                    try:
                        with open(os.path.join(hpath, fname), "r", encoding="utf-8") as f:
                            val_c = int(f.read().strip()) / 1000.0
                    except Exception:
                        continue
                    label = ""
                    lpath = os.path.join(hpath, f"{prefix}_label")
                    if os.path.exists(lpath):
                        try:
                            with open(lpath, "r", encoding="utf-8") as f:
                                label = f.read().strip()
                        except Exception:
                            pass

                    if chip == "coretemp":
                        if "Package" in label or prefix == "temp1":
                            temps["pkg"] = val_c
                        elif label.startswith("Core"):
                            try:
                                cidx = int(label.split()[1])
                                temps["cores"][cidx] = val_c
                            except Exception:
                                pass
                    elif chip == "nvme":
                        if "Composite" in label or prefix == "temp1":
                            temps["nvme"] = val_c
                    elif chip == "nouveau":
                        if temps["gpu"] is None:
                            temps["gpu"] = val_c
                    elif chip == "acpitz":
                        if temps["acpi"] is None or val_c > temps["acpi"]:
                            temps["acpi"] = val_c
        return temps

    def _read_memory(self):
        mem = {}
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    if ":" in line:
                        k, v = line.split(":", 1)
                        mem[k.strip()] = int(v.strip().split()[0]) * 1024
        except Exception:
            pass
        total = mem.get("MemTotal", 0)
        avail = mem.get("MemAvailable", mem.get("MemFree", 0))
        used = max(0, total - avail)
        buffers_cached = mem.get("Buffers", 0) + mem.get("Cached", 0) + mem.get("SReclaimable", 0)
        swap_total = mem.get("SwapTotal", 0)
        swap_free = mem.get("SwapFree", 0)
        swap_used = max(0, swap_total - swap_free)
        return {
            "ram_total": total,
            "ram_used": used,
            "ram_avail": avail,
            "ram_cache": buffers_cached,
            "ram_pct": (used * 100.0 / total) if total > 0 else 0.0,
            "swap_total": swap_total,
            "swap_used": swap_used,
            "swap_pct": (swap_used * 100.0 / swap_total) if swap_total > 0 else 0.0,
        }

    def _read_storage(self, pve_storages):
        # Fysieke Schijf 1: Samsung SSD 960 EVO (250 GB)
        # Samengevoegde bezetting van Proxmox Host root + VM Thinpool
        samsung_total = 250059350016  # 250 GB
        # root used (15.2 GB) + pve-data allocated (85.8 GB)
        root_used = 15633752064
        lvm_used = 85846468608
        samsung_used = root_used + lvm_used
        try:
            st = os.statvfs("/")
            root_used = (st.f_blocks - st.f_bfree) * st.f_frsize
            for s in pve_storages:
                if s.get("storage") == "local-lvm":
                    lvm_used = s.get("disk", 0)
            samsung_used = root_used + lvm_used
        except Exception:
            pass
        samsung_free = max(0, samsung_total - samsung_used)
        samsung_pct = (samsung_used * 100.0 / samsung_total) if samsung_total > 0 else 0.0

        # Fysieke Schijf 2: Kingston SSD 240GB (Proxmox Thinpool 'kingston-ssd')
        kingston_total = 240057409536  # 240 GB
        kingston_used = 35479622988    # 33.0 GB baseline
        try:
            for s in pve_storages:
                if s.get("storage") == "kingston-ssd":
                    kingston_used = s.get("disk", kingston_used)
                    if s.get("maxdisk", 0) > 0:
                        kingston_total = s.get("maxdisk")
        except Exception:
            pass
        kingston_free = max(0, kingston_total - kingston_used)
        kingston_pct = (kingston_used * 100.0 / kingston_total) if kingston_total > 0 else 0.0

        disks = [
            {
                "name": "Samsung 250GB",
                "dev": "nvme0n1",
                "used": samsung_used,
                "free": samsung_free,
                "total": samsung_total,
                "pct": samsung_pct,
            },
            {
                "name": "Kingston 240GB",
                "dev": "sda",
                "used": kingston_used,
                "free": kingston_free,
                "total": kingston_total,
                "pct": kingston_pct,
            },
        ]

        return disks, {}

    def _read_disk_io(self, dt=1.0):
        current = {}
        try:
            with open("/proc/diskstats", "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 14:
                        dev = parts[2]
                        if dev in ("nvme0n1", "sda"):
                            rios = int(parts[3])
                            rsec = int(parts[5])
                            wios = int(parts[7])
                            wsec = int(parts[9])
                            current[dev] = (rios, rsec * 512, wios, wsec * 512)
        except Exception:
            pass

        res = {}
        for dev, (rios, rbytes, wios, wbytes) in current.items():
            if dev in self.prev_disk and dt > 0:
                prios, prbytes, pwios, pwbytes = self.prev_disk[dev]
                res[dev] = {
                    "read_bps": max(0.0, (rbytes - prbytes) / dt),
                    "write_bps": max(0.0, (wbytes - pwbytes) / dt),
                    "read_iops": max(0.0, (rios - prios) / dt),
                    "write_iops": max(0.0, (wios - pwios) / dt),
                    "total_read": rbytes,
                    "total_write": wbytes,
                }
            else:
                res[dev] = {
                    "read_bps": 0.0,
                    "write_bps": 0.0,
                    "read_iops": 0.0,
                    "write_iops": 0.0,
                    "total_read": rbytes,
                    "total_write": wbytes,
                }
        self.prev_disk = current
        return res

    def _read_net_io(self, dt=1.0):
        current = {}
        try:
            with open("/proc/net/dev", "r", encoding="utf-8") as f:
                for line in f:
                    if ":" in line:
                        iface, rest = line.split(":", 1)
                        iface = iface.strip()
                        parts = rest.split()
                        if len(parts) >= 10:
                            rx_bytes = int(parts[0])
                            rx_pkts = int(parts[1])
                            tx_bytes = int(parts[8])
                            tx_pkts = int(parts[9])
                            current[iface] = (rx_bytes, rx_pkts, tx_bytes, tx_pkts)
        except Exception:
            pass

        iface = "vmbr0" if "vmbr0" in current else ("nic0" if "nic0" in current else "lo")
        rx_b, rx_p, tx_b, tx_p = current.get(iface, (0, 0, 0, 0))
        prev = self.prev_net.get(iface)
        if prev and dt > 0:
            rx_rate = max(0.0, (rx_b - prev[0]) / dt)
            tx_rate = max(0.0, (tx_b - prev[2]) / dt)
        else:
            rx_rate = 0.0
            tx_rate = 0.0
        self.prev_net = current

        speed = "1000 Mbps"
        try:
            with open("/sys/class/net/nic0/speed", "r", encoding="utf-8") as f:
                speed = f"{f.read().strip()} Mbps"
        except Exception:
            pass

        operstate = "UP"
        try:
            with open(f"/sys/class/net/{iface}/operstate", "r", encoding="utf-8") as f:
                st = f.read().strip().upper()
                if st:
                    operstate = st
        except Exception:
            pass

        ip_addr = "192.168.2.68"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("192.168.2.254", 80))
            ip_addr = s.getsockname()[0]
            s.close()
        except Exception:
            pass

        return {
            "iface": iface,
            "ip": ip_addr,
            "speed": speed,
            "state": operstate,
            "rx_bps": rx_rate,
            "tx_bps": tx_rate,
            "rx_total": rx_b,
            "tx_total": tx_b,
            "rx_pkts": rx_p,
            "tx_pkts": tx_p,
        }

    def _read_pve_resources(self):
        """Query Proxmox VE API using read-only PVEAuditor token over persistent connection."""
        vms = []
        storages = []
        api_ok = False

        if self.token_header:
            for _ in range(2):
                try:
                    if self.http_conn is None:
                        self.http_conn = http.client.HTTPSConnection(
                            PVE_HOST, PVE_PORT, context=self.ssl_ctx, timeout=2.0
                        )
                    self.http_conn.request(
                        "GET",
                        "/api2/json/cluster/resources",
                        headers={"Authorization": self.token_header, "Connection": "keep-alive"},
                    )
                    resp = self.http_conn.getresponse()
                    raw = resp.read()
                    if resp.status == 200:
                        payload = json.loads(raw).get("data", [])
                        api_ok = True
                        for item in payload:
                            itype = item.get("type")
                            if itype in ("qemu", "lxc"):
                                vms.append({
                                    "vmid": int(item.get("vmid", 0)),
                                    "name": item.get("name", f"VM-{item.get('vmid', 0)}"),
                                    "type": "QEMU" if itype == "qemu" else "LXC",
                                    "status": item.get("status", "unknown").upper(),
                                    "lock": str(item.get("lock", "")).strip().lower() if item.get("lock") else "",
                                    "qmpstatus": str(item.get("qmpstatus", "")).strip().lower() if item.get("qmpstatus") else "",
                                    "cpu_pct": float(item.get("cpu", 0.0)) * 100.0,
                                    "maxcpu": int(item.get("maxcpu", 1)),
                                    "mem": int(item.get("mem", 0)),
                                    "maxmem": int(item.get("maxmem", 0)),
                                    "uptime": int(item.get("uptime", 0)),
                                    "netin": int(item.get("netin", 0)),
                                    "netout": int(item.get("netout", 0)),
                                })
                            elif itype == "storage":
                                storages.append(item)
                        break
                    else:
                        self.http_conn.close()
                        self.http_conn = None
                except Exception:
                    if self.http_conn:
                        try:
                            self.http_conn.close()
                        except Exception:
                            pass
                    self.http_conn = None

        vms.sort(key=lambda x: x["vmid"])
        return vms, storages, api_ok

    def _get_username(self, uid):
        if uid not in self.uid_cache:
            try:
                self.uid_cache[uid] = pwd.getpwuid(uid).pw_name
            except Exception:
                self.uid_cache[uid] = str(uid)
        return self.uid_cache[uid]

    def _read_processes(self, dt, ram_total=16 * 1024**3):
        procs = []
        new_proc_times = {}
        running_count = 0
        total_count = 0

        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            pid = int(entry)
            total_count += 1
            pdir = f"/proc/{entry}"
            try:
                with open(f"{pdir}/stat", "r", encoding="utf-8", errors="replace") as f:
                    stat_str = f.read()
                rparen = stat_str.rfind(")")
                lparen = stat_str.find("(")
                comm = stat_str[lparen + 1 : rparen]
                fields = stat_str[rparen + 2 :].split()
                state = fields[0]
                if state == "R":
                    running_count += 1
                utime = int(fields[11])
                stime = int(fields[12])
                rss_pages = int(fields[21])
                proc_ticks = utime + stime
                new_proc_times[pid] = proc_ticks

                cpu_pct = 0.0
                if pid in self.prev_proc and dt > 0:
                    delta_ticks = max(0, proc_ticks - self.prev_proc[pid])
                    cpu_pct = (delta_ticks / self.clk_tck) * 100.0 / dt

                rss_bytes = max(0, rss_pages * self.page_size)
                mem_pct = (rss_bytes * 100.0 / ram_total) if ram_total > 0 else 0.0

                st_uid = os.stat(pdir).st_uid
                user = self._get_username(st_uid)

                cmdline = comm
                try:
                    with open(f"{pdir}/cmdline", "rb") as cf:
                        raw_cmd = cf.read(200).replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
                        if raw_cmd:
                            cmdline = raw_cmd
                except Exception:
                    pass

                procs.append({
                    "pid": pid,
                    "user": user[:10],
                    "state": state,
                    "cpu": cpu_pct,
                    "mem_pct": mem_pct,
                    "rss": rss_bytes,
                    "cmd": cmdline,
                })
            except Exception:
                continue

        self.prev_proc = new_proc_times
        procs.sort(key=lambda x: (round(x["cpu"], 1), x["rss"]), reverse=True)
        return procs, total_count, running_count

    def collect(self):
        now = time.monotonic()
        dt = max(0.1, now - self.prev_time)
        self.prev_time = now

        uptime_sec = 0.0
        try:
            with open("/proc/uptime", "r", encoding="utf-8") as f:
                uptime_sec = float(f.read().split()[0])
        except Exception:
            pass

        load_avg = (0.0, 0.0, 0.0)
        try:
            load_avg = os.getloadavg()
        except Exception:
            pass

        cpu_usages, cpu_freqs = self._read_cpu_times()
        temps = self._read_temperatures()
        mem = self._read_memory()
        vms, pve_storages, api_ok = self._read_pve_resources()
        disks, kingston_info = self._read_storage(pve_storages)
        disk_io = self._read_disk_io(dt)
        net_io = self._read_net_io(dt)
        procs, total_procs, running_procs = self._read_processes(dt, mem["ram_total"])

        return {
            "timestamp": datetime.now().strftime("%d-%m-%Y  %H:%M:%S"),
            "uptime": uptime_sec,
            "load": load_avg,
            "cpu_usages": cpu_usages,
            "cpu_freqs": cpu_freqs,
            "temps": temps,
            "mem": mem,
            "disks": disks,
            "kingston": kingston_info,
            "disk_io": disk_io,
            "net": net_io,
            "vms": vms,
            "api_ok": api_ok,
            "procs": procs,
            "total_procs": total_procs,
            "running_procs": running_procs,
            "pi5": self._get_pi5_snapshot(),
            "ha": self._get_ha_snapshot(),
            "weather": self._get_weather_snapshot(),
        }


# =========================================================================
# Curses Color Pair IDs & Semantic Theme
# =========================================================================
C_CYAN = 1          # Primary MLHK Electric Cyan (borders, headings)
C_MAGENTA = 2       # Secondary MLHK Neon Magenta (logo, accents)
C_GREEN = 3         # Laag verbruik (<60%) / ONLINE
C_ORANGE = 4        # Medium verbruik (60-80%)
C_RED = 5           # Hoog verbruik (>80%) / OFFLINE
C_WHITE = 6         # Crisp white values
C_GREY = 7          # Soft grey labels
C_DARK_GREY = 8     # Dark slate empty bar tracks
C_BADGE_ONLINE = 9  # Glowing green badge: [ ● ONLINE ]
C_BADGE_OFFLINE = 10# Red badge: [ ○ OFFLINE ]
C_BADGE_CYAN = 11   # Cyan pill
C_BADGE_MAGENTA = 12# Magenta pill
C_ALERT_RAIN = 13   # Blue/Cyan background for active rain
C_ALERT_EXTREME = 14# Red background for extreme weather alert
C_BADGE_REBOOT = 15 # Orange background for rebooting VM [ ↻ REBOOT ]
C_ROW_ALARM_FLASH = 16 # Pulsing alarm background (White on Red)
C_ROW_REBOOT = 17   # Orange accent for rebooting VM row
C_DOOR_POPUP_RED = 18    # Flash 1 for Door popup (White on Red)
C_DOOR_POPUP_AMBER = 19  # Flash 2 for Door popup (Black on Yellow/Orange)


def init_colors():
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(C_CYAN, curses.COLOR_CYAN, curses.COLOR_BLACK)
    curses.init_pair(C_MAGENTA, curses.COLOR_MAGENTA, curses.COLOR_BLACK)
    curses.init_pair(C_GREEN, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(C_ORANGE, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    curses.init_pair(C_RED, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(C_WHITE, curses.COLOR_WHITE, curses.COLOR_BLACK)
    curses.init_pair(C_GREY, curses.COLOR_WHITE, curses.COLOR_BLACK)
    curses.init_pair(C_DARK_GREY, curses.COLOR_BLACK, curses.COLOR_BLACK)
    curses.init_pair(C_BADGE_ONLINE, curses.COLOR_BLACK, curses.COLOR_GREEN)
    curses.init_pair(C_BADGE_OFFLINE, curses.COLOR_WHITE, curses.COLOR_RED)
    curses.init_pair(C_BADGE_CYAN, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(C_BADGE_MAGENTA, curses.COLOR_WHITE, curses.COLOR_MAGENTA)
    curses.init_pair(C_ALERT_RAIN, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(C_ALERT_EXTREME, curses.COLOR_WHITE, curses.COLOR_RED)
    curses.init_pair(C_BADGE_REBOOT, curses.COLOR_BLACK, curses.COLOR_YELLOW)
    curses.init_pair(C_ROW_ALARM_FLASH, curses.COLOR_WHITE, curses.COLOR_RED)
    curses.init_pair(C_ROW_REBOOT, curses.COLOR_BLACK, curses.COLOR_YELLOW)
    curses.init_pair(C_DOOR_POPUP_RED, curses.COLOR_WHITE, curses.COLOR_RED)
    curses.init_pair(C_DOOR_POPUP_AMBER, curses.COLOR_BLACK, curses.COLOR_YELLOW)


def get_usage_pair(pct, warn=60.0, crit=80.0):
    """Return green (<60%), orange (60-80%), or red (>80%) based on utilization."""
    if pct < warn:
        return curses.color_pair(C_GREEN)
    elif pct < crit:
        return curses.color_pair(C_ORANGE)
    else:
        return curses.color_pair(C_RED)


def safe_addstr(stdscr, y, x, text, attr=0):
    h, w = stdscr.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    if x < 0:
        text = text[-x:]
        x = 0
    max_len = w - x
    if y == h - 1:
        max_len -= 1
    if max_len <= 0:
        return
    try:
        stdscr.addstr(y, x, text[:max_len], attr)
    except curses.error:
        pass


def safe_box_str(stdscr, y, x, max_x, text, attr=0):
    avail = max_x - x
    if avail <= 0:
        return
    safe_addstr(stdscr, y, x, text[:avail], attr)


def draw_box(stdscr, y, x, h, w, title="", right_tag=""):
    """Draw a sleek MLHK Cyberpunk panel with Cyan borders and Magenta title accents."""
    max_h, max_w = stdscr.getmaxyx()
    if y >= max_h or x >= max_w or h < 2 or w < 4:
        return
    h = min(h, max_h - y)
    w = min(w, max_w - x)

    border_attr = curses.color_pair(C_CYAN)
    title_attr = curses.color_pair(C_WHITE) | curses.A_BOLD
    accent_attr = curses.color_pair(C_MAGENTA) | curses.A_BOLD
    grey_attr = curses.color_pair(C_GREY)

    top = "┌" + ("─" * (w - 2)) + "┐"
    bot = "└" + ("─" * (w - 2)) + "┘"
    inner_spaces = " " * max(0, w - 2)
    safe_addstr(stdscr, y, x, top, border_attr)
    for row in range(y + 1, y + h - 1):
        safe_addstr(stdscr, row, x, "│", border_attr)
        if w > 2:
            safe_addstr(stdscr, row, x + 1, inner_spaces, 0)
        safe_addstr(stdscr, row, x + w - 1, "│", border_attr)
    safe_addstr(stdscr, y + h - 1, x, bot, border_attr)

    if title:
        t_trim = title[: max(1, w - 8)]
        safe_addstr(stdscr, y, x + 2, "┤ ", accent_attr)
        safe_addstr(stdscr, y, x + 4, t_trim, title_attr)
        safe_addstr(stdscr, y, x + 4 + len(t_trim), " ├", accent_attr)

    if right_tag and len(title) + len(right_tag) + 14 < w:
        rx = x + w - len(right_tag) - 6
        safe_addstr(stdscr, y, rx, "┤ ", accent_attr)
        safe_addstr(stdscr, y, rx + 2, right_tag, grey_attr)
        safe_addstr(stdscr, y, rx + 2 + len(right_tag), " ├", accent_attr)


def draw_divider(stdscr, y, x, w):
    if w < 3:
        return
    border_attr = curses.color_pair(C_CYAN)
    line_attr = curses.color_pair(C_GREY)
    safe_addstr(stdscr, y, x, "├", border_attr)
    safe_addstr(stdscr, y, x + 1, "─" * (w - 2), line_attr)
    safe_addstr(stdscr, y, x + w - 1, "┤", border_attr)


def draw_bar(stdscr, y, x, width, pct, warn_pct=60.0, crit_pct=80.0):
    """
    Render progress bar:
      <60% = Groen
      60-80% = Oranje
      >80% = Rood
    """
    if width < 4:
        return
    pct = max(0.0, min(100.0, pct))
    inner = width - 2
    filled = int(round((pct / 100.0) * inner))
    filled = max(0, min(inner, filled))
    empty = inner - filled

    bracket_attr = curses.color_pair(C_GREY)
    fill_attr = get_usage_pair(pct, warn_pct, crit_pct) | curses.A_BOLD

    safe_addstr(stdscr, y, x, "[", bracket_attr)
    if filled > 0:
        safe_addstr(stdscr, y, x + 1, "█" * filled, fill_attr)
    if empty > 0:
        safe_addstr(stdscr, y, x + 1 + filled, " " * empty, 0)
    safe_addstr(stdscr, y, x + 1 + inner, "]", bracket_attr)


def render_portrait_dashboard(stdscr, collector, data):
    """
    Render the vertical portrait dashboard (1080x1920 / 90x80 portrait console).
    Incorporates the official mlhk.nl Cyberpunk logo in Electric Cyan & Neon Magenta,
    and 3-tier health coloring: Groen (<60%), Oranje (60-80%), Rood (>80%).
    """
    # Overschrijf cellen in-place (geen stdscr.erase/clear) om flikkeren te voorkomen
    h, w = stdscr.getmaxyx()
    max_x = w - 1

    c_cyan = curses.color_pair(C_CYAN) | curses.A_BOLD
    c_mag = curses.color_pair(C_MAGENTA) | curses.A_BOLD
    c_white = curses.color_pair(C_WHITE) | curses.A_BOLD
    c_grey = curses.color_pair(C_GREY)

    cur_y = 0

    # ══════════════════════════════════════════════════════════════════════
    # BOVENAAN — OFFICIAL MLHK.NL LOGO HEADER (9 rows)
    # ══════════════════════════════════════════════════════════════════════
    hdr_h = 9
    safe_addstr(stdscr, cur_y, 0, "╔" + ("═" * max(0, w - 2)) + "╗", c_cyan)
    hdr_inner = " " * max(0, w - 2)
    for r in range(1, hdr_h - 1):
        safe_addstr(stdscr, cur_y + r, 0, "║", c_cyan)
        if w > 2:
            safe_addstr(stdscr, cur_y + r, 1, hdr_inner, 0)
        safe_addstr(stdscr, cur_y + r, w - 1, "║", c_cyan)
    safe_addstr(stdscr, cur_y + hdr_h - 1, 0, "╚" + ("═" * max(0, w - 2)) + "╝", c_cyan)

    # Stylized MLHK Logo Block Font (5 rows)
    # Total width: 11 (M) + 2 + 8 (L) + 2 + 8 (H) + 2 + 8 (K) = 41 chars
    logo_m = ["███╗   ███╗", "████╗ ████║", "██╔████╔██║", "██║╚██╔╝██║", "██║ ╚═╝ ██║"]
    logo_l = ["██╗     ",    "██║     ",    "██║     ",    "██║     ",    "███████╗"]
    logo_h = ["██╗  ██╗",    "██║  ██║",    "███████║",    "██╔══██║",    "██║  ██║"]
    logo_k = ["██╗  ██╗",    "██║ ██╔╝",    "█████═╝ ",    "██╔═██╗ ",    "██║ ╚██╗"]

    lx = (w - 41) // 2  # centered at x = 24 in a 90-column terminal

    for r in range(5):
        # M: Cyan on outer edges, Neon Magenta in inner arch
        m_r = logo_m[r]
        safe_addstr(stdscr, cur_y + 1 + r, lx, m_r[:4], c_cyan)
        safe_addstr(stdscr, cur_y + 1 + r, lx + 4, m_r[4:7], c_mag)
        safe_addstr(stdscr, cur_y + 1 + r, lx + 7, m_r[7:], c_cyan)

        # L: Neon Magenta vertical stem, Cyan horizontal base
        l_r = logo_l[r]
        if r == 4:
            safe_addstr(stdscr, cur_y + 1 + r, lx + 13, l_r[:2], c_mag)
            safe_addstr(stdscr, cur_y + 1 + r, lx + 15, l_r[2:], c_cyan)
        else:
            safe_addstr(stdscr, cur_y + 1 + r, lx + 13, l_r, c_mag)

        # H: Neon Magenta left spine, Electric Cyan right spine & crossbar
        h_r = logo_h[r]
        safe_addstr(stdscr, cur_y + 1 + r, lx + 23, h_r[:4], c_mag)
        safe_addstr(stdscr, cur_y + 1 + r, lx + 27, h_r[4:], c_cyan)

        # K: Neon Magenta vertical spine, Electric Cyan diagonal arms
        k_r = logo_k[r]
        safe_addstr(stdscr, cur_y + 1 + r, lx + 33, k_r[:3], c_mag)
        safe_addstr(stdscr, cur_y + 1 + r, lx + 36, k_r[3:], c_cyan)

    # Decorative side text in header (left and right of logo)
    safe_addstr(stdscr, cur_y + 2, 3, "MLHK.NL", c_mag)
    safe_addstr(stdscr, cur_y + 3, 3, "INFRASTRUCTURE", c_cyan)
    safe_addstr(stdscr, cur_y + 4, 3, "SERVER MONITOR", c_white)

    safe_addstr(stdscr, cur_y + 2, w - 16, "PORTRAIT 1080p", c_cyan)
    safe_addstr(stdscr, cur_y + 3, w - 16, "27-INCH DISPLAY", c_white)
    safe_addstr(stdscr, cur_y + 4, w - 16, "PROXMOX VE 9.2", c_mag)

    # Row 6: Divider
    safe_addstr(stdscr, cur_y + 6, 0, "╟" + ("─" * max(0, w - 2)) + "╢", curses.color_pair(C_CYAN))

    # Row 7: Host metadata + ONLINE (GROEN) / OFFLINE (ROOD) Badge
    safe_box_str(stdscr, cur_y + 7, 3, max_x, "SERVER: ", c_grey)
    safe_box_str(stdscr, cur_y + 7, 11, max_x, SERVER_DISPLAY_NAME, c_white)
    mid_meta = f"│  UPTIME: {format_uptime(data['uptime'])}"
    safe_box_str(stdscr, cur_y + 7, 11 + len(SERVER_DISPLAY_NAME) + 2, max_x, mid_meta, c_white)

    date_str = f"DATUM: {data['timestamp']}"
    safe_addstr(stdscr, cur_y + 7, 48, date_str, c_white)

    # MODE ONLINE GROEN / OFFLINE ROOD
    api_ok = data.get("api_ok", True)
    if api_ok:
        status_pill = " ● ONLINE "
        pill_attr = curses.color_pair(C_BADGE_ONLINE) | curses.A_BOLD
    else:
        status_pill = " ○ OFFLINE "
        pill_attr = curses.color_pair(C_BADGE_OFFLINE) | curses.A_BOLD
    safe_addstr(stdscr, cur_y + 7, w - len(status_pill) - 3, status_pill, pill_attr)
    cur_y += hdr_h

    # ══════════════════════════════════════════════════════════════════════
    # BANNER 1 — HOST 1: SMURF-SERVER
    # ══════════════════════════════════════════════════════════════════════
    banner1 = " ══ [ SERVER 1 ] SMURF-SERVER : PROXMOX VE HYPERVISOR ══ "
    safe_addstr(stdscr, cur_y, 0, "═" * max(0, w - 1), c_cyan)
    safe_addstr(stdscr, cur_y, max(2, (w - len(banner1)) // 2), banner1, curses.color_pair(C_BADGE_CYAN) | curses.A_BOLD)
    cur_y += 1

    # ══════════════════════════════════════════════════════════════════════
    # SECTIE 1 — HOST STATUS (CPU, Temperatuur, Load, RAM, Swap) (11 rows)
    # ══════════════════════════════════════════════════════════════════════
    sec1_h = 11
    usages = data["cpu_usages"]
    freqs = data["cpu_freqs"]
    temps = data["temps"]
    total_cpu = usages.get("cpu", 0.0)
    avg_freq = (sum(freqs) / len(freqs)) if freqs else 3300.0
    pkg_temp = temps.get("pkg")
    pkg_str = f"{pkg_temp:4.1f}°C" if pkg_temp is not None else "N/A"
    l1, l5, l15 = data["load"]

    short_cpu = collector.cpu_model.replace("Intel(R) Core(TM) ", "Intel Core ").replace(" CPU ", " ")
    draw_box(
        stdscr,
        cur_y,
        0,
        sec1_h,
        w,
        "SECTIE 1 — HOST STATUS",
        f"{short_cpu[:36]}",
    )

    bar_w = max(14, w - 58)

    # Row 1: CPU Totaal
    safe_box_str(stdscr, cur_y + 1, 3, max_x, "CPU Totaal   :", c_white)
    draw_bar(stdscr, cur_y + 1, 18, bar_w, total_cpu)
    cpu_col = get_usage_pair(total_cpu) | curses.A_BOLD
    t_pkg_col = curses.color_pair(C_GREEN) if (pkg_temp or 0) < 60 else (curses.color_pair(C_ORANGE) if (pkg_temp or 0) < 75 else curses.color_pair(C_RED))
    safe_box_str(stdscr, cur_y + 1, 19 + bar_w, max_x, f"{total_cpu:5.1f}%", cpu_col)
    safe_box_str(stdscr, cur_y + 1, 26 + bar_w, max_x, f"│  {avg_freq:4.0f} MHz  │  Temp: ", c_white)
    safe_box_str(stdscr, cur_y + 1, 48 + bar_w, max_x, pkg_str, t_pkg_col | curses.A_BOLD)

    # Rows 2..5: CPU per core (Core 0..3)
    for c_idx in range(4):
        ry = cur_y + 2 + c_idx
        c_pct = usages.get(f"cpu{c_idx}", 0.0)
        c_freq = freqs[c_idx] if c_idx < len(freqs) else avg_freq
        c_temp = temps["cores"].get(c_idx)
        t_str = f"{c_temp:4.1f}°C" if c_temp is not None else "  -   "
        c_t_col = curses.color_pair(C_GREEN) if (c_temp or 0) < 60 else (curses.color_pair(C_ORANGE) if (c_temp or 0) < 75 else curses.color_pair(C_RED))

        branch = "└─" if c_idx == 3 else "├─"
        safe_box_str(stdscr, ry, 3, max_x, f"  {branch} Core {c_idx}  :", c_grey)
        draw_bar(stdscr, ry, 18, bar_w, c_pct)
        safe_box_str(stdscr, ry, 19 + bar_w, max_x, f"{c_pct:5.1f}%", get_usage_pair(c_pct) | curses.A_BOLD)
        safe_box_str(stdscr, ry, 26 + bar_w, max_x, f"│  {c_freq:4.0f} MHz  │  Core: ", c_grey)
        safe_box_str(stdscr, ry, 48 + bar_w, max_x, t_str, c_t_col | curses.A_BOLD)

    # Row 6: CPU Temperatuur + Load Average
    safe_box_str(stdscr, cur_y + 6, 3, max_x, "CPU Temp/Load:", c_white)
    draw_bar(stdscr, cur_y + 6, 18, bar_w, pkg_temp or 0.0, warn_pct=60.0, crit_pct=75.0)
    safe_box_str(stdscr, cur_y + 6, 19 + bar_w, max_x, f"{pkg_str:>6}", t_pkg_col | curses.A_BOLD)
    load_col = curses.color_pair(C_GREEN) if l1 < 2.5 else (curses.color_pair(C_ORANGE) if l1 < 4.0 else curses.color_pair(C_RED))
    safe_box_str(stdscr, cur_y + 6, 26 + bar_w, max_x, "│  Load: ", c_white)
    safe_box_str(stdscr, cur_y + 6, 35 + bar_w, max_x, f"{l1:.2f} / {l5:.2f} / {l15:.2f}", load_col | curses.A_BOLD)

    # Row 7: Divider between CPU and Memory
    draw_divider(stdscr, cur_y + 7, 0, w)

    # Row 8: RAM Gebruik
    m = data["mem"]
    safe_box_str(stdscr, cur_y + 8, 3, max_x, "RAM Gebruik  :", c_white)
    draw_bar(stdscr, cur_y + 8, 18, bar_w, m["ram_pct"])
    ram_col = get_usage_pair(m["ram_pct"]) | curses.A_BOLD
    safe_box_str(stdscr, cur_y + 8, 19 + bar_w, max_x, f"{m['ram_pct']:5.1f}%", ram_col)
    safe_box_str(stdscr, cur_y + 8, 26 + bar_w, max_x, f"│ {format_bytes(m['ram_used'], 2)} / {format_bytes(m['ram_total'], 2)}", c_white)

    # Row 9: Swap & Hypervisor
    safe_box_str(stdscr, cur_y + 9, 3, max_x, "Swap & Systeem:", c_grey)
    draw_bar(stdscr, cur_y + 9, 18, bar_w, m["swap_pct"], warn_pct=30.0, crit_pct=60.0)
    swap_col = get_usage_pair(m["swap_pct"], 30.0, 60.0) | curses.A_BOLD
    safe_box_str(stdscr, cur_y + 9, 19 + bar_w, max_x, f"{m['swap_pct']:5.1f}%", swap_col)
    safe_box_str(stdscr, cur_y + 9, 26 + bar_w, max_x, f"│ Vrij: {format_bytes(m['ram_avail'], 1)} │ PVE 9.2 (Linux {collector.kernel_release[:14]})", c_grey)

    cur_y += sec1_h

    # ══════════════════════════════════════════════════════════════════════
    # SECTIE 2 — OPSLAG (Samsung 250GB & Kingston 240GB) (4 rows)
    # ══════════════════════════════════════════════════════════════════════
    sec2_h = 4
    draw_box(
        stdscr,
        cur_y,
        0,
        sec2_h,
        w,
        "SECTIE 2 — OPSLAG (DISKS)",
        "Samsung SSD 250GB + Kingston SSD 240GB",
    )

    s_bar_w = max(10, w - 74)

    # Disk 1: Samsung SSD 960 EVO 250GB
    d1 = data["disks"][0]
    nvme_io = data["disk_io"].get("nvme0n1", {"read_bps": 0.0, "write_bps": 0.0})
    ry1 = cur_y + 1
    safe_box_str(stdscr, ry1, 3, max_x, f"{d1['name']:<15}:", c_white)
    draw_bar(stdscr, ry1, 19, s_bar_w, d1["pct"])
    d1_col = get_usage_pair(d1["pct"]) | curses.A_BOLD
    safe_box_str(stdscr, ry1, 20 + s_bar_w, max_x, f"{d1['pct']:5.1f}%", d1_col)
    safe_box_str(
        stdscr,
        ry1,
        27 + s_bar_w,
        max_x,
        f"│ Gebr: {format_bytes(d1['used'], 1):>7} / {format_bytes(d1['total'], 1):<7} │ R: {format_rate(nvme_io['read_bps']):<8} │ W: {format_rate(nvme_io['write_bps']):<8}",
        c_white,
    )

    # Disk 2: Kingston SSD 240GB
    d2 = data["disks"][1]
    sda_io = data["disk_io"].get("sda", {"read_bps": 0.0, "write_bps": 0.0})
    ry2 = cur_y + 2
    safe_box_str(stdscr, ry2, 3, max_x, f"{d2['name']:<15}:", c_cyan)
    draw_bar(stdscr, ry2, 19, s_bar_w, d2["pct"])
    d2_col = get_usage_pair(d2["pct"]) | curses.A_BOLD
    safe_box_str(stdscr, ry2, 20 + s_bar_w, max_x, f"{d2['pct']:5.1f}%", d2_col)
    safe_box_str(
        stdscr,
        ry2,
        27 + s_bar_w,
        max_x,
        f"│ Gebr: {format_bytes(d2['used'], 1):>7} / {format_bytes(d2['total'], 1):<7} │ R: {format_rate(sda_io['read_bps']):<8} │ W: {format_rate(sda_io['write_bps']):<8}",
        c_white,
    )

    cur_y += sec2_h

    # ══════════════════════════════════════════════════════════════════════
    # SECTIE 3 — NETWERK (5 rows)
    # ══════════════════════════════════════════════════════════════════════
    sec3_h = 5
    net = data["net"]
    link_ok = (net["state"] == "UP")
    draw_box(
        stdscr,
        cur_y,
        0,
        sec3_h,
        w,
        "SECTIE 3 — NETWERK",
        f"Interface {net['iface']} ({net['state']} @ {net['speed']})",
    )

    rx_pct = min(100.0, (net["rx_bps"] / (12.5 * 1024 * 1024)) * 100.0)
    tx_pct = min(100.0, (net["tx_bps"] / (12.5 * 1024 * 1024)) * 100.0)

    safe_box_str(stdscr, cur_y + 1, 3, max_x, "IP & Gateway   :", c_grey)
    safe_box_str(
        stdscr,
        cur_y + 1,
        19,
        max_x,
        f"{net['ip']}/24 │ Gateway: 192.168.2.254 │ Web: https://{net['ip']}:8006",
        c_white,
    )
    safe_box_str(stdscr, cur_y + 2, 3, max_x, "Download (RX)  :", c_white)
    draw_bar(stdscr, cur_y + 2, 19, s_bar_w, rx_pct)
    safe_box_str(
        stdscr,
        cur_y + 2,
        20 + s_bar_w,
        max_x,
        f"{format_rate(net['rx_bps'])} │ Totaal RX: {format_bytes(net['rx_total'], 2)}",
        curses.color_pair(C_GREEN) | curses.A_BOLD,
    )
    safe_box_str(stdscr, cur_y + 3, 3, max_x, "Upload   (TX)  :", c_white)
    draw_bar(stdscr, cur_y + 3, 19, s_bar_w, tx_pct)
    safe_box_str(
        stdscr,
        cur_y + 3,
        20 + s_bar_w,
        max_x,
        f"{format_rate(net['tx_bps'])} │ Totaal TX: {format_bytes(net['tx_total'], 2)}",
        c_cyan,
    )

    cur_y += sec3_h

    # ══════════════════════════════════════════════════════════════════════
    # SECTIE 4 — VM STATUS (Proxmox VE Virtuele Machines & Containers)
    # Groeit dynamisch mee met het aantal VM's; alleen Sectie 5 (Processen)
    # krimpt wanneer er meer VM's worden toegevoegd.
    # ══════════════════════════════════════════════════════════════════════
    vms = data["vms"]
    running_vms = sum(1 for v in vms if v["status"] == "RUNNING" and not (v.get("lock") in ("reboot", "restart") or "reboot" in v["status"].lower() or "reset" in v.get("qmpstatus", "")))
    rebooting_vms = sum(1 for v in vms if (v.get("lock") in ("reboot", "restart") or "reboot" in v["status"].lower() or "reset" in v.get("qmpstatus", "")))
    stopped_vms = sum(1 for v in vms if v["status"] in ("STOPPED", "OFFLINE") and not (v.get("lock") in ("reboot", "restart") or "reboot" in v["status"].lower()))

    # Reserveer voldoende ruimte voor Server 2 (HA: 11 rows), Server 3 (Pi: 16 rows),
    # en Weer (Banner + Box: minimaal 7 rows) + Footer (1 row) = 35 rijen
    min_lower_reserved = 35
    max_sec4_h = max(6, (h - cur_y - 1) - min_lower_reserved)
    max_vm_content_rows = max(2, max_sec4_h - 4)

    if not vms:
        vm_content_rows = 2
        compact_vm_mode = False
    elif len(vms) * 2 <= max_vm_content_rows:
        vm_content_rows = len(vms) * 2
        compact_vm_mode = False
    else:
        vm_content_rows = min(max_vm_content_rows, len(vms))
        compact_vm_mode = True

    sec4_h = vm_content_rows + 4

    pve_tag = f"Totaal: {len(vms)} │ Running: {running_vms}"
    if rebooting_vms > 0:
        pve_tag += f" │ Reboot: {rebooting_vms}"
    pve_tag += f" │ Stopped: {stopped_vms}"

    draw_box(
        stdscr,
        cur_y,
        0,
        sec4_h,
        w,
        "SECTIE 4 — PROXMOX VM STATUS",
        pve_tag,
    )

    safe_box_str(
        stdscr,
        cur_y + 1,
        3,
        max_x,
        f"{'VM-ID':<7} │ {'NAAM':<22} │ {'STATUS':<11} │ {'CPU & GEHEUGEN (RAM) OVERZICHT'}",
        c_cyan,
    )
    draw_divider(stdscr, cur_y + 2, 0, w)

    # Flashing effect voor OFFLINE / CRITIEKE VM's (pulserend alarm 2Hz)
    alarm_flash_on = (int(time.monotonic() * 2) % 2 == 0)

    if not vms:
        safe_box_str(
            stdscr,
            cur_y + 3,
            4,
            max_x,
            "■ Geen VM's of containers gevonden op Proxmox-node 'pve'.",
            c_white,
        )
        safe_box_str(
            stdscr,
            cur_y + 4,
            4,
            max_x,
            "  Gereed voor o.a. HabNight, MLHK-Hosting en AzuraCast.",
            c_grey,
        )
    elif not compact_vm_mode:
        max_vms_shown = (sec4_h - 4) // 2
        v_bar_w = max(8, (w - 64) // 2)
        for idx, vm in enumerate(vms[:max_vms_shown]):
            ry1 = cur_y + 3 + (idx * 2)
            ry2 = ry1 + 1

            v_raw_status = vm.get("status", "UNKNOWN").upper()
            v_lock = vm.get("lock", "")
            v_qmp = vm.get("qmpstatus", "")

            # Detecteer rebooting, offline, en kritieke toestand
            is_rebooting = (
                v_lock in ("reboot", "restart")
                or "reboot" in v_raw_status.lower()
                or "reset" in v_qmp
            )
            is_offline = (
                v_raw_status in ("STOPPED", "OFFLINE")
                and not is_rebooting
            )
            is_crit = (
                (vm["cpu_pct"] >= 95.0 or (vm["maxmem"] > 0 and (vm["mem"] * 100.0 / vm["maxmem"]) >= 95.0))
                and not is_offline
                and not is_rebooting
            )
            is_run = (v_raw_status == "RUNNING" and not is_rebooting)

            ram_pct = min(100.0, (vm["mem"] * 100.0 / vm["maxmem"])) if vm["maxmem"] > 0 else 0.0
            if is_rebooting:
                up_str = "Herstarten..."
            elif is_run:
                up_str = format_uptime(vm["uptime"])
            else:
                up_str = "Gestopt"

            # Achtergrond styling:
            # - Rebooting: hele regel oranje achtergrond
            # - Offline / Kritiek: pulsend flashy alarm (rood/wit flitsend)
            if is_offline or is_crit:
                card_bg = (curses.color_pair(C_ROW_ALARM_FLASH) | curses.A_BOLD) if alarm_flash_on else 0
            elif is_rebooting:
                card_bg = curses.color_pair(C_ROW_REBOOT) | curses.A_BOLD
            else:
                card_bg = 0

            if card_bg:
                row_fill = " " * max(0, w - 4)
                safe_addstr(stdscr, ry1, 2, row_fill, card_bg)
                safe_addstr(stdscr, ry2, 2, row_fill, card_bg)

            # Line 1 of VM card: ID, Name, Status Pill
            id_col = card_bg if card_bg else c_white
            pipe_col = card_bg if card_bg else c_grey
            name_col = card_bg if card_bg else c_white

            safe_box_str(stdscr, ry1, 3, max_x, f"#{vm['vmid']:<6}", id_col)
            safe_box_str(stdscr, ry1, 11, max_x, "│ ", pipe_col)
            safe_box_str(stdscr, ry1, 13, max_x, f"{vm['name'][:20]:<20}", name_col)
            safe_box_str(stdscr, ry1, 36, max_x, "│ ", pipe_col)

            if is_rebooting:
                safe_box_str(stdscr, ry1, 38, max_x, " ↻ REBOOT  ", curses.color_pair(C_BADGE_REBOOT) | curses.A_BOLD)
            elif is_offline:
                badge_style = (curses.color_pair(C_ROW_ALARM_FLASH) | curses.A_BOLD) if alarm_flash_on else (curses.color_pair(C_BADGE_OFFLINE) | curses.A_BOLD)
                safe_box_str(stdscr, ry1, 38, max_x, " ⚠ OFFLINE ", badge_style)
            elif is_crit:
                badge_style = (curses.color_pair(C_ROW_ALARM_FLASH) | curses.A_BOLD) if alarm_flash_on else (curses.color_pair(C_ALERT_EXTREME) | curses.A_BOLD)
                safe_box_str(stdscr, ry1, 38, max_x, " ! CRITIEK ", badge_style)
            elif is_run:
                safe_box_str(stdscr, ry1, 38, max_x, " ● RUNNING ", curses.color_pair(C_BADGE_ONLINE) | curses.A_BOLD)
            else:
                safe_box_str(stdscr, ry1, 38, max_x, f" ○ {v_raw_status[:7]:<7} ", curses.color_pair(C_BADGE_OFFLINE) | curses.A_BOLD)

            meta_style = card_bg if card_bg else (c_white if is_run else c_grey)
            safe_box_str(
                stdscr,
                ry1,
                50,
                max_x,
                f"│ {vm['type']} ({vm['maxcpu']} vCPU) │ Uptime: {up_str}",
                meta_style,
            )

            # Line 2 of VM card: CPU & RAM Bars
            safe_box_str(stdscr, ry2, 3, max_x, "        │ ", pipe_col)
            safe_box_str(stdscr, ry2, 13, max_x, "CPU:", card_bg if card_bg else c_grey)
            draw_bar(stdscr, ry2, 18, v_bar_w, vm["cpu_pct"])
            cx = 19 + v_bar_w
            vm_cpu_col = card_bg if card_bg else (get_usage_pair(vm["cpu_pct"]) | curses.A_BOLD)
            safe_box_str(stdscr, ry2, cx, max_x, f"{vm['cpu_pct']:5.1f}%", vm_cpu_col)
            safe_box_str(stdscr, ry2, cx + 7, max_x, "│ RAM:", pipe_col)
            rx = cx + 15
            draw_bar(stdscr, ry2, rx, v_bar_w, ram_pct)
            vm_ram_col = card_bg if card_bg else (get_usage_pair(ram_pct) | curses.A_BOLD)
            mem_label_col = card_bg if card_bg else c_white
            safe_box_str(
                stdscr,
                ry2,
                rx + v_bar_w + 1,
                max_x,
                f"{format_bytes(vm['mem'], 1)} / {format_bytes(vm['maxmem'], 1)} (",
                mem_label_col,
            )
            safe_box_str(
                stdscr,
                ry2,
                rx + v_bar_w + 1 + len(f"{format_bytes(vm['mem'], 1)} / {format_bytes(vm['maxmem'], 1)} ("),
                max_x,
                f"{ram_pct:3.0f}%",
                vm_ram_col,
            )
            safe_box_str(
                stdscr,
                ry2,
                rx + v_bar_w + 1 + len(f"{format_bytes(vm['mem'], 1)} / {format_bytes(vm['maxmem'], 1)} (") + len(f"{ram_pct:3.0f}%"),
                max_x,
                ")",
                mem_label_col,
            )
    else:
        max_vms_shown = sec4_h - 4
        for idx, vm in enumerate(vms[:max_vms_shown]):
            ry = cur_y + 3 + idx
            v_raw_status = vm.get("status", "UNKNOWN").upper()
            v_lock = vm.get("lock", "")
            v_qmp = vm.get("qmpstatus", "")

            is_rebooting = (
                v_lock in ("reboot", "restart")
                or "reboot" in v_raw_status.lower()
                or "reset" in v_qmp
            )
            is_offline = (
                v_raw_status in ("STOPPED", "OFFLINE")
                and not is_rebooting
            )
            is_crit = (
                (vm["cpu_pct"] >= 95.0 or (vm["maxmem"] > 0 and (vm["mem"] * 100.0 / vm["maxmem"]) >= 95.0))
                and not is_offline
                and not is_rebooting
            )
            is_run = (v_raw_status == "RUNNING" and not is_rebooting)

            ram_pct = (vm["mem"] * 100.0 / vm["maxmem"]) if vm["maxmem"] > 0 else 0.0

            if is_offline or is_crit:
                card_bg = (curses.color_pair(C_ROW_ALARM_FLASH) | curses.A_BOLD) if alarm_flash_on else 0
            elif is_rebooting:
                card_bg = curses.color_pair(C_ROW_REBOOT) | curses.A_BOLD
            else:
                card_bg = 0

            if card_bg:
                row_fill = " " * max(0, w - 4)
                safe_addstr(stdscr, ry, 2, row_fill, card_bg)

            id_col = card_bg if card_bg else c_white
            pipe_col = card_bg if card_bg else c_white
            name_col = card_bg if card_bg else c_white

            safe_box_str(stdscr, ry, 3, max_x, f"#{vm['vmid']:<6}", id_col)
            safe_box_str(stdscr, ry, 11, max_x, f"│ {vm['name'][:20]:<20} │ ", pipe_col)

            if is_rebooting:
                safe_box_str(stdscr, ry, 38, max_x, " ↻ REB ", curses.color_pair(C_BADGE_REBOOT) | curses.A_BOLD)
            elif is_offline:
                badge_style = (curses.color_pair(C_ROW_ALARM_FLASH) | curses.A_BOLD) if alarm_flash_on else (curses.color_pair(C_BADGE_OFFLINE) | curses.A_BOLD)
                safe_box_str(stdscr, ry, 38, max_x, " ⚠ OFF ", badge_style)
            elif is_crit:
                badge_style = (curses.color_pair(C_ROW_ALARM_FLASH) | curses.A_BOLD) if alarm_flash_on else (curses.color_pair(C_ALERT_EXTREME) | curses.A_BOLD)
                safe_box_str(stdscr, ry, 38, max_x, " ! CRT ", badge_style)
            elif is_run:
                safe_box_str(stdscr, ry, 38, max_x, " ● RUN ", curses.color_pair(C_BADGE_ONLINE) | curses.A_BOLD)
            else:
                safe_box_str(stdscr, ry, 38, max_x, " ○ STP ", curses.color_pair(C_BADGE_OFFLINE) | curses.A_BOLD)

            cpu_val_col = card_bg if card_bg else (get_usage_pair(vm["cpu_pct"]) | curses.A_BOLD)
            ram_val_col = card_bg if card_bg else (get_usage_pair(ram_pct) | curses.A_BOLD)
            lbl_col = card_bg if card_bg else c_grey

            safe_box_str(stdscr, ry, 46, max_x, "│ CPU: ", lbl_col)
            safe_box_str(stdscr, ry, 53, max_x, f"{vm['cpu_pct']:5.1f}%", cpu_val_col)
            safe_box_str(stdscr, ry, 60, max_x, f"│ RAM: {format_bytes(vm['mem'], 1)}/{format_bytes(vm['maxmem'], 1)} (", lbl_col)
            safe_box_str(stdscr, ry, 82, max_x, f"{ram_pct:3.0f}%)", ram_val_col)

    cur_y += sec4_h

    # ══════════════════════════════════════════════════════════════════════
    # DUIDELIJKE AFSCHEIDING TUSSEN LOCAL SERVER EN SMART HOME / REMOTE
    # ══════════════════════════════════════════════════════════════════════
    # ══════════════════════════════════════════════════════════════════════
    # BANNER 2 — SERVER 2: HOME ASSISTANT
    # ══════════════════════════════════════════════════════════════════════
    banner2 = " ══ [ SERVER 2 ] HOME ASSISTANT : SLIM HUIS & ENERGIE ══ "
    safe_addstr(stdscr, cur_y, 0, "═" * max(0, w - 1), curses.color_pair(C_MAGENTA) | curses.A_BOLD)
    safe_addstr(stdscr, cur_y, max(2, (w - len(banner2)) // 2), banner2, curses.color_pair(C_BADGE_MAGENTA) | curses.A_BOLD)
    cur_y += 1

    # ══════════════════════════════════════════════════════════════════════
    # SECTIE 5 — HOME ASSISTANT SMART HOME (10 rows)
    # ══════════════════════════════════════════════════════════════════════
    ha = data.get("ha", {})
    is_ha_online = ha.get("online", False)
    sec5_h = 10
    ha_badge = " ● ONLINE " if is_ha_online else " ○ OFFLINE "
    ha_badge_col = curses.color_pair(C_BADGE_ONLINE) if is_ha_online else curses.color_pair(C_BADGE_OFFLINE)

    draw_box(
        stdscr,
        cur_y,
        0,
        sec5_h,
        w,
        "SECTIE 5 — HOME ASSISTANT SMART HOME",
        f"http://{HA_HOST}:{HA_PORT} │ P1 DSMR-Meter & Sensoren",
    )

    # Row 1: Wie is er thuis? + Focus mode & Schermbeheer
    safe_box_str(stdscr, cur_y + 1, 3, max_x, "WIE IS THUIS? :", c_white)
    persons = ha.get("persons", [])
    p_x = 19
    if persons:
        for p in persons:
            p_name = p.get("name", "Persoon")
            p_home = p.get("is_home", False)
            safe_box_str(stdscr, cur_y + 1, p_x, max_x, f"{p_name}:", c_grey)
            p_x += len(p_name) + 1
            b_text = "●THUIS" if p_home else "○WEG"
            b_col = curses.color_pair(C_BADGE_ONLINE) if p_home else curses.color_pair(C_DARK_GREY)
            safe_box_str(stdscr, cur_y + 1, p_x, max_x, f" {b_text} ", b_col | curses.A_BOLD)
            p_x += len(b_text) + 3
    else:
        safe_box_str(stdscr, cur_y + 1, p_x, max_x, "Sensoren stand-by", c_grey)

    safe_box_str(stdscr, cur_y + 1, 55, max_x, "│ Hub Status: ", c_grey)
    safe_box_str(stdscr, cur_y + 1, 69, max_x, ha_badge, ha_badge_col | curses.A_BOLD)

    # Row 2: Totaal Stroomverbruik Nu
    p1_pow = ha.get("p1_power", 0.0)
    p1_tot = ha.get("p1_import_total", 0.0)
    f_bar_w = 16
    # 35A per fase @ 230V = 8050W per fase, totaal 24150W
    tot_pct = min(100.0, (p1_pow / 24150.0) * 100.0)
    safe_box_str(stdscr, cur_y + 2, 3, max_x, "STROOM TOTAAL :", c_white)
    draw_bar(stdscr, cur_y + 2, 19, f_bar_w, tot_pct, warn_pct=50.0, crit_pct=80.0)
    p1_col = curses.color_pair(C_GREEN) if p1_pow < 2000 else (curses.color_pair(C_ORANGE) if p1_pow < 5000 else curses.color_pair(C_RED))
    safe_box_str(stdscr, cur_y + 2, 20 + f_bar_w, max_x, f"{p1_pow:6.1f} W", p1_col | curses.A_BOLD)
    safe_box_str(stdscr, cur_y + 2, 28 + f_bar_w, max_x, f"│ Meterstand Import: {p1_tot:9.1f} kWh", c_white)

    # Rows 3..5: 3-Fasen Stroomgebruik (Max 35A / 8050W per fase)
    f_vals = [ha.get("p1_phase1", 0.0), ha.get("p1_phase2", 0.0), ha.get("p1_phase3", 0.0)]
    for idx, f_watt in enumerate(f_vals):
        f_row = cur_y + 3 + idx
        f_amp = f_watt / 230.0
        f_pct = min(100.0, (f_amp / 35.0) * 100.0)
        # Kleur: <60% (21A) = Groen, 60-80% (21-28A) = Oranje, >80% (28-35A) = Rood
        f_col = curses.color_pair(C_GREEN) if f_pct < 60.0 else (curses.color_pair(C_ORANGE) if f_pct < 80.0 else curses.color_pair(C_RED))
        b_char = "└─" if idx == 2 else "├─"
        safe_box_str(stdscr, f_row, 3, max_x, f"  {b_char} Fase {idx + 1} (35A) :", c_grey)
        draw_bar(stdscr, f_row, 19, f_bar_w, f_pct, warn_pct=60.0, crit_pct=80.0)
        safe_box_str(stdscr, f_row, 20 + f_bar_w, max_x, f"{f_amp:4.1f} A", f_col | curses.A_BOLD)
        safe_box_str(stdscr, f_row, 26 + f_bar_w, max_x, f"│ {f_watt:5.0f} W │ Belasting:", c_grey)
        safe_box_str(stdscr, f_row, 49 + f_bar_w, max_x, f"{f_pct:4.1f}%", f_col | curses.A_BOLD)

    draw_divider(stdscr, cur_y + 6, 0, w)

    # Row 7: Klimaat & Comfort
    t_wk = ha.get("temp_woonkamer")
    h_wk = ha.get("hum_woonkamer")
    t_str = f"{t_wk:.1f}°C" if t_wk is not None else "N/A"
    h_str = f"{h_wk:.0f}%" if h_wk is not None else "N/A"
    safe_box_str(stdscr, cur_y + 7, 3, max_x, "KLIMAAT BINNEN:", c_white)
    safe_box_str(stdscr, cur_y + 7, 19, max_x, f"Woonkamer: {t_str} │ Vochtigheid: {h_str} RV", c_cyan)

    # Row 8: Huishoudelijke Apparatuur & Contactsensoren
    wm_st = ha.get("wasmachine_state", "Uit")
    dr_st = ha.get("droger_state", "Uit")
    b_pow = ha.get("boiler_power", 0.0)
    door_is_open = ha.get("door_open", False)
    door_secs = ha.get("door_open_seconds", 0)
    wm_col = curses.color_pair(C_BADGE_ONLINE) if wm_st == "Draait" else curses.color_pair(C_GREY)
    dr_col = curses.color_pair(C_BADGE_ONLINE) if dr_st == "Draait" else curses.color_pair(C_GREY)
    safe_box_str(stdscr, cur_y + 8, 3, max_x, "STATUS HUIS   :", c_white)
    safe_box_str(stdscr, cur_y + 8, 19, max_x, "Wasmachine: ", c_grey)
    safe_box_str(stdscr, cur_y + 8, 31, max_x, f"[{wm_st}]", wm_col | curses.A_BOLD)
    safe_box_str(stdscr, cur_y + 8, 41, max_x, "│ Droger: ", c_grey)
    safe_box_str(stdscr, cur_y + 8, 51, max_x, f"[{dr_st}]", dr_col | curses.A_BOLD)
    if door_is_open:
        m, s = divmod(door_secs, 60)
        d_badge = f"[OPEN {m:02d}:{s:02d}]"
        d_col = curses.color_pair(C_ALERT_EXTREME) if door_secs >= 600 else curses.color_pair(C_BADGE_REBOOT)
    else:
        d_badge = "[DICHT]"
        d_col = curses.color_pair(C_BADGE_ONLINE)
    safe_box_str(stdscr, cur_y + 8, 61, max_x, "│ Voordeur: ", c_grey)
    safe_box_str(stdscr, cur_y + 8, 73, max_x, d_badge, d_col | curses.A_BOLD)

    cur_y += sec5_h

    # ══════════════════════════════════════════════════════════════════════
    # BANNER 3 — SERVER 3: BETASERVER (Raspberry Pi 5 Media Node)
    # ══════════════════════════════════════════════════════════════════════
    banner3 = " ══ [ SERVER 3 ] BETASERVER : RASPBERRY PI 5 MEDIA NODE ══ "
    safe_addstr(stdscr, cur_y, 0, "═" * max(0, w - 1), c_cyan)
    safe_addstr(stdscr, cur_y, max(2, (w - len(banner3)) // 2), banner3, curses.color_pair(C_BADGE_CYAN) | curses.A_BOLD)
    cur_y += 1

    # ══════════════════════════════════════════════════════════════════════
    # SECTIE 6 — BETASERVER MEDIA NODE (Dynamische hoogte)
    # ══════════════════════════════════════════════════════════════════════
    pi = data.get("pi5", {})
    is_pi_online = pi.get("online", False)
    status_badge = " ● ONLINE " if is_pi_online else " ○ OFFLINE "
    badge_color = curses.color_pair(C_BADGE_ONLINE) if is_pi_online else curses.color_pair(C_BADGE_OFFLINE)

    plex_streams = pi.get("plex_streams", [])
    if not isinstance(plex_streams, list):
        plex_streams = []
    jelly_streams = pi.get("jelly_streams", [])
    if not isinstance(jelly_streams, list):
        jelly_streams = []
    pi_disks = pi.get("disks", [])
    if not isinstance(pi_disks, list):
        pi_disks = []
    num_stream_rows = max(1, min(2, len(plex_streams) + len(jelly_streams)))
    num_disk_rows = min(4, len(pi_disks))
    # 1 (header) + 2 (node/load) + 1 (divider) + 1 (stream badge) + num_stream_rows + 1 (divider) + 1 (disk title) + num_disk_rows + 1 (bottom border) = 8 + rows
    sec6_h = 8 + num_stream_rows + num_disk_rows

    draw_box(
        stdscr,
        cur_y,
        0,
        sec6_h,
        w,
        "SECTIE 6 — BETASERVER MEDIA NODE",
        f"{pi.get('ip', '192.168.2.25')} │ Model: {pi.get('model', 'Pi 5')}",
    )

    # Row 1: Node info, status, uptime, SoC temp
    safe_box_str(stdscr, cur_y + 1, 3, max_x, f"Node: {pi.get('hostname', 'betaserver')}", c_white)
    safe_box_str(stdscr, cur_y + 1, 21, max_x, status_badge, badge_color | curses.A_BOLD)
    up_pi = format_uptime(pi.get("uptime", 0)) if is_pi_online else "Onbereikbaar"
    t_pi = f"{pi.get('temp', 0):.1f}°C" if pi.get("temp") else "N/A"
    t_col_pi = curses.color_pair(C_GREEN) if pi.get("temp", 0) < 65 else curses.color_pair(C_ORANGE)
    safe_box_str(stdscr, cur_y + 1, 34, max_x, f"│ Uptime: {up_pi:<11} │ SoC Temp: ", c_white)
    safe_box_str(stdscr, cur_y + 1, 68, max_x, t_pi, t_col_pi | curses.A_BOLD)

    # Row 2: CPU Load & RAM Bar
    p_mem = pi.get("mem", {})
    p_mem_pct = p_mem.get("pct", 0.0)
    p_load = pi.get("load", [0.0, 0.0, 0.0])
    safe_box_str(stdscr, cur_y + 2, 3, max_x, f"Load: {p_load[0]:.2f}, {p_load[1]:.2f} │ RAM:", c_white)
    p_bar_w = 16
    draw_bar(stdscr, cur_y + 2, 28, p_bar_w, p_mem_pct)
    safe_box_str(stdscr, cur_y + 2, 29 + p_bar_w, max_x, f"{p_mem_pct:5.1f}%", get_usage_pair(p_mem_pct) | curses.A_BOLD)
    safe_box_str(
        stdscr,
        cur_y + 2,
        36 + p_bar_w,
        max_x,
        f"│ Gebr: {format_bytes(p_mem.get('used', 0), 1)} / {format_bytes(p_mem.get('total', 1), 1)}",
        c_white,
    )

    draw_divider(stdscr, cur_y + 3, 0, w)

    # Rows 4..6: Media Streams Overzicht (Plex & Jellyfin)
    plex_cnt = pi.get("plex_count", 0)
    jelly_cnt = pi.get("jelly_count", 0)
    plex_streams = pi.get("plex_streams", [])
    jelly_streams = pi.get("jelly_streams", [])

    safe_box_str(stdscr, cur_y + 4, 3, max_x, "MEDIA STREAMS :", c_white)
    p_badge_str = f" PLEX: {plex_cnt} Kijker{'s' if plex_cnt != 1 else ''} "
    p_badge_col = curses.color_pair(C_BADGE_ONLINE) if plex_cnt > 0 else curses.color_pair(C_DARK_GREY)
    safe_box_str(stdscr, cur_y + 4, 19, max_x, p_badge_str, p_badge_col | curses.A_BOLD)

    j_badge_str = f" JELLYFIN: {jelly_cnt} Kijker{'s' if jelly_cnt != 1 else ''} "
    j_badge_col = curses.color_pair(C_BADGE_ONLINE) if jelly_cnt > 0 else curses.color_pair(C_DARK_GREY)
    safe_box_str(stdscr, cur_y + 4, 38, max_x, j_badge_str, j_badge_col | curses.A_BOLD)
    safe_box_str(stdscr, cur_y + 4, 58, max_x, f"│ Jellyfin: {pi.get('jelly_status', 'Online')}", c_grey)

    # Stream regels (max 2 streams)
    s_rendered = 0
    if plex_streams or jelly_streams:
        for ps in plex_streams[:2]:
            pct_str = f" ({ps.get('pct', 0.0):.0f}%)" if ps.get('pct') is not None else ""
            s_line = f"  ▶ PLEX │ {ps.get('user', 'User')} @ {ps.get('device', 'Device')}: {ps.get('title', 'Titel')} [{ps.get('state', 'Direct Play')}{pct_str}]"
            safe_box_str(stdscr, cur_y + 5 + s_rendered, 3, max_x, s_line, curses.color_pair(C_GREEN) | curses.A_BOLD)
            s_rendered += 1
        if s_rendered < 2 and jelly_streams:
            js = jelly_streams[0]
            s_line = f"  ▶ JELLYFIN │ {js.get('user', 'User')} @ {js.get('device', 'Device')}: {js.get('title', 'Titel')}"
            safe_box_str(stdscr, cur_y + 5 + s_rendered, 3, max_x, s_line, curses.color_pair(C_CYAN) | curses.A_BOLD)
            s_rendered += 1
    if s_rendered == 0:
        safe_box_str(stdscr, cur_y + 5, 3, max_x, "  ■ Geen actieve streams op dit moment — Stand-by", c_grey)
        s_rendered = 1

    draw_divider(stdscr, cur_y + 5 + s_rendered, 0, w)

    # Schijvenlijst (4 Disks)
    pi_disks = pi.get("disks", [])
    d_base = cur_y + 6 + s_rendered
    safe_box_str(stdscr, d_base, 3, max_x, f"OPSLAG & SCHIJVEN ({len(pi_disks)} Disks):", c_cyan)
    d_bar_w = 16
    for d_idx, pd in enumerate(pi_disks[:4]):
        r_row = d_base + 1 + d_idx
        if r_row >= cur_y + sec6_h - 1:
            break
        safe_box_str(stdscr, r_row, 3, max_x, f"{pd['name'][:18]:<18}:", c_white)
        draw_bar(stdscr, r_row, 22, d_bar_w, pd["pct"])
        pd_col = get_usage_pair(pd["pct"]) | curses.A_BOLD
        safe_box_str(stdscr, r_row, 23 + d_bar_w, max_x, f"{pd['pct']:5.1f}%", pd_col)
        safe_box_str(
            stdscr,
            r_row,
            30 + d_bar_w,
            max_x,
            f"│ Gebr: {format_bytes(pd['used'], 1):>8} / {format_bytes(pd['size'], 1):<8}",
            c_white,
        )

    cur_y += sec6_h

    # ══════════════════════════════════════════════════════════════════════
    # BANNER 4 — WEER & REGENRADAR ('S-HERTOGENBOSCH)
    # ══════════════════════════════════════════════════════════════════════
    weer = data.get("weather", {})
    is_ext = weer.get("is_extreme", False)
    k_code = weer.get("knmi_code", "GROEN")
    # Flashing effect bij extreem weer via time.monotonic()
    flash_on = (int(time.monotonic() * 2) % 2 == 0) if is_ext else False

    banner4 = " ══ WEER, REGENRADAR & KNMI WAARSCHUWING : 'S-HERTOGENBOSCH ══ "
    b4_attr = (curses.color_pair(C_ALERT_EXTREME) | curses.A_BOLD) if is_ext else (curses.color_pair(C_BADGE_MAGENTA) | curses.A_BOLD)
    safe_addstr(stdscr, cur_y, 0, "═" * max(0, w - 1), curses.color_pair(C_MAGENTA) | curses.A_BOLD)
    safe_addstr(stdscr, cur_y, max(2, (w - len(banner4)) // 2), banner4, b4_attr)
    cur_y += 1

    # ══════════════════════════════════════════════════════════════════════
    # SECTIE 7 — WEER & RADAR DASHBOARD (Tot aan de footer balk)
    # ══════════════════════════════════════════════════════════════════════
    sec7_h = max(8, h - cur_y - 1)
    draw_box(
        stdscr,
        cur_y,
        0,
        sec7_h,
        w,
        "SECTIE 7 — WEERSVERWACHTING & LIVE REGENRADAR",
        "Buienradar (2-uur radar) │ KNMI Brabant │ Open-Meteo",
    )

    # Row 1: KNMI Waarschuwing Banner
    if cur_y + 1 < cur_y + sec7_h - 1:
        knmi_t = weer.get("knmi_title", "Geen waarschuwingen (KNMI)")
        safe_box_str(stdscr, cur_y + 1, 3, max_x, "KNMI STATUS   :", c_white)
        if k_code == "ROOD":
            k_attr = (curses.color_pair(C_ALERT_EXTREME) | curses.A_BOLD) if flash_on else (curses.color_pair(C_RED) | curses.A_BOLD)
        elif k_code == "ORANJE":
            k_attr = (curses.color_pair(C_ORANGE) | curses.A_BOLD)
        elif k_code == "GEEL":
            k_attr = (curses.color_pair(C_ORANGE) | curses.A_BOLD)
        else:
            k_attr = curses.color_pair(C_BADGE_ONLINE) | curses.A_BOLD
        safe_box_str(stdscr, cur_y + 1, 19, max_x, f" [ {knmi_t} ] ", k_attr)

    # Row 2: Live Regen Alert & Samenvatting
    max_r = weer.get("max_rain_2h_mm", 0.0)
    curr_r = weer.get("current_rain_mm", 0.0)
    rain_sum = weer.get("rain_summary", "Droog komende 2 uur")
    if cur_y + 2 < cur_y + sec7_h - 1:
        safe_box_str(stdscr, cur_y + 2, 3, max_x, "REGEN ALERT   :", c_white)
        if max_r > 3.0 or curr_r > 2.0:
            r_attr = curses.color_pair(C_ALERT_EXTREME) | curses.A_BOLD
        elif max_r > 0.8 or curr_r > 0.2:
            r_attr = curses.color_pair(C_ALERT_RAIN) | curses.A_BOLD
        elif max_r > 0.0:
            r_attr = curses.color_pair(C_CYAN) | curses.A_BOLD
        else:
            r_attr = curses.color_pair(C_GREEN) | curses.A_BOLD
        safe_box_str(stdscr, cur_y + 2, 19, max_x, f" {rain_sum} ", r_attr)

    # Row 3: Live Buienradar 2-Uur Sparkline / Grafiek
    if cur_y + 3 < cur_y + sec7_h - 1:
        safe_box_str(stdscr, cur_y + 3, 3, max_x, "2U REGENRADAR :", c_white)
        r_pts = weer.get("rain_forecast", [])
        radar_str = ""
        for pt in r_pts[:24]:
            mm = pt.get("mm", 0.0)
            if mm <= 0.0:
                radar_str += " "
            elif mm < 0.3:
                radar_str += "▂"
            elif mm < 0.8:
                radar_str += "▃"
            elif mm < 1.5:
                radar_str += "▄"
            elif mm < 3.0:
                radar_str += "▅"
            elif mm < 5.0:
                radar_str += "▆"
            elif mm < 8.0:
                radar_str += "▇"
            else:
                radar_str += "█"

        radar_attr = curses.color_pair(C_CYAN) | curses.A_BOLD if max_r > 0 else curses.color_pair(C_GREY)
        safe_box_str(stdscr, cur_y + 3, 19, max_x, f"[{radar_str:<24}]", radar_attr)
        if r_pts:
            t_start = r_pts[0].get("time", "")
            t_end = r_pts[-1].get("time", "") if len(r_pts) > 1 else ""
            safe_box_str(stdscr, cur_y + 3, 46, max_x, f"Nu ({t_start}) ──> +2u ({t_end}) │ Piek: {max_r:.1f} mm/u", c_white)

    if cur_y + 4 < cur_y + sec7_h - 1:
        draw_divider(stdscr, cur_y + 4, 0, w)

    # Row 5: Actueel Weer (Meetstation Volkel)
    if cur_y + 5 < cur_y + sec7_h - 1:
        v_info = weer.get("volkel", {})
        v_temp = f"{v_info.get('temp', 0.0):.1f}°C" if v_info else "N/A"
        v_feel = f"{v_info.get('feel', 0.0):.1f}°C" if v_info else "N/A"
        v_bft = v_info.get("wind_bft", 0)
        v_dir = v_info.get("wind_dir", "-")
        v_desc = v_info.get("desc", "Geen meetdata")
        safe_box_str(stdscr, cur_y + 5, 3, max_x, "ACTUEEL WEER  :", c_white)
        safe_box_str(
            stdscr,
            cur_y + 5,
            19,
            max_x,
            f"{v_desc} │ Temp: {v_temp} (Gevoel: {v_feel}) │ Wind: {v_dir} {v_bft} Bft",
            c_cyan,
        )

    # Rows 6..10: Weersverwachting Den Bosch (dynamisch krimpen tot aan onderkant box)
    max_weather_box_y = cur_y + sec7_h - 1
    if cur_y + 6 < max_weather_box_y:
        d_fc = weer.get("forecast_daily", [])
        nl_days = {0: "Maandag", 1: "Dinsdag", 2: "Woensdag", 3: "Donderdag", 4: "Vrijdag", 5: "Zaterdag", 6: "Zondag"}
        rendered_days = 0
        for d_i, df in enumerate(d_fc[:5]):
            d_row = cur_y + 7 + d_i
            if d_row >= max_weather_box_y:
                break
            if rendered_days == 0:
                safe_box_str(stdscr, cur_y + 6, 3, max_x, f"WEERSVERWACHTING ({len(d_fc[:5])} Dagen):", c_white)
            if d_i == 0:
                lbl = "Vandaag"
            elif d_i == 1:
                lbl = "Morgen"
            else:
                try:
                    dt_obj = datetime.strptime(df.get("date", ""), "%Y-%m-%d")
                    lbl = nl_days.get(dt_obj.weekday(), df.get("date", ""))
                except Exception:
                    lbl = df.get("date", "")
            t_max = df.get("max", 0.0)
            t_min = df.get("min", 0.0)
            r_pct = df.get("rain_pct", 0)
            r_mm = df.get("rain_mm", 0.0)
            safe_box_str(stdscr, d_row, 3, max_x, f"  ▶ {lbl:<12}:", c_grey)
            safe_box_str(stdscr, d_row, 19, max_x, f"Max: {t_max:4.1f}°C │ Min: {t_min:4.1f}°C", c_white)
            r_col = curses.color_pair(C_CYAN) if r_pct > 40 else c_grey
            safe_box_str(stdscr, d_row, 45, max_x, f"│ Neerslagkans: {r_pct:2d}% ({r_mm:3.1f} mm)", r_col | curses.A_BOLD)
            rendered_days += 1

    # ══════════════════════════════════════════════════════════════════════
    # POPUP OVERLAY — VOORDEUR ALARM (Als deur >= 10 minuten open staat)
    # ══════════════════════════════════════════════════════════════════════
    ha_info = data.get("ha", {})
    if ha_info.get("door_open_alert", False):
        d_secs = ha_info.get("door_open_seconds", 600)
        dm, ds = divmod(d_secs, 60)
        pop_w = min(74, w - 4)
        pop_h = 11
        pop_x = max(2, (w - pop_w) // 2)
        pop_y = max(4, (h - pop_h) // 2)

        # Knipper effect (2 Hz)
        is_flash = (int(time.time() * 2) % 2 == 0)
        pop_col = curses.color_pair(C_DOOR_POPUP_RED) if is_flash else curses.color_pair(C_DOOR_POPUP_AMBER)
        pop_attr = pop_col | curses.A_BOLD

        # Achtergrond en rand vullen
        for py in range(pop_y, pop_y + pop_h):
            safe_addstr(stdscr, py, pop_x, " " * pop_w, pop_attr)

        border_top = "╔" + ("═" * (pop_w - 2)) + "╗"
        border_bot = "╚" + ("═" * (pop_w - 2)) + "╝"
        safe_addstr(stdscr, pop_y, pop_x, border_top, pop_attr)
        for py in range(pop_y + 1, pop_y + pop_h - 1):
            safe_addstr(stdscr, py, pop_x, "║", pop_attr)
            safe_addstr(stdscr, py, pop_x + pop_w - 1, "║", pop_attr)
        safe_addstr(stdscr, pop_y + pop_h - 1, pop_x, border_bot, pop_attr)

        line1 = "⚠️   ALARM: VOORDEUR STAAT OPEN!   ⚠️"
        line2 = f"CONTACT SENSOR: TUYA DEUR (b5nfnb0yhzueptpt)"
        line3 = f"DUUR: AL MEER DAN {dm} MINUTEN EN {ds:02d} SECONDEN OPEN!"
        line4 = "CONTROLEER OF DE DEUR GOED IN HET SLOT VALT"

        safe_addstr(stdscr, pop_y + 2, pop_x + max(1, (pop_w - len(line1)) // 2), line1, pop_attr)
        safe_addstr(stdscr, pop_y + 4, pop_x + max(1, (pop_w - len(line2)) // 2), line2, pop_attr)
        safe_addstr(stdscr, pop_y + 6, pop_x + max(1, (pop_w - len(line3)) // 2), line3, pop_attr)
        safe_addstr(stdscr, pop_y + 8, pop_x + max(1, (pop_w - len(line4)) // 2), line4, pop_attr)

    # ══════════════════════════════════════════════════════════════════════
    # ONDERAAN — FOOTER BALK (1 row)
    # ══════════════════════════════════════════════════════════════════════
    footer_y = h - 1
    safe_addstr(stdscr, footer_y, 0, " " * max(0, w - 1), curses.color_pair(C_DARK_GREY))
    safe_addstr(stdscr, footer_y, 1, " MLHK.NL ", curses.color_pair(C_BADGE_MAGENTA) | curses.A_BOLD)
    rot_str = get_fbcon_rotation()
    f_info = f" {w}x{h} ({rot_str}) │ [B] btop  [O] Roteer "
    safe_box_str(stdscr, footer_y, 11, max_x, f_info, c_white)
    safe_addstr(stdscr, footer_y, w - 24, "● <60% ", curses.color_pair(C_GREEN) | curses.A_BOLD)
    safe_addstr(stdscr, footer_y, w - 17, "● 60-80% ", curses.color_pair(C_ORANGE) | curses.A_BOLD)
    safe_addstr(stdscr, footer_y, w - 8, "● >80%", curses.color_pair(C_RED) | curses.A_BOLD)

    stdscr.refresh()


def run_btop():
    """Temporarily hand over the TTY to btop (configured in MLHK house style), then return."""
    curses.endwin()
    try:
        subprocess.run(["/usr/bin/btop", "--tty_on"], check=False)
    except Exception:
        pass


def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(250)
    init_colors()

    collector = SystemCollector()
    data = collector.collect()
    last_update = time.monotonic()
    render_portrait_dashboard(stdscr, collector, data)

    last_flash_state = -1

    while True:
        now = time.monotonic()
        # Fast animation frame check for pulsing alarms (weather and VM offline/critical)
        current_flash_state = int(now * 2) % 2
        needs_render = False

        if now - last_update >= REFRESH_INTERVAL:
            data = collector.collect()
            last_update = now
            needs_render = True
        elif current_flash_state != last_flash_state:
            # Check of er een pulserend alarm actief is
            has_alarm = (
                data.get("ha", {}).get("door_open_alert", False)
                or data.get("weather", {}).get("is_extreme", False)
                or any(
                    (v.get("status", "").upper() in ("STOPPED", "OFFLINE") and not (v.get("lock") in ("reboot", "restart") or "reboot" in v.get("status", "").lower()))
                    or (v.get("cpu_pct", 0) >= 95.0 or (v.get("maxmem", 0) > 0 and (v.get("mem", 0) * 100.0 / v.get("maxmem", 1)) >= 95.0))
                    for v in data.get("vms", [])
                )
            )
            if has_alarm:
                needs_render = True

        if needs_render:
            last_flash_state = current_flash_state
            render_portrait_dashboard(stdscr, collector, data)

        ch = stdscr.getch()
        if ch in (ord("b"), ord("B")):
            run_btop()
            set_linux_console_palette()
            stdscr.clear()
            curses.curs_set(0)
            data = collector.collect()
            last_update = time.monotonic()
            render_portrait_dashboard(stdscr, collector, data)
        elif ch in (ord("o"), ord("O")):
            toggle_fbcon_rotation()
            curses.endwin()
            stdscr = curses.initscr()
            curses.curs_set(0)
            stdscr.nodelay(True)
            stdscr.timeout(250)
            data = collector.collect()
            last_update = time.monotonic()
            render_portrait_dashboard(stdscr, collector, data)
        elif ch in (ord("r"), ord("R"), curses.KEY_RESIZE):
            curses.update_lines_cols()
            data = collector.collect()
            last_update = time.monotonic()
            render_portrait_dashboard(stdscr, collector, data)


if __name__ == "__main__":
    import traceback
    os.environ.setdefault("ESCDELAY", "25")
    set_linux_console_palette()
    while True:
        try:
            curses.wrapper(main)
        except KeyboardInterrupt:
            time.sleep(0.5)
            set_linux_console_palette()
        except Exception as e:
            with open("/tmp/monitor_err.log", "a") as f:
                f.write(f"[{datetime.now()}] CRASH: {e}\n{traceback.format_exc()}\n")
            time.sleep(2.0)
            set_linux_console_palette()
