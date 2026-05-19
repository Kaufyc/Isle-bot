# The Isle Evrima Discord Save Bot (Python)

Discord bot with an in-Discord panel (buttons + modals) to save up to **2 dinos per user**, list/delete them, and restore via **RCON**.

It also includes optional **Pterodactyl API** status support and advanced server-side protections.

## Features

- Discord panel command: `/panel`
- In-Discord UI with buttons and modal input
- Bilingual panel: **English + German** (user can toggle language)
- Per-user storage limit: **max 2 dinos**
- RCON restore and admin recreate command support with configurable templates
- Dino restore flow checks online status and Steam verification
- SteamID64 verification command: `/verify_steam`
- Save captures live server dino data (species, growth, location, server, cluster)
- Save kills old character after snapshot so player can pick a new character
- Anti-duplication fingerprint protection
- Save expiration with automatic cleanup task
- Auto backup of SQLite database on interval
- Admin logging channel integration
- Ticket integration (`/create_restore_ticket` and panel button)
- Optional Pterodactyl server status command: `/server_status`
- SQLite persistence

## Project Structure

- `src/main.py` - Bot startup and slash commands
- `src/settings.py` - Centralized `.env` loading and runtime configuration parsing
- `src/ui/panel.py` - Discord panel UI, buttons, modal, localization, cooldown logic
- `src/services/storage.py` - SQLite storage, Steam links, anti-duplication, expiration cleanup
- `src/services/rcon_pterodactyl.py` - single-server RCON + Pterodactyl + online checks
- `.env.example` - Environment configuration template

## Setup (Windows PowerShell)

1. Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Create `.env` from `.env.example` and fill your values:

Required minimum:

- `DISCORD_TOKEN`
- RCON settings (`RCON_*`)

Optional:

- `DISCORD_GUILD_ID` (faster slash-command sync while testing)
- `PTERODACTYL_PANEL_URL`
- `PTERODACTYL_API_KEY`
- `PTERODACTYL_SERVER_IDENTIFIER`
- `ADMIN_LOG_CHANNEL_ID`
- `TICKET_CHANNEL_ID`
- `TICKET_STAFF_ROLE_ID`
- `AUTO_BACKUP_MINUTES`
- `SAVE_EXPIRATION_HOURS`
- `ACTION_COOLDOWN_SECONDS`

4. Start the bot:

```powershell
python src/main.py
```

## Docker

1. Copy `.env.example` to `.env` and set your values.

2. Build and run with Docker Compose:

```powershell
docker compose up -d --build
```

3. Check logs:

```powershell
docker compose logs -f
```

4. Stop:

```powershell
docker compose down
```

Notes:

- SQLite database and backups are persisted in `./data` via volume mount.
- Container reads environment variables from `.env`.
- If you change Python dependencies, rebuild with `docker compose up -d --build`.

## Usage

1. In Discord (admin), run `/panel` in a channel.
   - Optional: choose a specific channel in the `/panel` command to create the panel there.
2. Users interact with the message buttons:
   - Save Dino / Dino speichern
   - My Dinos / Meine Dinos
   - Restore Dino / Dino laden
   - Delete Dino / Dino loeschen
   - Open Ticket / Ticket erstellen
   - Language toggle (EN/DE)

Save flow:

- Player presses save.
- Bot reads current dino data from the server by SteamID.
- Bot stores the data in slot 1 or 2.
- Bot kills the currently active character.

Load flow:

- Player presses restore and chooses slot 1 or 2.
- Bot reapplies the exact saved data from that slot.

3. Users link Steam before saving/restoring:

```text
/verify_steam <SteamID64>
```

4. Admin commands:

```text
/server_status
/player_online <steam_id>
/admin_recreate <user> <slot>
```

## RCON Restore Command Template

Configure templates in `.env`.

Available placeholders:

- `RESTORE_COMMAND_TEMPLATE`: `{user_id}`, `{slot}`, `{steam_id}`, `{name}`, `{species}`, `{growth}`, `{location}`, `{server_id}`, `{cluster_id}`, `{live_data}`
- `ADMIN_RECREATE_COMMAND_TEMPLATE`: `{target_user_id}`, `{admin_user_id}`, `{slot}`, `{steam_id}`, `{name}`, `{species}`, `{growth}`, `{location}`, `{server_id}`, `{cluster_id}`
- `KILL_CHARACTER_COMMAND_TEMPLATE`: `{steam_id}`

Example:

```env
RESTORE_COMMAND_TEMPLATE=restore_dino {steam_id} "{species}" {growth} "{location}" {server_id} {cluster_id}
```

Replace this with the exact command syntax your Isle Evrima server expects.

## Notes

- Slots are fixed to **1** and **2**.
- If both slots are full, user must enter slot `1` or `2` to replace a dino.
- Single server mode uses `RCON_SERVER_ID`, `RCON_CLUSTER_ID`, and `RCON_*` values from `.env`.
