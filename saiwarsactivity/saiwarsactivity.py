import logging

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, commands


LOG = logging.getLogger("red.saiwarsactivity")


class SaiWarsActivity(commands.Cog):
    """Post new SaiWars activity events to Discord."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=743921608154, force_registration=True)
        self.config.register_guild(url=None, channel_id=None, last_activity_id=0)
        self._poll_activity.start()

    def cog_unload(self):
        self._poll_activity.cancel()

    async def _poll_guild(self, guild):
        settings = await self.config.guild(guild).all()
        url = settings["url"]
        channel_id = settings["channel_id"]
        if not url or not channel_id:
            return

        channel = guild.get_channel(channel_id)
        if channel is None:
            LOG.error("Configured activity channel %s was not found in guild %s", channel_id, guild.id)
            return

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                response.raise_for_status()
                payload = await response.json(content_type=None)

        activities = payload.get("activities") if isinstance(payload, dict) else payload
        if not isinstance(activities, list):
            raise ValueError("Activity API response must be a list or contain an 'activities' list")

        last_activity_id = int(settings["last_activity_id"])
        new_activities = [
            activity
            for activity in activities
            if isinstance(activity, dict)
            and isinstance(activity.get("id"), int)
            and not isinstance(activity.get("id"), bool)
            and activity["id"] > last_activity_id
        ]

        for activity in sorted(new_activities, key=lambda item: item["id"]):
            text = activity.get("text")
            if not isinstance(text, str):
                raise ValueError(f"Activity {activity['id']} does not contain string text")
            await channel.send(text)
            await self.config.guild(guild).last_activity_id.set(activity["id"])

    @tasks.loop(seconds=20)
    async def _poll_activity(self):
        for guild in self.bot.guilds:
            try:
                await self._poll_guild(guild)
            except Exception:
                LOG.exception("Failed to poll SaiWars activity for guild %s", guild.id)

    @_poll_activity.before_loop
    async def _before_poll_activity(self):
        await self.bot.wait_until_ready()

    @commands.group(name="saiwarsactivity", invoke_without_command=True)
    @commands.guild_only()
    async def saiwarsactivity_group(self, ctx):
        """Configure SaiWars activity posts."""
        await ctx.send_help()

    @saiwarsactivity_group.command(name="channel")
    @commands.admin_or_permissions(administrator=True)
    async def saiwarsactivity_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel where activity messages are posted."""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"SaiWars activity channel set to {channel.mention}.")

    @saiwarsactivity_group.command(name="url")
    @commands.admin_or_permissions(administrator=True)
    async def saiwarsactivity_url(self, ctx, *, url: str):
        """Set the SaiWars activity API URL."""
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            await ctx.send("The activity URL must start with `http://` or `https://`.")
            return

        await self.config.guild(ctx.guild).url.set(url)
        await ctx.send("SaiWars activity API URL updated.")

    @saiwarsactivity_group.command(name="status")
    async def saiwarsactivity_status(self, ctx):
        """Show the current SaiWars activity configuration."""
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(settings["channel_id"]) if settings["channel_id"] else None
        channel_display = channel.mention if channel else "Not configured"
        url_display = settings["url"] or "Not configured"
        polling = "running" if self._poll_activity.is_running() else "stopped"
        await ctx.send(
            f"URL: {url_display}\n"
            f"Channel: {channel_display}\n"
            f"Polling: {polling}"
        )