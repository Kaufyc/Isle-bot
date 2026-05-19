from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from services.rcon_pterodactyl import (
    PterodactylConfig,
    PterodactylRconService,
    TemplateConfig,
)
from settings import AppSettings, load_settings
from services.storage import DinoStorage
from ui.panel import DinoPanelView, Services, SteamVerifyModal, build_panel_embed, refresh_button_labels


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("isle-bot")


class IsleBot(commands.Bot):
    def __init__(self, settings: AppSettings) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.settings = settings

        self.storage = DinoStorage(settings.database_path)

        self.save_expiration_hours = settings.save_expiration_hours
        self.cooldown_seconds = settings.cooldown_seconds
        self.backup_interval_minutes = settings.backup_interval_minutes
        self.backup_dir = Path(settings.backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        self.admin_log_channel_id = settings.admin_log_channel_id
        self.ticket_channel_id = settings.ticket_channel_id
        self.ticket_staff_role_id = settings.ticket_staff_role_id

        ptero = PterodactylConfig(
            panel_url=settings.pterodactyl_panel_url,
            api_key=settings.pterodactyl_api_key,
            server_identifier=settings.pterodactyl_server_identifier,
        )
        templates = TemplateConfig(
            restore_template=settings.restore_command_template,
            admin_recreate_template=settings.admin_recreate_command_template,
            online_players_command=settings.online_players_command,
            player_dino_query_template=settings.player_dino_query_template,
            kill_character_template=settings.kill_character_command_template,
        )

        self.rcon_service = PterodactylRconService(
            ptero=ptero,
            servers=settings.rcon_servers,
            templates=templates,
        )

        self.services = Services(
            storage=self.storage,
            rcon_service=self.rcon_service,
            expiration_hours=self.save_expiration_hours,
            cooldown_seconds=self.cooldown_seconds,
            admin_logger=self._admin_log,
            ticket_creator=self._ticket_create,
        )

    async def setup_hook(self) -> None:
        await self.storage.init()

        # Register persistent buttons so panel keeps working after restart.
        self.add_view(DinoPanelView(self.services))

        if self.settings.discord_guild_id is not None:
            guild = discord.Object(id=self.settings.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Synced commands to guild %s", self.settings.discord_guild_id)
        else:
            await self.tree.sync()
            logger.info("Synced global commands")

        self.backup_task.change_interval(minutes=self.backup_interval_minutes)
        self.backup_task.start()
        self.cleanup_task.start()

    async def _admin_log(self, interaction: discord.Interaction, message: str) -> None:
        if self.admin_log_channel_id <= 0:
            return

        channel = self.get_channel(self.admin_log_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        embed = discord.Embed(title="Admin Log", description=message, color=discord.Color.orange())
        embed.add_field(name="User", value=f"{interaction.user} ({interaction.user.id})", inline=False)
        embed.add_field(name="Channel", value=f"{interaction.channel_id}", inline=False)
        embed.timestamp = datetime.now(UTC)
        await channel.send(embed=embed)

    async def _ticket_create(self, interaction: discord.Interaction, reason: str) -> None:
        if self.ticket_channel_id <= 0:
            return

        channel = self.get_channel(self.ticket_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        steam_id = await self.storage.get_steam_link(interaction.user.id)
        dinos = await self.storage.get_user_dinos(interaction.user.id)

        text = [
            f"Ticket Type: {reason}",
            f"User: {interaction.user} ({interaction.user.id})",
            f"SteamID: {steam_id or 'not-verified'}",
        ]
        for dino in dinos:
            text.append(
                (
                    f"Slot {dino['slot']} -> species={dino['species']} growth={dino['growth']} "
                    f"location={dino['location']} server={dino['server_id']} cluster={dino['cluster_id']}"
                )
            )

        mention = f"<@&{self.ticket_staff_role_id}>\n" if self.ticket_staff_role_id > 0 else ""
        await channel.send(mention + "\n".join(text))

    @tasks.loop(minutes=30)
    async def backup_task(self) -> None:
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        source = self.storage.database_path
        target = self.backup_dir / f"dinos-{timestamp}.db"
        if source.exists():
            shutil.copy2(source, target)
            logger.info("Backup created: %s", target)

    @tasks.loop(minutes=5)
    async def cleanup_task(self) -> None:
        removed = await self.storage.cleanup_expired()
        if removed > 0:
            logger.info("Expired saves removed: %s", removed)


bot = IsleBot(load_settings())


@bot.tree.command(name="panel", description="Erweitertes Dino-Panel erstellen")
@app_commands.checks.has_permissions(administrator=True)
async def panel_command(interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
    locale = "de"
    embed = await build_panel_embed(locale)
    view = DinoPanelView(bot.services)
    refresh_button_labels(view, locale)

    target_channel = channel
    if target_channel is None:
        current_channel = interaction.channel
        if not isinstance(current_channel, discord.TextChannel):
            await interaction.response.send_message(
                "Bitte waehle einen Textkanal fuer das Panel aus.",
                ephemeral=True,
            )
            return
        target_channel = current_channel

    try:
        await target_channel.send(embed=embed, view=view)
    except discord.Forbidden:
        await interaction.response.send_message(
            "Ich habe keine Berechtigung, in diesem Kanal zu schreiben.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        f"Panel wurde in {target_channel.mention} erstellt.",
        ephemeral=True,
    )


@bot.tree.command(name="verify_steam", description="SteamID-Verknuepfung per Popup oeffnen")
async def verify_steam(interaction: discord.Interaction) -> None:
    locale = await bot.storage.get_locale(interaction.user.id)
    await interaction.response.send_modal(SteamVerifyModal(bot.services, locale))


@bot.tree.command(name="server_status", description="Server-Status anzeigen")
@app_commands.checks.has_permissions(administrator=True)
async def server_status(interaction: discord.Interaction, server_id: str) -> None:
    data = await bot.rcon_service.get_server_resources(server_id)
    if not data:
        await interaction.response.send_message(
            "Pterodactyl ist fuer diesen Server nicht konfiguriert.",
            ephemeral=True,
        )
        return

    attributes = data.get("attributes", {})
    resources = attributes.get("resources", {})

    cpu = resources.get("cpu_absolute", "?")
    memory = resources.get("memory_bytes", 0)
    state = attributes.get("current_state", "unknown")

    memory_mb = round((int(memory) / 1024 / 1024), 2) if isinstance(memory, (int, float)) else "?"

    await interaction.response.send_message(
        f"Server: {server_id}\nStatus: {state}\nCPU: {cpu}%\nSpeicher: {memory_mb} MB",
        ephemeral=True,
    )


@server_status.autocomplete("server_id")
async def server_id_autocomplete(_: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=sid, value=sid)
        for sid in bot.rcon_service.get_server_ids()
        if current.lower() in sid.lower()
    ][:25]


@bot.tree.command(name="player_online", description="Pruefen, ob eine SteamID online ist")
@app_commands.checks.has_permissions(administrator=True)
async def player_online(interaction: discord.Interaction, steam_id: str, server_id: str) -> None:
    online = await bot.rcon_service.is_player_online(server_id, steam_id)
    await interaction.response.send_message(
        f"SteamID {steam_id} online auf {server_id}: {online}",
        ephemeral=True,
    )


@bot.tree.command(name="admin_recreate", description="Admin: Charakter aus Slot wiederherstellen")
@app_commands.checks.has_permissions(administrator=True)
async def admin_recreate(interaction: discord.Interaction, user: discord.User, slot: app_commands.Range[int, 1, 2]) -> None:
    dino = await bot.storage.get_dino_in_slot(user.id, slot)
    if not dino:
        await interaction.response.send_message("Kein Save in diesem Slot.", ephemeral=True)
        return

    response = await bot.rcon_service.recreate_character_admin(
        target_user_id=user.id,
        admin_user_id=interaction.user.id,
        slot=slot,
        dino=dino,
    )
    await bot._admin_log(
        interaction,
        f"admin_recreate admin={interaction.user.id} target={user.id} slot={slot} response={response}",
    )
    await interaction.response.send_message("Admin-Recreate-Befehl wurde gesendet.", ephemeral=True)


@bot.tree.command(name="create_restore_ticket", description="Restore-Ticket fuer deine Saves erstellen")
async def create_restore_ticket(interaction: discord.Interaction) -> None:
    await bot._ticket_create(interaction, "slash-ticket")
    await interaction.response.send_message("Ticket wurde erstellt.", ephemeral=True)


@panel_command.error
@server_status.error
@player_online.error
@admin_recreate.error
async def admin_only_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("Admin-Berechtigung erforderlich.", ephemeral=True)
        return
    raise error


def main() -> None:
    bot.run(bot.settings.discord_token)


if __name__ == "__main__":
    main()
