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
        self.config.register_guild(
            vote_role_id=None,
            duration_seconds=172800,
            grants={},
            poll_roles={},
            poll_votes={},
            delete_expired_poll_roles=True,
        )
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

    async def _grant_temporary_role(
        self, guild: discord.Guild, member: discord.Member, role: discord.Role
    ) -> bool:
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
        grant_key = f"{member.id}:{role.id}"

        async with self.config.guild(guild).grants() as grants:
            grants[grant_key] = expires_at

        return True

    async def _grant_vote_role(self, guild: discord.Guild, member: discord.Member) -> bool:
        role = await self._get_vote_role(guild)
        if role is None:
            return False
        return await self._grant_temporary_role(guild, member, role)

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

    @commands.Cog.listener()
    async def on_raw_poll_vote_add(self, payload: Any):
        """Track Discord poll option votes for later finalization."""
        guild_id = getattr(payload, "guild_id", None)
        if guild_id is None:
            return

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return

        user_id = getattr(payload, "user_id", None)
        message_id = getattr(payload, "message_id", None)
        answer_id = getattr(payload, "answer_id", None)
        if user_id is None or message_id is None or answer_id is None:
            return

        async with self.config.guild(guild).poll_votes() as poll_votes:
            poll_votes.setdefault(str(message_id), {})
            poll_votes[str(message_id)][str(user_id)] = int(answer_id)

    @commands.Cog.listener()
    async def on_raw_poll_vote_remove(self, payload: Any):
        """Track poll vote removals so finalization reflects latest voter choice."""
        guild_id = getattr(payload, "guild_id", None)
        if guild_id is None:
            return

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return

        user_id = getattr(payload, "user_id", None)
        message_id = getattr(payload, "message_id", None)
        answer_id = getattr(payload, "answer_id", None)
        if user_id is None or message_id is None or answer_id is None:
            return

        message_key = str(message_id)
        user_key = str(user_id)
        async with self.config.guild(guild).poll_votes() as poll_votes:
            if message_key not in poll_votes:
                return
            current_answer = poll_votes[message_key].get(user_key)
            if current_answer == int(answer_id):
                del poll_votes[message_key][user_key]
            if not poll_votes[message_key]:
                del poll_votes[message_key]

    @tasks.loop(minutes=1)
    async def _expire_vote_roles(self):
        now = time.time()
        for guild in self.bot.guilds:
            grants = await self.config.guild(guild).grants()
            if not grants:
                continue

            expired_grant_keys = [grant_key for grant_key, expiry in grants.items() if expiry <= now]
            if not expired_grant_keys:
                continue

            vote_role_id = await self.config.guild(guild).vote_role_id()
            delete_expired_poll_roles = await self.config.guild(guild).delete_expired_poll_roles()
            expired_role_ids = set()
            for grant_key in expired_grant_keys:
                user_id = None
                role_id = None

                if ":" in grant_key:
                    raw_user_id, raw_role_id = grant_key.split(":", 1)
                    try:
                        user_id = int(raw_user_id)
                        role_id = int(raw_role_id)
                    except ValueError:
                        continue
                else:
                    try:
                        user_id = int(grant_key)
                    except ValueError:
                        continue
                    role_id = vote_role_id

                if role_id is None:
                    continue

                expired_role_ids.add(role_id)
                member = guild.get_member(user_id)
                role = guild.get_role(role_id)
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
                for grant_key in expired_grant_keys:
                    stored_grants.pop(grant_key, None)

            if not delete_expired_poll_roles:
                continue

            remaining_grants = await self.config.guild(guild).grants()
            for role_id in expired_role_ids:
                if role_id == vote_role_id:
                    continue

                has_active_grants = any(
                    key.endswith(f":{role_id}") for key in remaining_grants.keys()
                )
                if has_active_grants:
                    continue

                role = guild.get_role(role_id)
                if role is None:
                    continue

                if role.members:
                    continue

                try:
                    await role.delete(reason="Temporary poll role expired")
                except discord.Forbidden:
                    LOG.warning(
                        "Missing permissions to delete expired poll role %s in guild %s.",
                        role_id,
                        guild.id,
                    )
                    continue
                except discord.HTTPException as exc:
                    LOG.warning(
                        "Failed to delete expired poll role %s in guild %s: %s",
                        role_id,
                        guild.id,
                        exc,
                    )
                    continue

                async with self.config.guild(guild).poll_roles() as poll_roles:
                    message_keys_to_clear = []
                    for message_key, mappings in poll_roles.items():
                        answer_keys_to_delete = [
                            answer_key
                            for answer_key, mapped_role_id in mappings.items()
                            if mapped_role_id == role_id
                        ]
                        for answer_key in answer_keys_to_delete:
                            del mappings[answer_key]
                        if not mappings:
                            message_keys_to_clear.append(message_key)
                    for message_key in message_keys_to_clear:
                        del poll_roles[message_key]

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
        poll_roles = await self.config.guild(ctx.guild).poll_roles()
        poll_mapping_count = sum(len(answers) for answers in poll_roles.values())
        poll_votes = await self.config.guild(ctx.guild).poll_votes()
        tracked_vote_count = sum(len(voters) for voters in poll_votes.values())
        delete_expired_poll_roles = await self.config.guild(ctx.guild).delete_expired_poll_roles()

        role_text = role.mention if role is not None else "Not configured (or role no longer exists)"
        await ctx.send(
            f"**Vote role:** {role_text}\n"
            f"**Duration:** {days:g} day(s)\n"
            f"**Delete expired poll roles:** {'Yes' if delete_expired_poll_roles else 'No'}\n"
            f"**Poll option role mappings:** {poll_mapping_count}\n"
            f"**Tracked poll votes:** {tracked_vote_count}\n"
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

    @voterole_group.command(name="deleteexpiredpollroles")
    async def voterole_deleteexpiredpollroles(self, ctx: commands.Context, enabled: bool):
        """Enable/disable deleting expired poll roles from the server."""
        await self.config.guild(ctx.guild).delete_expired_poll_roles.set(enabled)
        await ctx.send(
            f"Delete expired poll roles is now {'enabled' if enabled else 'disabled'}."
        )

    @voterole_group.group(name="poll")
    async def voterole_poll_group(self, ctx: commands.Context):
        """Manage per-poll-option temporary role mappings."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @voterole_poll_group.command(name="set")
    async def voterole_poll_set(
        self,
        ctx: commands.Context,
        message_id: int,
        answer_id: int,
        role: discord.Role,
    ):
        """Map one poll option to a temporary role."""
        async with self.config.guild(ctx.guild).poll_roles() as poll_roles:
            poll_roles.setdefault(str(message_id), {})
            poll_roles[str(message_id)][str(answer_id)] = role.id

        await ctx.send(
            f"Mapped poll `{message_id}` option `{answer_id}` to role {role.mention}."
        )

    @voterole_poll_group.command(name="remove")
    async def voterole_poll_remove(self, ctx: commands.Context, message_id: int, answer_id: int):
        """Remove one poll option mapping."""
        message_key = str(message_id)
        answer_key = str(answer_id)
        async with self.config.guild(ctx.guild).poll_roles() as poll_roles:
            mappings = poll_roles.get(message_key)
            if not mappings or answer_key not in mappings:
                await ctx.send("That poll option mapping does not exist.")
                return

            del mappings[answer_key]
            if not mappings:
                del poll_roles[message_key]

        await ctx.send(f"Removed mapping for poll `{message_id}` option `{answer_id}`.")

    @voterole_poll_group.command(name="clear")
    async def voterole_poll_clear(self, ctx: commands.Context, message_id: int):
        """Remove all role mappings for one poll message."""
        message_key = str(message_id)
        async with self.config.guild(ctx.guild).poll_roles() as poll_roles:
            if message_key not in poll_roles:
                await ctx.send("No mappings found for that poll message.")
                return
            del poll_roles[message_key]

        await ctx.send(f"Cleared all option mappings for poll `{message_id}`.")

    @voterole_poll_group.command(name="list")
    async def voterole_poll_list(self, ctx: commands.Context, message_id: Optional[int] = None):
        """List poll option role mappings."""
        poll_roles = await self.config.guild(ctx.guild).poll_roles()
        if not poll_roles:
            await ctx.send("No poll option mappings configured.")
            return

        lines = []
        if message_id is not None:
            mappings = poll_roles.get(str(message_id))
            if not mappings:
                await ctx.send("No mappings found for that poll message.")
                return
            lines.append(f"Poll `{message_id}`:")
            for answer_key, role_id in sorted(mappings.items(), key=lambda x: int(x[0])):
                role = ctx.guild.get_role(role_id)
                role_text = role.mention if role is not None else f"`{role_id}` (not found)"
                lines.append(f"- Option `{answer_key}` -> {role_text}")
        else:
            for message_key, mappings in sorted(poll_roles.items(), key=lambda x: int(x[0])):
                lines.append(f"Poll `{message_key}`:")
                for answer_key, role_id in sorted(mappings.items(), key=lambda x: int(x[0])):
                    role = ctx.guild.get_role(role_id)
                    role_text = role.mention if role is not None else f"`{role_id}` (not found)"
                    lines.append(f"- Option `{answer_key}` -> {role_text}")

        await ctx.send("\n".join(lines))

    @voterole_poll_group.command(name="finalize")
    async def voterole_poll_finalize(self, ctx: commands.Context, message_id: int):
        """Assign mapped temporary roles to voters of a finished poll."""
        message_key = str(message_id)
        poll_roles = await self.config.guild(ctx.guild).poll_roles()
        poll_votes = await self.config.guild(ctx.guild).poll_votes()

        mappings = poll_roles.get(message_key)
        if not mappings:
            await ctx.send("No role mappings found for that poll message.")
            return

        votes = poll_votes.get(message_key)
        if not votes:
            await ctx.send("No tracked votes found for that poll message.")
            return

        applied = 0
        skipped = 0
        for user_key, answer_id in votes.items():
            role_id = mappings.get(str(answer_id))
            if role_id is None:
                skipped += 1
                continue

            member = ctx.guild.get_member(int(user_key))
            role = ctx.guild.get_role(role_id)
            if member is None or role is None:
                skipped += 1
                continue

            if await self._grant_temporary_role(ctx.guild, member, role):
                applied += 1
            else:
                skipped += 1

        async with self.config.guild(ctx.guild).poll_votes() as stored_votes:
            stored_votes.pop(message_key, None)

        await ctx.send(
            f"Finalized poll `{message_id}`. Applied roles: {applied}. Skipped: {skipped}."
        )


async def setup(bot):
    await bot.add_cog(VoteRole(bot))
