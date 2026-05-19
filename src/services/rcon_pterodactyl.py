from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

import requests
from valve.rcon import execute as rcon_execute


@dataclass(slots=True)
class PterodactylConfig:
    panel_url: str | None
    api_key: str | None
    server_identifier: str | None = None


@dataclass(slots=True)
class RconConfig:
    server_id: str
    cluster_id: str
    host: str
    port: int
    password: str
    timeout: int
    pterodactyl_server_identifier: str | None = None


@dataclass(slots=True)
class TemplateConfig:
    restore_template: str
    admin_recreate_template: str
    online_players_command: str
    player_dino_query_template: str
    kill_character_template: str


STEAM_ID_RE = re.compile(r"\b\d{17}\b")


class PterodactylRconService:
    def __init__(self, ptero: PterodactylConfig, server: RconConfig, templates: TemplateConfig) -> None:
        self.ptero = ptero
        self.templates = templates
        self.server = server

    def _headers(self) -> dict[str, str]:
        if not self.ptero.api_key:
            return {}
        return {
            "Authorization": f"Bearer {self.ptero.api_key}",
            "Accept": "Application/vnd.pterodactyl.v1+json",
            "Content-Type": "application/json",
        }

    def get_server_ids(self) -> list[str]:
        return [self.server.server_id]

    def get_default_server_id(self) -> str:
        return self.server.server_id

    def get_cluster_id(self, server_id: str) -> str:
        return self._get_server(server_id).cluster_id

    def _get_server(self, server_id: str) -> RconConfig:
        if server_id != self.server.server_id:
            raise ValueError(f"Unknown server_id: {server_id}")
        return self.server

    async def get_server_resources(self, server_id: str) -> dict[str, Any] | None:
        server = self._get_server(server_id)
        server_identifier = server.pterodactyl_server_identifier or self.ptero.server_identifier
        if not (self.ptero.panel_url and self.ptero.api_key and server_identifier):
            return None

        url = (
            f"{self.ptero.panel_url.rstrip('/')}/api/client/servers/"
            f"{server_identifier}/resources"
        )

        def _request() -> dict[str, Any]:
            response = requests.get(url, headers=self._headers(), timeout=10)
            response.raise_for_status()
            try:
                return response.json()
            except ValueError as exc:
                preview = response.text.strip().replace("\n", " ")[:200]
                raise RuntimeError(
                    "Pterodactyl API returned non-JSON response. "
                    f"status={response.status_code} preview={preview!r}"
                ) from exc

        return await asyncio.to_thread(_request)

    async def send_rcon_command(self, server_id: str, command: str) -> str:
        server = self._get_server(server_id)

        def _send() -> Any:
            # Compatibility: some python-valve versions do not accept a timeout kwarg.
            try:
                return rcon_execute(
                    (server.host, server.port),
                    server.password,
                    command,
                    timeout=server.timeout,
                )
            except TypeError as exc:
                if "unexpected keyword argument 'timeout'" not in str(exc):
                    raise
                return rcon_execute(
                    (server.host, server.port),
                    server.password,
                    command,
                )

        result = await asyncio.to_thread(_send)
        return str(result)

    async def list_online_players(self, server_id: str) -> str:
        return await self.send_rcon_command(server_id, self.templates.online_players_command)

    async def is_player_online(self, server_id: str, steam_id: str) -> bool:
        output = await self.list_online_players(server_id)
        return steam_id in output

    async def get_online_steam_ids(self, server_id: str) -> set[str]:
        output = await self.list_online_players(server_id)
        return set(STEAM_ID_RE.findall(output))

    async def query_player_dino_data(self, server_id: str, steam_id: str) -> dict[str, Any]:
        command = self.templates.player_dino_query_template.format(steam_id=steam_id)
        raw = await self.send_rcon_command(server_id, command)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}

    async def kill_player_character(self, server_id: str, steam_id: str) -> str:
        command = self.templates.kill_character_template.format(steam_id=steam_id)
        return await self.send_rcon_command(server_id, command)

    async def capture_and_kill_current_dino(self, server_id: str, steam_id: str) -> dict[str, Any]:
        if not await self.is_player_online(server_id, steam_id):
            raise ValueError(f"Player is offline on server {server_id}")

        live_data = await self.query_player_dino_data(server_id, steam_id)
        species = str(live_data.get("species") or live_data.get("dino") or "unknown")
        growth_raw = live_data.get("growth", live_data.get("age", 100))
        location = str(live_data.get("location") or "unknown")

        try:
            growth = float(growth_raw)
        except (TypeError, ValueError):
            growth = 100.0

        await self.kill_player_character(server_id, steam_id)
        return {
            "name": species,
            "species": species,
            "growth": growth,
            "location": location,
            "raw": live_data,
        }

    async def restore_dino(self, user_id: int, slot: int, dino: dict[str, Any]) -> str:
        server_id = str(dino["server_id"])
        live_data = await self.query_player_dino_data(server_id, str(dino["steam_id"]))
        command = self.templates.restore_template.format(
            user_id=user_id,
            slot=slot,
            steam_id=dino["steam_id"],
            name=dino["name"],
            species=dino["species"],
            growth=dino["growth"],
            location=dino["location"],
            server_id=server_id,
            cluster_id=dino["cluster_id"],
            live_data=json.dumps(live_data),
        )
        return await self.send_rcon_command(server_id, command)

    async def recreate_character_admin(
        self,
        *,
        target_user_id: int,
        admin_user_id: int,
        slot: int,
        dino: dict[str, Any],
    ) -> str:
        server_id = str(dino["server_id"])
        command = self.templates.admin_recreate_template.format(
            target_user_id=target_user_id,
            admin_user_id=admin_user_id,
            slot=slot,
            steam_id=dino["steam_id"],
            name=dino["name"],
            species=dino["species"],
            growth=dino["growth"],
            location=dino["location"],
            server_id=server_id,
            cluster_id=dino["cluster_id"],
        )
        return await self.send_rcon_command(server_id, command)
