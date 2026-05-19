from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import discord

from services.rcon_pterodactyl import PterodactylRconService
from services.storage import DinoStorage

AdminLogCallback = Callable[[discord.Interaction, str], Awaitable[None]]
TicketCallback = Callable[[discord.Interaction, str], Awaitable[None]]
STEAM_ID_RE = re.compile(r"^\d{17}$")


TEXT = {
    "en": {
        "panel_title": "The Isle Evrima Advanced Save Panel",
        "panel_desc": "Steam-verified dino saves with anti-duplication, cooldowns, expiration, and restore checks.",
        "save": "Save Dino",
        "my_dinos": "My Dinos",
        "restore": "Restore Dino",
        "delete": "Delete Dino",
        "ticket": "Open Ticket",
        "verify": "Verify Steam",
        "switch_lang": "Deutsch",
        "saved_ok": "Dino saved in slot {slot}. Old character was killed. Expires: {expires_at}",
        "replaced_ok": "Dino replaced in slot {slot}. Old character was killed. Expires: {expires_at}",
        "slot_required": "You already have 2 dinos. Enter slot 1 or 2 to replace.",
        "deleted": "Deleted slot {slot}.",
        "not_found": "No dino found in slot {slot}.",
        "list_empty": "You have no saved dinos yet.",
        "restore_done": "Restore command sent for slot {slot} on server {server_id}.",
        "restore_error": "Restore failed: {error}",
        "lang_changed": "Language switched to English.",
        "steam_missing": "Verify your SteamID first using the Verify Steam button.",
        "steam_id_label": "SteamID64",
        "steam_id_placeholder": "Enter your 17-digit SteamID64",
        "steam_invalid": "Invalid SteamID64 format.",
        "steam_already_linked": "This SteamID is already linked to another Discord account.",
        "steam_linked": "SteamID linked: {steam_id}",
        "online_required": "Player is currently offline on server {server_id}.",
        "cooldown": "Cooldown active. Try again in {seconds}s.",
        "ticket_created": "Restore ticket was created for admins.",
    },
    "de": {
        "panel_title": "The Isle Evrima Erweitertes Speicher-Panel",
        "panel_desc": "Steam-verifizierte Dino-Saves mit Anti-Duplikat, Cooldowns, Ablaufzeit und Restore-Pruefungen.",
        "save": "Store Dino",
        "my_dinos": "Meine Dinos",
        "restore": "Load Dino",
        "delete": "Dino loeschen",
        "ticket": "Ticket erstellen",
        "verify": "Link Account",
        "switch_lang": "English",
        "saved_ok": "Dino in Slot {slot} gespeichert. Alter Charakter wurde getoetet. Ablauf: {expires_at}",
        "replaced_ok": "Dino in Slot {slot} ersetzt. Alter Charakter wurde getoetet. Ablauf: {expires_at}",
        "slot_required": "Du hast bereits 2 Dinos. Gib Slot 1 oder 2 zum Ersetzen ein.",
        "deleted": "Slot {slot} wurde geloescht.",
        "not_found": "Kein Dino in Slot {slot} gefunden.",
        "list_empty": "Du hast noch keine Dinos gespeichert.",
        "restore_done": "Restore-Befehl fuer Slot {slot} auf Server {server_id} gesendet.",
        "restore_error": "Wiederherstellung fehlgeschlagen: {error}",
        "lang_changed": "Sprache auf Deutsch umgestellt.",
        "steam_missing": "Bitte zuerst ueber den Link-Account-Button Steam verknuepfen.",
        "steam_id_label": "SteamID64",
        "steam_id_placeholder": "Gib deine 17-stellige SteamID64 ein",
        "steam_invalid": "Ungueltiges SteamID64-Format.",
        "steam_already_linked": "Diese SteamID ist bereits mit einem anderen Discord-Konto verknuepft.",
        "steam_linked": "SteamID verknuepft: {steam_id}",
        "online_required": "Spieler ist aktuell auf Server {server_id} offline.",
        "cooldown": "Cooldown aktiv. Versuche es in {seconds}s erneut.",
        "ticket_created": "Restore-Ticket wurde fuer Admins erstellt.",
    },
}


@dataclass(slots=True)
class Services:
    storage: DinoStorage
    rcon_service: PterodactylRconService
    expiration_hours: int
    cooldown_seconds: int
    admin_logger: AdminLogCallback
    ticket_creator: TicketCallback
    _cooldowns: dict[str, dict[int, float]] = field(default_factory=dict)

    def check_cooldown(self, key: str, user_id: int) -> int:
        bucket = self._cooldowns.setdefault(key, {})
        now = time.monotonic()
        previous = bucket.get(user_id, 0.0)
        remaining = int(self.cooldown_seconds - (now - previous))
        if remaining > 0:
            return remaining
        bucket[user_id] = now
        return 0


def t(locale: str, key: str, **kwargs: object) -> str:
    language = "de" if locale == "de" else "en"
    return TEXT[language][key].format(**kwargs)


class SaveDinoModal(discord.ui.Modal):
    def __init__(self, services: Services, locale: str) -> None:
        super().__init__(title=t(locale, "save"))
        self.services = services
        self.locale = locale

        self.server_input = discord.ui.TextInput(label="Server-ID", placeholder=", ".join(self.services.rcon_service.get_server_ids()))
        self.slot_input = discord.ui.TextInput(label="Slot 1-2 (optional)", required=False)

        self.add_item(self.server_input)
        self.add_item(self.slot_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cooldown = self.services.check_cooldown("save", interaction.user.id)
        if cooldown > 0:
            await interaction.response.send_message(t(self.locale, "cooldown", seconds=cooldown), ephemeral=True)
            return

        try:
            steam_id = await self.services.storage.get_steam_link(interaction.user.id)
            if not steam_id:
                await interaction.response.send_message(t(self.locale, "steam_missing"), ephemeral=True)
                return

            slot_raw = str(self.slot_input.value).strip()
            requested_slot = int(slot_raw) if slot_raw else None
            server_id = str(self.server_input.value).strip()
            cluster_id = self.services.rcon_service.get_cluster_id(server_id)

            snapshot = await self.services.rcon_service.capture_and_kill_current_dino(server_id, steam_id)
            species = str(snapshot["species"])
            growth = float(snapshot["growth"])
            location = str(snapshot["location"])

            result = await self.services.storage.save_dino(
                interaction.user.id,
                steam_id=steam_id,
                name=species,
                species=species,
                growth=growth,
                location=location,
                server_id=server_id,
                cluster_id=cluster_id,
                expiration_hours=self.services.expiration_hours,
                requested_slot=requested_slot,
            )

            message_key = "replaced_ok" if result.replaced else "saved_ok"
            await self.services.admin_logger(
                interaction,
                (
                    f"save user={interaction.user.id} steam={steam_id} slot={result.slot} "
                    f"species={species} growth={growth} location={location} "
                    f"server={server_id} cluster={cluster_id}"
                ),
            )
            await interaction.response.send_message(
                t(self.locale, message_key, slot=result.slot, expires_at=result.expires_at),
                ephemeral=True,
            )
        except ValueError as exc:
            message = str(exc)
            if "Slot" in message or "free slot" in message:
                message = t(self.locale, "slot_required")
            await interaction.response.send_message(message, ephemeral=True)
        except Exception as exc:  # noqa: BLE001
            await interaction.response.send_message(
                t(self.locale, "restore_error", error=str(exc)),
                ephemeral=True,
            )


class SteamVerifyModal(discord.ui.Modal):
    def __init__(self, services: Services, locale: str) -> None:
        super().__init__(title=t(locale, "verify"))
        self.services = services
        self.locale = locale

        self.steam_id_input = discord.ui.TextInput(
            label=t(locale, "steam_id_label"),
            placeholder=t(locale, "steam_id_placeholder"),
            min_length=17,
            max_length=17,
        )
        self.add_item(self.steam_id_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        steam_id = str(self.steam_id_input.value).strip()
        if not STEAM_ID_RE.match(steam_id):
            await interaction.response.send_message(t(self.locale, "steam_invalid"), ephemeral=True)
            return

        existing = await self.services.storage.get_user_id_by_steam(steam_id)
        if existing and existing != str(interaction.user.id):
            await interaction.response.send_message(t(self.locale, "steam_already_linked"), ephemeral=True)
            return

        await self.services.storage.set_steam_link(interaction.user.id, steam_id)
        await self.services.admin_logger(interaction, f"steam_verify user={interaction.user.id} steam={steam_id}")
        await interaction.response.send_message(t(self.locale, "steam_linked", steam_id=steam_id), ephemeral=True)


class RestoreSlotView(discord.ui.View):
    def __init__(self, services: Services, locale: str) -> None:
        super().__init__(timeout=120)
        self.services = services
        self.locale = locale

    @discord.ui.button(label="Slot 1", style=discord.ButtonStyle.primary)
    async def restore_slot_1(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._restore(interaction, 1)

    @discord.ui.button(label="Slot 2", style=discord.ButtonStyle.primary)
    async def restore_slot_2(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._restore(interaction, 2)

    async def _restore(self, interaction: discord.Interaction, slot: int) -> None:
        cooldown = self.services.check_cooldown("restore", interaction.user.id)
        if cooldown > 0:
            await interaction.response.send_message(t(self.locale, "cooldown", seconds=cooldown), ephemeral=True)
            return

        dino = await self.services.storage.get_dino_in_slot(interaction.user.id, slot)
        if not dino:
            await interaction.response.send_message(t(self.locale, "not_found", slot=slot), ephemeral=True)
            return

        steam_id = await self.services.storage.get_steam_link(interaction.user.id)
        if not steam_id or steam_id != dino.get("steam_id"):
            await interaction.response.send_message(t(self.locale, "steam_missing"), ephemeral=True)
            return

        server_id = str(dino["server_id"])
        online = await self.services.rcon_service.is_player_online(server_id, steam_id)
        if not online:
            await interaction.response.send_message(
                t(self.locale, "online_required", server_id=server_id),
                ephemeral=True,
            )
            return

        try:
            await self.services.rcon_service.restore_dino(interaction.user.id, slot, dino)
            await self.services.admin_logger(
                interaction,
                f"restore user={interaction.user.id} steam={steam_id} slot={slot} server={server_id}",
            )
            await interaction.response.send_message(
                t(self.locale, "restore_done", slot=slot, server_id=server_id),
                ephemeral=True,
            )
        except Exception as exc:  # noqa: BLE001
            await interaction.response.send_message(
                t(self.locale, "restore_error", error=str(exc)),
                ephemeral=True,
            )


class DeleteSlotView(discord.ui.View):
    def __init__(self, services: Services, locale: str) -> None:
        super().__init__(timeout=120)
        self.services = services
        self.locale = locale

    @discord.ui.button(label="Slot 1", style=discord.ButtonStyle.danger)
    async def delete_slot_1(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._delete(interaction, 1)

    @discord.ui.button(label="Slot 2", style=discord.ButtonStyle.danger)
    async def delete_slot_2(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._delete(interaction, 2)

    async def _delete(self, interaction: discord.Interaction, slot: int) -> None:
        deleted = await self.services.storage.delete_slot(interaction.user.id, slot)
        if deleted:
            await self.services.admin_logger(interaction, f"delete user={interaction.user.id} slot={slot}")
            await interaction.response.send_message(t(self.locale, "deleted", slot=slot), ephemeral=True)
            return

        await interaction.response.send_message(t(self.locale, "not_found", slot=slot), ephemeral=True)


class DinoPanelView(discord.ui.View):
    def __init__(self, services: Services) -> None:
        super().__init__(timeout=None)
        self.services = services

    async def _locale(self, user_id: int) -> str:
        return "de"

    async def _build_list_embed(self, user_id: int, locale: str) -> discord.Embed:
        dinos = await self.services.storage.get_user_dinos(user_id)
        embed = discord.Embed(
            title=t(locale, "panel_title"),
            description=t(locale, "panel_desc"),
            color=discord.Color.green(),
        )
        if not dinos:
            embed.add_field(name="Info", value=t(locale, "list_empty"), inline=False)
            return embed

        for dino in dinos:
            embed.add_field(
                name=f"Slot {dino['slot']}: {dino['species']}",
                value=(
                    f"Growth: {dino['growth']}\n"
                    f"Location: {dino['location']}\n"
                    f"Server: {dino['server_id']} / Cluster: {dino['cluster_id']}\n"
                    f"Expires: {dino['expires_at']}"
                ),
                inline=False,
            )
        return embed

    @discord.ui.button(label="Store Dino", style=discord.ButtonStyle.success, custom_id="dino:save")
    async def save_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        locale = await self._locale(interaction.user.id)
        await interaction.response.send_modal(SaveDinoModal(self.services, locale))

    @discord.ui.button(label="Load Dino", style=discord.ButtonStyle.primary, custom_id="dino:restore")
    async def restore_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        locale = await self._locale(interaction.user.id)
        await interaction.response.send_message(
            t(locale, "restore") + ":",
            view=RestoreSlotView(self.services, locale),
            ephemeral=True,
        )

    @discord.ui.button(label="Link Account", style=discord.ButtonStyle.secondary, custom_id="dino:verify")
    async def verify_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        locale = await self._locale(interaction.user.id)
        await interaction.response.send_modal(SteamVerifyModal(self.services, locale))


async def build_panel_embed(locale: str = "de") -> discord.Embed:
    embed = discord.Embed(
        title=t(locale, "panel_title"),
        description=t(locale, "panel_desc"),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="1", value=t(locale, "verify"), inline=True)
    embed.add_field(name="2", value=t(locale, "save"), inline=True)
    embed.add_field(name="3", value=t(locale, "restore"), inline=True)
    return embed


def refresh_button_labels(view: DinoPanelView, locale: str) -> None:
    for item in view.children:
        if isinstance(item, discord.ui.Button):
            if item.custom_id == "dino:save":
                item.label = t(locale, "save")
            elif item.custom_id == "dino:restore":
                item.label = t(locale, "restore")
            elif item.custom_id == "dino:verify":
                item.label = t(locale, "verify")
