# Temples Karta Tracker

Per-Karta temple-yatra status tracking + admin followup, deployed at
**https://temples.vaidicpujas.in**.

## Live URLs

- `temples.vaidicpujas.in/` — full pilgrimage map (Tamil Nadu / Karnataka / Andhra Pradesh / Telangana) with per-state Karta dropdown filter
- `temples.vaidicpujas.in/k/` — Karta directory (filter by state)
- `temples.vaidicpujas.in/k/<slug>` — per-Karta page: mark Done / Prasaad collected, upload photos to Google Drive, share progress on WhatsApp
- `temples.vaidicpujas.in/followup` — admin dashboard: per-state + per-Karta progress, bulk mark, WhatsApp send via Baileys

## Components

| File | Role |
|---|---|
| `app.py` | Flask app — reads/writes the Google Sheet, serves `/k/*`, `/followup*`, handles photo upload to Drive, WhatsApp send via Baileys |
| `index.html` | Static map page deployed at `/var/www/temples/index.html` (extends an existing static site with the Karta dropdown + filter logic) |
| `karta.service` | systemd unit running `app.py` on `127.0.0.1:8001` |

## Server layout (SSPT — `root@168.231.120.137`)

- `/var/www/temples-karta/app.py` — Flask app (this repo's `app.py`)
- `/var/www/temples/index.html` — static map (this repo's `index.html`)
- `/etc/systemd/system/karta.service` — systemd unit
- `/etc/caddy/Caddyfile` — reverse-proxies `/k*` and `/followup*` to `localhost:8001`
- `/var/www/saints/google_config.json` — OAuth `client_id`, `client_secret`, `refresh_token` (Sheets + Drive scopes)

## Google Sheet

Source of truth: `1PZ4c2rHmfa6dHKWIJ4SzkmN7qvL5xftv_6hfF0imRbo`

Tabs:
- `KA Route`, `TN Route`, `AP Route`, `TG Route` — day-stop-temple plans with columns `Status` (E), Karta/POC (F), `Prasaad`, `Photos`
- `Karta Directory` — name, phone, slug
- `Devi-20` — Amman temple soft copy
- `Shiva-70`, `Vishnu-70` — short-list tabs

## WhatsApp send (Baileys)

`/followup` exposes a "📲 Send via WhatsApp" button per Karta. Backend posts to a local Baileys API at `localhost:7000/chats/send?id=<sessionId>` (already running on the server). Configurable session ID lives in `app.py` (`BAILEYS_SESSION_ID`).

## Caddy block

```
temples.vaidicpujas.in {
    handle /k* { reverse_proxy localhost:8001 }
    handle /followup    { reverse_proxy localhost:8001 }
    handle /followup/*  { reverse_proxy localhost:8001 }
    handle {
        root * /var/www/temples
        try_files {path} /index.html
        file_server
    }
}
```

## Deploy

```sh
scp app.py root@168.231.120.137:/var/www/temples-karta/app.py
scp index.html root@168.231.120.137:/var/www/temples/index.html
ssh root@168.231.120.137 'systemctl restart karta'
```
