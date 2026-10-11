# 🖥️ MLHK Server Monitor & Portrait Info Screen

[![Platform](https://img.shields.io/badge/Platform-Ubuntu%20%7C%20Debian%20%7C%20Proxmox%20VE-blue.svg)](https://proxmox.com)
[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![Style](https://img.shields.io/badge/Design-MLHK%20Cyberpunk%20House%20Style-magenta.svg)](https://mlhk.nl)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Een hardwarematig geoptimaliseerd **verticaal portret server-dashboard (1080x1920)** voor een dedicated monitor aangesloten op **Proxmox VE, Ubuntu of Debian**. 

Ontworpen in de officiële **MLHK Cyberpunk** huisstijl (*Electric Cyan `#00E5FF`*, *Neon Magenta `#E040FB`*, *Obsidian Black* en *Pure White*). Rendert vloeiend, stabiel en **100% flikkervrij** direct op de fysieke Linux framebuffer console (`/dev/tty1`, 90 kolommen x 80 rijen) zonder een zware desktopomgeving (X11 of Wayland) nodig te hebben.

---

## 🌟 Belangrijkste Functies

### 1. 🖥️ SERVER 1: Proxmox VE / Host Telemetrie
- **CPU & Cores:** Realtime belasting en hardware-kloksnelheden per afzonderlijke core.
- **Temperatuur & Load:** Package-temperatuur via `hwmon` en 1m / 5m / 15m load averages.
- **Geheugen & Opslag:** Dynamische RAM en Swap verbruiksbalken met semantische kleurcodering (**Groen** `<60%`, **Oranje** `60-80%`, **Rood** `>80%`).
- **Disk I/O & Netwerk:** Realtime lees-/schrijfsnelheden per SSD/HDD en actuele doorvoersnelheden op netwerk-interfaces (`vmbr0` / `eth0`).
- **Virtuele Machines & Containers:** Dynamische statusweergave van draaiende QEMU VM's en LXC containers met vCPU- en RAM-verbruik.

### 2. ⚡ SERVER 2: Home Assistant & Energie Management
- **Wie is er thuis:** Duidelijke status-badges voor bewoners op basis van de Home Assistant aanwezigheidssensoren.
- **Totaal Stroomverbruik:** Actueel totaal wattage en totale geïmporteerde kWh-teller (P1 DSMR meter).
- **3-Fasen Monitoring (Max 35A / 8050W per fase):**
  - Berekent exacte stroomsterkte ($A = W / 230$) en wattage per afzonderlijke fase (Fase 1, 2 en 3).
  - Kleurgecodeerde laadbalken op basis van de 35A zekeringbelasting (**Groen**, **Oranje**, **Rood**).
- **Klimaat & Apparatuur:** Binnentemperatuur, luchtvochtigheid en status van grootverbruikers (wasmachine, droger, boiler).

### 3. 🎬 SERVER 3: Remote Media Node (Raspberry Pi 5)
- **Live Stream Weergave (Plex & Jellyfin):**
  - Aantal actieve kijkers met gebruikersnaam, afspeelapparaat (`Apple TV`, `Smart TV`, `KPN TV+`) en film/serie titel.
  - Detecteert Direct Play vs. Transcode status.
- **Multi-Disk Opslag:** Monitor tot 4 aangesloten opslagschijven (NVMe, SSD, externe HDD's) met actueel gigabyte- en terabyte-verbruik.

### 4. 🌦️ Weer, Live Regenradar & KNMI Waarschuwingen
- **KNMI Weeralarm:** Live koppeling met de officiële KNMI provincie-waarschuwingen (Code Groen, Geel, Oranje, Rood).
- **Live 2-Uurs Regenradar:** Neerslagintensiteit van Buienradar berekend via de officiële formule ($mm/u = 10^{(val-109)/32}$) in een compacte ASCII sparkline (`[  ▂▃▄▅... ]`).
- **Regen Alert Banner:** Waarschuwt direct in felle kleuren bij naderende buien met verwachte starttijd en piekintensiteit in mm/u.
- **Actueel Weer & Verwachting:** Temperatuur, gevoelstemperatuur, windrichting & Bft (Meetstation Volkel) + meerdaagse weersverwachting.
- **Dynamische Hoogte:** Krimpt automatisch in als er meer virtuele machines bijkomen, zodat het scherm nooit gaat scrollen!

---

## 📐 Schermindeling & Console Geometrie

Het dashboard is ontworpen voor een **1080x1920 portret display** (bijv. een 24" of 27" monitor op zijn kant):
- **Resolutie:** `1080x1920` (270° of 90° geroteerd)
- **Console Raster:** Exact **90 kolommen × 80 rijen**
- **Hardware Palet:** Herprogrammeert bij het opstarten het Linux TTY 16-kleuren hardwarepalet naar de officiële MLHK cyberpunk kleurtonen.

---

## 🚀 Snelle Installatie (Quick Start)

### 1. Repository klonen
```bash
git clone https://github.com/itsdjooie/ubuntu-info-screen-custom.git /opt/mlhk-monitor
cd /opt/mlhk-monitor
```

### 2. Automatische installer uitvoeren (als root)
```bash
chmod +x install.sh
sudo ./install.sh
```

De installer voert automatisch de volgende stappen uit:
1. Installeert vereiste tools (`python3`, `fbset`, `btop`, `curl`, `jq`).
2. Maakt een afgeschermde service-user `mlhk-monitor` aan.
3. Configureert **TTY1 auto-login** via systemd.
4. Voegt het script toe aan `.bash_profile` zodat het direct start bij het booten.

### 3. Scherm roteren naar portretmodus
Om de console 270° te roteren voor verticale schermen:
```bash
echo 3 > /sys/class/graphics/fbcon/rotate_all
```
*(Gebruik `1` voor 90° rotatie, of `0` voor standaard liggend).*

---

## ⚙️ Configuratie

Kopieer het voorbeeldbestand en vul je eigen gegevens in:
```bash
cp config.env.example .env
nano .env
```

| Variabele | Omschrijving | Standaardwaarde |
| :--- | :--- | :--- |
| `SERVER_NAME` | Naam van de hoofdserver | `SMURF-SERVER` |
| `ENABLE_HA` | Home Assistant integratie in-/uitschakelen | `true` |
| `HA_HOST` | IP-adres van je Home Assistant installatie | `192.168.2.56` |
| `HA_PORT` | Poort van Home Assistant | `8123` |
| `HA_TOKEN` | Long-Lived Access Token van Home Assistant | `YOUR_TOKEN_HERE` |
| `ENABLE_REMOTE_NODE`| Remote node poller (bijv. Raspberry Pi) | `true` |
| `REMOTE_NODE_HOST` | IP-adres van de remote node | `192.168.2.25` |
| `REMOTE_NODE_USER` | SSH-gebruiker van de remote node | `molhoek` |
| `WEATHER_LAT` | Breedtegraad voor weersverwachting | `51.6978` ('s-Hertogenbosch) |
| `WEATHER_LON` | Lengtegraad voor weersverwachting | `5.3037` ('s-Hertogenbosch) |

---

## ⌨️ Sneltoetsen (Interactief)

Wanneer het dashboard draait, kun je de console interactief bedienen:
- `[B]` : Schakelt direct over naar **`btop`** (in MLHK cyberpunk thema) voor geavanceerde process-inspectie. Druk op `q` om terug te keren naar de monitor.
- `[O]` : Roteert de fysieke schermoriëntatie live tussen 270° en 90° portret.
- `[R]` : Forceert een directe herberekening van de schermresolutie en vensterafmetingen.

---

## 🔒 Beveiliging & Privacy

- Dit openbare project bevat **geen hardcoded tokens, wachtwoorden of persoonlijke API-keys**.
- Alle externe communicatie (Home Assistant, Plex, KNMI, Buienradar) verloopt via beveiligde verzoeken of lokale API-endpoints.

---

## 📄 Licentie

Gedistribueerd onder de **MIT Licentie**. Zie `LICENSE` voor meer informatie.
Ontwikkeld met ❤️ voor [mlhk.nl](https://mlhk.nl).
