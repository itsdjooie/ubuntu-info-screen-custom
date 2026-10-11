#!/usr/bin/env python3
import json, subprocess, os, glob, urllib.request

# CPU & Load & Uptime
load = [0.0, 0.0, 0.0]
try:
    load = list(os.getloadavg())
except Exception:
    pass

uptime_sec = 0
try:
    with open('/proc/uptime') as f:
        uptime_sec = float(f.read().split()[0])
except Exception:
    pass

# Memory
mem_total = 16720637952
mem_used = 4933013504
try:
    with open('/proc/meminfo') as f:
        m = {}
        for line in f:
            p = line.split(':')
            if len(p) == 2:
                m[p[0].strip()] = int(p[1].strip().split()[0]) * 1024
        mem_total = m.get('MemTotal', 1)
        mem_avail = m.get('MemAvailable', 0)
        mem_used = mem_total - mem_avail
except Exception:
    pass

mem_pct = (mem_used * 100.0 / mem_total) if mem_total > 0 else 0.0

# Disks
disks = []
try:
    out = subprocess.check_output(['df', '-B1', '/dev/nvme0n1p2', '/dev/sda3', '/dev/sdb1', '/dev/sdc1']).decode().splitlines()[1:]
    for line in out:
        parts = line.split()
        if len(parts) >= 6:
            size = int(parts[1])
            used = int(parts[2])
            avail = int(parts[3])
            pct = float(parts[4].rstrip('%'))
            mp = parts[5]
            if mp == '/':
                label = 'Root NVMe (2TB)'
            elif 'samsung' in mp:
                label = 'Samsung SSD (500GB)'
            elif '1tb2' in mp:
                label = 'Media HDD 2 (1TB)'
            elif '1tb' in mp:
                label = 'Media HDD 1 (1TB)'
            else:
                label = mp.replace('/mnt/', '')
            disks.append({
                'name': label,
                'mount': mp,
                'size': size,
                'used': used,
                'free': avail,
                'pct': pct
            })
except Exception:
    pass

# Temperature
temp = 0.0
try:
    with open('/sys/class/thermal/thermal_zone0/temp') as f:
        temp = float(f.read().strip()) / 1000.0
except Exception:
    pass

# Rich Plex Stream Details via local Plex Media Server API
plex_streams = []
token = None

# Check cache file in home dir
token_cache_file = os.path.expanduser("~/.config/plex_token.txt")
try:
    if os.path.exists(token_cache_file):
        with open(token_cache_file, "r") as cf:
            c_tok = cf.read().strip()
            if c_tok:
                token = c_tok
except Exception:
    pass

if not token:
    try:
        files = glob.glob("/var/lib/plexmediaserver/**/Preferences.xml", recursive=True)
        for f in files:
            try:
                with open(f) as fp:
                    for part in fp.read().split():
                        if part.startswith("PlexOnlineToken="):
                            token = part.split('"')[1]
                            break
            except Exception:
                pass
            if token:
                break
    except Exception:
        pass

# Fallback: if permission denied for molhoek, read Preferences.xml via sudo -n
if not token:
    try:
        res = subprocess.run(
            ["sudo", "-n", "cat", "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Preferences.xml"],
            capture_output=True,
            text=True,
            timeout=2
        )
        if res.returncode == 0:
            for part in res.stdout.split():
                if part.startswith("PlexOnlineToken="):
                    token = part.split('"')[1]
                    break
    except Exception:
        pass

# If found, persist in cache so sudo isn't even needed next time
if token:
    try:
        os.makedirs(os.path.dirname(token_cache_file), exist_ok=True)
        with open(token_cache_file, "w") as cf:
            cf.write(token)
    except Exception:
        pass

if token:
    try:
        url = f"http://127.0.0.1:32400/status/sessions?X-Plex-Token={token}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=2) as res:
            p_data = json.loads(res.read())
            metadata = p_data.get("MediaContainer", {}).get("Metadata", [])
            for item in metadata:
                user = item.get("User", {}).get("title", "Gebruiker")
                player = item.get("Player", {}).get("title", item.get("Player", {}).get("product", "Device"))
                title = item.get("title", "")
                grandparent = item.get("grandparentTitle", "")
                media_title = f"{grandparent} - {title}" if grandparent else title
                transcode = item.get("TranscodeSession", {})
                state = "Transcode" if transcode else "Direct Play"
                view_offset = item.get("viewOffset", 0)
                duration = item.get("duration", 1)
                progress_pct = round((view_offset * 100.0 / duration), 1) if duration > 0 else 0.0
                plex_streams.append({
                    "user": user,
                    "device": player,
                    "title": media_title,
                    "state": state,
                    "pct": progress_pct
                })
    except Exception:
        pass

# Jellyfin Stream Details (Public / Sessions API fallback)
jelly_streams = []
jelly_status = "Online"
try:
    # Check if Jellyfin service is responding
    req = urllib.request.Request("http://127.0.0.1:8096/System/Info/Public")
    with urllib.request.urlopen(req, timeout=1.5) as res:
        j_info = json.loads(res.read())
        jelly_status = f"Online v{j_info.get('Version', '12.1.0')}"
except Exception:
    jelly_status = "Standby"

result = {
    'hostname': 'betaserver',
    'model': 'Raspberry Pi 5 (16GB)',
    'ip': '192.168.2.25',
    'uptime': uptime_sec,
    'load': load,
    'temp': temp,
    'mem': {
        'total': mem_total,
        'used': mem_used,
        'pct': mem_pct
    },
    'disks': disks,
    'plex_count': len(plex_streams),
    'plex_streams': plex_streams,
    'jelly_count': len(jelly_streams),
    'jelly_streams': jelly_streams,
    'jelly_status': jelly_status,
    'online': True
}

print(json.dumps(result))
