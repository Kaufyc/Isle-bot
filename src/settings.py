from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from services.rcon_pterodactyl import RconConfig


@dataclass(slots=True)
class AppSettings:
    discord_token: str
    discord_guild_id: int | None
    database_path: str
    save_expiration_hours: int
    cooldown_seconds: int
    backup_interval_minutes: int
    backup_dir: str
    admin_log_channel_id: int
    ticket_channel_id: int
    ticket_staff_role_id: int
    pterodactyl_panel_url: str | None
    pterodactyl_api_key: str | None
    pterodactyl_server_identifier: str | None
    restore_command_template: str
    admin_recreate_command_template: str
    online_players_command: str
    player_dino_query_template: str
    kill_character_command_template: str
    rcon_server: RconConfig


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if value is not None and value.strip() == "":
        return None
    return value


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    if value is None:
        return default
    return int(value)


def _parse_server_from_env() -> RconConfig:
    return RconConfig(
        server_id=_env("RCON_SERVER_ID", "default") or "default",
        cluster_id=_env("RCON_CLUSTER_ID", "default") or "default",
        host=_env("RCON_HOST", "127.0.0.1") or "127.0.0.1",
        port=int(_env("RCON_PORT", "7778") or "7778"),
        password=_env("RCON_PASSWORD", "") or "",
        timeout=int(_env("RCON_TIMEOUT", "8") or "8"),
        pterodactyl_server_identifier=_env("PTERODACTYL_SERVER_IDENTIFIER"),
    )


def load_settings(root: Path | None = None) -> AppSettings:
    project_root = root or Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env")

    token = _env("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN missing in .env")

    guild_id_raw = _env("DISCORD_GUILD_ID")

    return AppSettings(
        discord_token=token,
        discord_guild_id=int(guild_id_raw) if guild_id_raw else None,
        database_path=_env("DATABASE_PATH", "data/dinos.db") or "data/dinos.db",
        save_expiration_hours=_env_int("SAVE_EXPIRATION_HOURS", 168),
        cooldown_seconds=_env_int("ACTION_COOLDOWN_SECONDS", 30),
        backup_interval_minutes=_env_int("AUTO_BACKUP_MINUTES", 30),
        backup_dir=_env("BACKUP_DIR", "data/backups") or "data/backups",
        admin_log_channel_id=int(_env("ADMIN_LOG_CHANNEL_ID", "0") or "0"),
        ticket_channel_id=int(_env("TICKET_CHANNEL_ID", "0") or "0"),
        ticket_staff_role_id=int(_env("TICKET_STAFF_ROLE_ID", "0") or "0"),
        pterodactyl_panel_url=_env("PTERODACTYL_PANEL_URL"),
        pterodactyl_api_key=_env("PTERODACTYL_API_KEY"),
        pterodactyl_server_identifier=_env("PTERODACTYL_SERVER_IDENTIFIER"),
        restore_command_template=(
            _env(
                "RESTORE_COMMAND_TEMPLATE",
                "restore_dino {steam_id} \"{species}\" {growth} \"{location}\" {server_id} {cluster_id}",
            )
            or "restore_dino {steam_id} \"{species}\" {growth} \"{location}\" {server_id} {cluster_id}"
        ),
        admin_recreate_command_template=(
            _env(
                "ADMIN_RECREATE_COMMAND_TEMPLATE",
                "admin_recreate {target_user_id} {steam_id} \"{species}\" {growth} \"{location}\"",
            )
            or "admin_recreate {target_user_id} {steam_id} \"{species}\" {growth} \"{location}\""
        ),
        online_players_command=_env("ONLINE_PLAYERS_COMMAND", "listplayers") or "listplayers",
        player_dino_query_template=(
            _env("PLAYER_DINO_QUERY_TEMPLATE", "player_dino {steam_id}") or "player_dino {steam_id}"
        ),
        kill_character_command_template=(
            _env("KILL_CHARACTER_COMMAND_TEMPLATE", "kill_player_dino {steam_id}")
            or "kill_player_dino {steam_id}"
        ),
        rcon_server=_parse_server_from_env(),
    )
