import logging
import time
from typing import Any, Optional

import discord
from discord.ext import tasks
from redbot.core import Config, commands


LOG = logging.getLogger("red.voterole")


class VoteRole(commands.Cog):
    """Give a temporary role to members when a vote event is received."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=932670441255, force_registration=True)
        self.config.register_guild(vote_role_id=None, duration_seconds=172800, grants={})
        self._expire_vote_roles.start()

    def cog_unload(self):
        self._expire_vote_roles.cancel()

    @staticmethod
    def _extract_vote_user_id(data: Any) -> Optional[int]:
        raw_user_id = None

        if isinstance(data, dict):
            raw_user_id = data.get("user") or data.get("user_id") or data.get("id")
        else:
            raw_user_id = (
                getattr(data, "user", None)
                or getattr(data, "user_id", None)
                or getattr(data, "id", None)
            )

        if raw_user_id is None:
            return None

        try:
            return int(raw_user_id)
        except (TypeError, ValueError):
            return None

    async def _get_vote_role(self, guild: discord.Guild) -> Optional[discord.Role]:
        role_id = await self.config.guild(guild).vote_role_id()
        if role_id is None:
            return None
        return guild.get_role(role_id)

    async def _grant_vote_role(self, guild: discord.Guild, member: discord.Member) -> bool:
        role = await self._get_vote_role(guild)
        if role is None:
            return False

        if role not in member.roles:
            try:
                await member.add_roles(role, reason="Vote reward role granted")
            except discord.Forbidden:
                LOG.warning(
                    "Missing permissions to grant vote role %s in guild %s.",
                    role.id,
                    guild.id,
                )
                return False
            except discord.HTTPException as exc:
                LOG.warning(
                    "Failed to grant vote role %s in guild %s: %s",
                    role.id,
                    guild.id,
                    exc,
                )
                return False

        duration_seconds = await self.config.guild(guild).duration_seconds()
        expires_at = time.time() + duration_seconds

        async with self.config.guild(guild).grants() as grants:
            grants[str(member.id)] = expires_at

        return True

    async def _process_vote(self, user_id: int, source_event: str) -> None:
        applied_count = 0
        for guild in self.bot.guilds:
            member = guild.get_member(user_id)
            if member is None:
                continue

            if await self._grant_vote_role(guild, member):
                applied_count += 1

        LOG.info("Processed %s for user %s in %s guild(s).", source_event, user_id, applied_count)

    @commands.Cog.listener()
    async def on_dbl_vote(self, data: Any):
        """Handle top.gg vote webhook event used by many vote integrations."""
        user_id = self._extract_vote_user_id(data)
        if user_id is None:
            LOG.warning("Received on_dbl_vote payload without a valid user id: %r", data)
            return
        await self._process_vote(user_id, "on_dbl_vote")

    @commands.Cog.listener()
    async def on_topgg_vote(self, data: Any):
        """Handle top.gg vote webhook event alias."""
        user_id = self._extract_vote_user_id(data)
        if user_id is None:
            LOG.warning("Received on_topgg_vote payload without a valid user id: %r", data)
            return
        await self._process_vote(user_id, "on_topgg_vote")

    @tasks.loop(minutes=1)
    async def _expire_vote_roles(self):
        now = time.time()
        for guild in self.bot.guilds:
            role = await self._get_vote_role(guild)
            grants = await self.config.guild(guild).grants()
            if not grants:
                continue

            expired_user_ids = [user_id for user_id, expiry in grants.items() if expiry <= now]
            if not expired_user_ids:
                continue

            for user_id in expired_user_ids:
                member = guild.get_member(int(user_id))
                if member is None or role is None or role not in member.roles:
                    continue

                try:
                    await member.remove_roles(role, reason="Vote reward role expired")
                except discord.Forbidden:
                    LOG.warning(
                        "Missing permissions to remove vote role %s in guild %s.",
                        role.id,
                        guild.id,
                    )
                except discord.HTTPException as exc:
                    LOG.warning(
                        "Failed to remove vote role %s in guild %s: %s",
                        role.id,
                        guild.id,
                        exc,
                    )

            async with self.config.guild(guild).grants() as stored_grants:
                for user_id in expired_user_ids:
                    stored_grants.pop(user_id, None)

    @_expire_vote_roles.before_loop
    async def _before_expire_vote_roles(self):
        await self.bot.wait_until_ready()

    @commands.group(name="voterole")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def voterole_group(self, ctx: commands.Context):
        """Configure vote role rewards."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @voterole_group.command(name="setrole")
    async def voterole_setrole(self, ctx: commands.Context, role: discord.Role):
        """Set the temporary role given after a vote."""
        await self.config.guild(ctx.guild).vote_role_id.set(role.id)
        await ctx.send(f"Vote role set to {role.mention}.")

    @voterole_group.command(name="createrole")
    async def voterole_createrole(self, ctx: commands.Context, *, name: str = "Voter"):
        """Create a vote role and set it as the reward role."""
        cleaned_name = name.strip()
        if not cleaned_name:
            await ctx.send("Role name cannot be empty.")
            return

        existing_role = discord.utils.get(ctx.guild.roles, name=cleaned_name)
        if existing_role is not None:
            await self.config.guild(ctx.guild).vote_role_id.set(existing_role.id)
            await ctx.send(
                f"Role `{cleaned_name}` already exists. Set it as vote role: {existing_role.mention}."
            )
            return

        try:
            role = await ctx.guild.create_role(
                name=cleaned_name,
                reason=f"Vote role created by {ctx.author} ({ctx.author.id})",
            )
        except discord.Forbidden:
            await ctx.send("I cannot create roles. Please give me `Manage Roles` permission.")
            return
        except discord.HTTPException as exc:
            await ctx.send(f"Failed to create role: {exc}")
            return

        await self.config.guild(ctx.guild).vote_role_id.set(role.id)
        await ctx.send(f"Created and set vote role: {role.mention}.")

    @voterole_group.command(name="clearrole")
    async def voterole_clearrole(self, ctx: commands.Context):
        """Clear the configured vote role."""
        await self.config.guild(ctx.guild).vote_role_id.set(None)
        await ctx.send("Cleared the configured vote role.")

    @voterole_group.command(name="duration")
    async def voterole_duration(self, ctx: commands.Context, days: float):
        """Set role duration in days (example: 1 or 2)."""
        if days <= 0 or days > 30:
            await ctx.send("Duration must be greater than 0 and at most 30 days.")
            return

        duration_seconds = int(days * 86400)
        await self.config.guild(ctx.guild).duration_seconds.set(duration_seconds)
        await ctx.send(f"Vote role duration set to {days:g} day(s).")

    @voterole_group.command(name="status")
    async def voterole_status(self, ctx: commands.Context):
        """Show current vote role settings."""
        role = await self._get_vote_role(ctx.guild)
        duration_seconds = await self.config.guild(ctx.guild).duration_seconds()
        days = duration_seconds / 86400
        grants = await self.config.guild(ctx.guild).grants()

        role_text = role.mention if role is not None else "Not configured (or role no longer exists)"
        await ctx.send(
            f"**Vote role:** {role_text}\n"
            f"**Duration:** {days:g} day(s)\n"
            f"**Active timed grants:** {len(grants)}"
        )

    @voterole_group.command(name="grant")
    async def voterole_grant(self, ctx: commands.Context, member: discord.Member):
        """Manually grant/refresh the vote role for testing."""
        role = await self._get_vote_role(ctx.guild)
        if role is None:
            await ctx.send("No vote role is configured. Use `[p]voterole setrole @Role` first.")
            return

        applied = await self._grant_vote_role(ctx.guild, member)
        if not applied:
            await ctx.send(
                "I could not grant the vote role. Check my role hierarchy and permissions."
            )
            return

        await ctx.send(f"Granted/refreshed {role.mention} for {member.mention}.")


async def setup(bot):
    await bot.add_cog(VoteRole(bot))
