import asyncio
from contextlib import suppress
import hashlib
import logging
import time
from typing import Optional

import discord
from redbot.core import Config, commands
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu
from redbot.core.utils.views import SetApiView

from .api import APIError, MightyAPI, alliance_tag, identifier
from .views import (
    calendar, comparison_pages, embed, fields, image, kvk_embed, mapping, number, pages,
    records, roster, SCOUT_BOARDS, scout_embeds, scout_rows, text, timestamp,
)

log = logging.getLogger("red.Sai.kingshot")
PLAYER_STATS = [
    ("Power", "power", True), ("Town Center", "town_center_level", True),
    ("Kills", "kills", True), ("VIP", "vip", True), ("Kingdom", "kid", True),
]
GUILD_DEFAULTS = dict(
    kingdom=570, alliance="VIL", channel=None, interval=10,
    auto_enabled=False, auto_channel=None, message_id=None,
    auto_attempt=0, auto_success=0, auto_error=None,
    snapshots=[], snapshot_attempt=0, snapshot_error=None,
    remind_enabled=False, remind_channel=None, remind_offsets=[24, 1],
    reminder_attempt=0, reminder_error=None, reminder_sent={},
    manual_events=[],
    linked_role=None, alliance_role=None, link_nickname=True,
)
MEMBER_DEFAULTS = dict(governor_id=None, linked_at=0, assigned_roles=[],
                       original_nick=None, applied_nick=None, pending_nick=None)
SAFE_ROLE_PERMISSIONS = frozenset(
    "view_channel read_messages send_messages send_tts_messages embed_links attach_files "
    "read_message_history add_reactions external_emojis use_external_emojis external_stickers "
    "use_external_stickers connect speak stream use_voice_activation priority_speaker "
    "use_application_commands create_public_threads create_private_threads send_messages_in_threads "
    "use_embedded_activities use_soundboard use_external_sounds send_voice_messages "
    "send_polls use_external_apps change_nickname".split()
)


class Kingshot(commands.Cog):
    """Kingshot statistics and Mighty preparation score tracking."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=5702026103101, force_registration=True)
        self.config.register_guild(**GUILD_DEFAULTS)
        self.config.register_member(**MEMBER_DEFAULTS)
        self.api = MightyAPI(self._token)
        self._worker: Optional[asyncio.Task] = None
        self._locks = {}
        self._token_views = set()
        self._closing = False

    async def _token(self):
        return (await self.bot.get_shared_api_tokens("mightpulse")).get("api_key")

    def _lock(self, guild_id):
        return self._locks.setdefault(guild_id, asyncio.Lock())

    async def cog_load(self):
        self._closing = False
        await self.api.start()
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="kingshot-worker")

    async def cog_unload(self):
        self._closing = True
        if self._worker is not None:
            self._worker.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None
        await self.api.close()
        for view in self._token_views:
            view.stop()
        self._token_views.clear()
        self._locks.clear()

    async def red_delete_data_for_user(self, *, requester, user_id):
        for guild_id, members in (await self.config.all_members()).items():
            if user_id in members:
                async with self._lock(guild_id):
                    await self.config.member_from_ids(guild_id, user_id).clear()

    async def cog_command_error(self, ctx, error):
        original = getattr(error, "original", error)
        if isinstance(original, (APIError, ValueError)):
            await ctx.send(str(original), allowed_mentions=discord.AllowedMentions.none())
        elif isinstance(original, commands.UserInputError):
            await ctx.send("Invalid, missing or extra argument. See the command's help.")
        elif isinstance(original, discord.Forbidden):
            await ctx.send("I lack a required Discord permission. Check channel permissions, "
                           "Manage Nicknames/Manage Roles and the role hierarchy.")
        elif isinstance(original, discord.HTTPException):
            await ctx.send("Discord could not complete the request. Try again later.")
        elif isinstance(original, commands.CheckFailure):
            await ctx.send("This command is not available here or you lack permission.")
        else:
            # Never log exception text or traceback: upstream errors can contain secrets.
            log.error("Kingshot command failed (%s)", type(original).__name__)
            await ctx.send("An unexpected Kingshot error occurred; the owner should inspect the cog.")

    async def _send_pages(self, ctx, title, lines, data=None):
        output = pages(title, lines, data=data)
        if len(output) == 1:
            await ctx.send(embed=output[0], allowed_mentions=discord.AllowedMentions.none())
        else:
            await menu(ctx, output, DEFAULT_CONTROLS)

    async def _settings(self, ctx):
        return await self.config.guild(ctx.guild).all()

    async def _kingdom_id(self, ctx, kid: Optional[int] = None):
        if kid is None:
            kid = (await self._settings(ctx))["kingdom"]
        return identifier(kid)

    async def _change(self, ctx, **values):
        async with self._lock(ctx.guild.id):
            config = self.config.guild(ctx.guild)
            for key, value in values.items():
                await config.get_attr(key).set(value)
        await ctx.tick()

    @staticmethod
    def _interval(minutes):
        if not 5 <= minutes <= 1440:
            raise ValueError("Refresh interval must be between 5 and 1440 minutes.")
        return minutes

    def _channel(self, guild, channel_id):
        channel = guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            raise ValueError("Configure a text channel in this server first.")
        permissions = channel.permissions_for(guild.me)
        if not (permissions.view_channel and permissions.send_messages
                and permissions.embed_links and permissions.read_message_history):
            raise ValueError("The channel requires View Channel, Send Messages, Embed Links "
                             "and Read Message History permissions for this bot.")
        return channel

    async def _kvk_data(self, kid):
        matchup = await self.api.kvk(kid)
        if matchup.get("bye") is True:
            return matchup, None, None
        opponent = matchup.get("opponent")
        if not isinstance(opponent, dict) or opponent.get("kid") is None:
            return matchup, None, "Opponent not published."
        try:
            scores = await self.api.scores(kid)
        except APIError as error:
            if error.status != 404:
                raise
            return matchup, None, str(error)
        return matchup, scores, None

    @commands.group(name="ks", invoke_without_command=True)
    @commands.guild_only()
    @commands.bot_has_permissions(embed_links=True)
    async def ks(self, ctx):
        """Kingshot statistics. See help ks for all commands."""
        await ctx.send_help()

    @staticmethod
    def _role_error(guild, role):
        if role is None:
            return "The configured role was deleted."
        if role.is_default() or role.managed:
            return "Default and managed roles cannot be used for unverified linking."
        unsafe = [name for name, enabled in role.permissions
                  if enabled and name not in SAFE_ROLE_PERMISSIONS]
        if unsafe:
            return "Unverified links cannot grant privileged roles or permissions."
        if not guild.me.guild_permissions.manage_roles or role >= guild.me.top_role:
            return "The bot needs Manage Roles and a role above the configured role."
        return None

    def _validate_link_role(self, ctx, role):
        error = self._role_error(ctx.guild, role)
        if error:
            raise ValueError(error)
        if ctx.author.id != ctx.guild.owner_id and role >= ctx.author.top_role:
            raise ValueError("Select a role below your highest role.")

    async def _apply_link(self, member, settings, state, player=None):
        """Track only roles/nicknames managed by linking; keep failed work retryable."""
        config = self.config.member(member)
        warnings = []
        desired = set()
        if player is not None:
            if settings["linked_role"]:
                desired.add(settings["linked_role"])
            alliance = player.get("alliance")
            if (settings["alliance_role"] and player.get("kid") == settings["kingdom"]
                    and isinstance(alliance, dict) and alliance.get("abbr") == settings["alliance"]):
                desired.add(settings["alliance_role"])
            elif settings["alliance_role"] and "alliance" not in player:
                raise APIError(200, "schema")
        assigned = set(state["assigned_roles"])
        held = {role.id for role in member.roles}
        for role_id in list(desired):
            error = self._role_error(member.guild, member.guild.get_role(role_id))
            if error:
                warnings.append(error)
                desired.discard(role_id)
        for role_id in sorted(assigned - desired):
            role = member.guild.get_role(role_id)
            if role is None or role_id not in held:
                assigned.discard(role_id)
                await config.assigned_roles.set(sorted(assigned))
                continue
            if (role.managed or role.is_default() or role >= member.guild.me.top_role
                    or not member.guild.me.guild_permissions.manage_roles):
                warnings.append("A previously assigned role cannot be removed; "
                                "check Manage Roles and role hierarchy, then sync/unlink again.")
                continue
            try:
                await member.remove_roles(role, reason="Kingshot unverified link cleanup")
            except discord.HTTPException:
                warnings.append("Discord could not remove a previous linked role; retry sync/unlink.")
                continue
            assigned.discard(role_id)
            await config.assigned_roles.set(sorted(assigned))
        for role_id in sorted(desired):
            role = member.guild.get_role(role_id)
            if role_id in held:
                continue
            # Reserve before granting so interrupted operations can be cleaned up.
            assigned.add(role_id)
            await config.assigned_roles.set(sorted(assigned))
            try:
                await member.add_roles(role, reason="Kingshot self-service link (UNVERIFIED)")
            except discord.HTTPException:
                warnings.append("Discord could not assign a configured role; retry sync.")
        nickname = player.get("nick_name")[:32] if player and settings["link_nickname"] else None
        previous_applied = state["applied_nick"]
        pending_nick = state["pending_nick"]
        if nickname is not None and nickname != member.nick:
            if not member.guild.me.guild_permissions.manage_nicknames:
                warnings.append("The bot needs Manage Nicknames; the account is linked but "
                                "the nickname was not changed.")
            else:
                if previous_applied is None and pending_nick is None:
                    await config.original_nick.set(member.nick)
                await config.pending_nick.set(nickname)
                try:
                    await member.edit(nick=nickname, reason="Kingshot unverified game-name sync")
                except discord.HTTPException:
                    warnings.append("Discord could not change the nickname (server owner or role "
                                    "hierarchy may prevent it); retry sync.")
                else:
                    await config.applied_nick.set(nickname)
                    await config.pending_nick.set(None)
        elif nickname is not None and member.nick == nickname and pending_nick is not None:
            await config.applied_nick.set(nickname)
            await config.pending_nick.set(None)
        elif nickname is None and (previous_applied is not None or pending_nick is not None):
            if member.nick is not None and member.nick in (previous_applied, pending_nick):
                try:
                    await member.edit(nick=state["original_nick"],
                                      reason="Kingshot restore previous server nickname")
                except discord.HTTPException:
                    warnings.append("Discord could not restore the previous nickname; retry sync/unlink.")
                else:
                    await config.applied_nick.set(None)
                    await config.pending_nick.set(None)
                    await config.original_nick.set(None)
            else:
                # Do not overwrite a nickname manually changed since the last cog update.
                await config.applied_nick.set(None)
                await config.pending_nick.set(None)
                await config.original_nick.set(None)
        return warnings

    async def _link_account(self, ctx, governor_id=None):
        async with self._lock(ctx.guild.id):
            member = await ctx.guild.fetch_member(ctx.author.id)
            config = self.config.member(member)
            state = await config.all()
            governor_id = governor_id if governor_id is not None else state["governor_id"]
            if governor_id is None:
                raise ValueError("Link an account first with link <governor_id>.")
            governor_id = identifier(governor_id)
            members = await self.config.all_members(ctx.guild)
            if any(uid != ctx.author.id and values.get("governor_id") == governor_id
                   for uid, values in members.items()):
                raise ValueError("That governor ID is already linked in this server. "
                                 "Links are unverified; ask an administrator to clear a disputed link.")
            data = await self.api.player(governor_id)
            player = mapping(data, "player")
            if (player.get("governor_id") != governor_id
                    or not isinstance(player.get("nick_name"), str) or not player["nick_name"].strip()):
                raise APIError(200, "schema")
            settings = await self._settings(ctx)
            if settings["alliance_role"]:
                if ("alliance" not in player or "kid" not in player
                        or (player["alliance"] is not None
                            and (not isinstance(player["alliance"], dict)
                                 or not isinstance(player["alliance"].get("abbr"), str)))):
                    raise APIError(200, "schema")
                identifier(player["kid"])
            await config.governor_id.set(governor_id)
            if state["governor_id"] != governor_id:
                await config.linked_at.set(time.time())
            warnings = await self._apply_link(member, settings, state, player)
        result = embed("Kingshot link - UNVERIFIED", data,
                       f"Governor {governor_id}: {text(player['nick_name'])}\n"
                       "Self-service linking does not verify game-account ownership. "
                       "Never use linked roles as proof of identity or privileged access.")
        result.add_field(name="Nickname / roles",
                         value="\n".join(warnings)[:1024] if warnings else "Synchronization completed.",
                         inline=False)
        await ctx.send(embed=result, allowed_mentions=discord.AllowedMentions.none())

    @commands.command(name="link", ignore_extra=False)
    @commands.guild_only()
    @commands.bot_has_permissions(embed_links=True)
    async def link(self, ctx, governor_id: int):
        """Link an in-game governor ID (not Discord user ID). Ownership is UNVERIFIED."""
        await self._link_account(ctx, governor_id)

    @commands.command(name="sync", ignore_extra=False)
    @commands.guild_only()
    @commands.bot_has_permissions(embed_links=True)
    async def sync(self, ctx):
        """Sync your linked game name and configured roles. API caching still applies."""
        await self._link_account(ctx)

    async def _unlink_account(self, member):
        async with self._lock(member.guild.id):
            member = await member.guild.fetch_member(member.id)
            config = self.config.member(member)
            state = await config.all()
            await config.governor_id.set(None)
            await config.linked_at.set(0)
            warnings = await self._apply_link(member, await self.config.guild(member.guild).all(), state)
            remaining = await config.all()
            if (not remaining["assigned_roles"] and remaining["applied_nick"] is None
                    and remaining["pending_nick"] is None):
                await config.clear()
        return warnings

    @commands.command(name="unlink", ignore_extra=False)
    @commands.guild_only()
    async def unlink(self, ctx):
        """Unlink your account, remove cog-assigned roles and restore its previous nickname."""
        warnings = await self._unlink_account(ctx.author)
        await ctx.send("Account unlinked. " + (" ".join(warnings) if warnings else
                       "Linked-role and nickname cleanup completed."),
                       allowed_mentions=discord.AllowedMentions.none())

    @ks.group(name="linkset", invoke_without_command=True)
    @commands.admin_or_permissions(administrator=True)
    async def linkset(self, ctx):
        """Configure existing non-privileged roles for UNVERIFIED self-service links."""
        await ctx.send_help()

    @linkset.command(name="role")
    async def linkset_role(self, ctx, role: Optional[discord.Role] = None):
        """Select an existing linked-player role. Omit role to disable assignment."""
        if role is not None:
            self._validate_link_role(ctx, role)
        await self._change(ctx, linked_role=role.id if role else None)

    @linkset.command(name="alliance")
    async def linkset_alliance(self, ctx, role: Optional[discord.Role] = None):
        """Select a role for the configured kingdom/tag. Omit role to disable."""
        if role is not None:
            self._validate_link_role(ctx, role)
        await self._change(ctx, alliance_role=role.id if role else None)

    @linkset.command(name="nickname")
    async def linkset_nickname(self, ctx, enabled: bool):
        """Enable/disable game-name server nickname sync; applied on next link/sync."""
        await self._change(ctx, link_nickname=enabled)

    @linkset.command(name="status")
    async def linkset_status(self, ctx):
        """Show linking settings, without listing member associations."""
        settings = await self._settings(ctx)
        await ctx.send(f"Links are UNVERIFIED. Nickname sync: {settings['link_nickname']}\n"
                       f"Linked role ID: {settings['linked_role']}\n"
                       f"Alliance role ID: {settings['alliance_role']} "
                       f"(K{settings['kingdom']} / {text(settings['alliance'])})\n"
                       "Changes apply on the next link or sync; there is no scheduled sync.",
                       allowed_mentions=discord.AllowedMentions.none())

    @linkset.command(name="clear")
    async def linkset_clear(self, ctx, member: discord.Member):
        """Clear a disputed link and clean up that member's cog-assigned roles/nickname."""
        if ctx.author.id != ctx.guild.owner_id and member.top_role >= ctx.author.top_role:
            raise ValueError("You may only clear links for members below your highest role.")
        warnings = await self._unlink_account(member)
        await ctx.send("Member link cleared. " + (" ".join(warnings) if warnings else
                       "Linked-role and nickname cleanup completed."),
                       allowed_mentions=discord.AllowedMentions.none())

    @ks.command(name="player")
    async def player(self, ctx, governor_id: int):
        """Player profile by in-game governor ID, not internal UID."""
        data = await self.api.player(governor_id)
        player = mapping(data, "player")
        result = embed(text(player.get("nick_name")), data)
        fields(result, player, PLAYER_STATS)
        result.add_field(name="Governor ID", value=text(player.get("governor_id")))
        alliance = player.get("alliance")
        result.add_field(name="Alliance", value=text(alliance.get("abbr"))
                         if isinstance(alliance, dict) else "No alliance on record")
        image(result, player.get("avatar_url"))
        await ctx.send(embed=result)

    @ks.command(name="heroes")
    async def heroes(self, ctx, governor_id: int):
        """Recorded arena defence heroes, not the player's complete hero collection."""
        data = await self.api.player(governor_id)
        heroes = data.get("heroes")
        if not isinstance(heroes, list):
            raise APIError(200, "schema")
        lines = []
        for hero in heroes:
            if not isinstance(hero, dict):
                raise APIError(200, "schema")
            lines.append(f"**{text(hero.get('name'))}** | Level {number(hero.get('level'))} | "
                         f"Stars {text(hero.get('star_label', hero.get('stars')))} | "
                         f"Power {number(hero.get('power'))}")
            gear = hero.get("gear", [])
            if not isinstance(gear, list):
                raise APIError(200, "schema")
            for item in gear:
                if not isinstance(item, dict):
                    raise APIError(200, "schema")
                lines.append(f"  {text(item.get('name'))}: +{number(item.get('enhancement_level'))}; "
                             f"mastery {number(item.get('refine_level'))}; "
                             f"{text(item.get('quality_label'))}")
            exclusive = hero.get("exclusive_gear")
            if isinstance(exclusive, dict):
                lines.append(f"  Widget: {text(exclusive.get('name'))}, "
                             f"level {number(exclusive.get('level'))}")
        await self._send_pages(ctx, "Arena defence heroes", lines, data)

    @ks.command(name="gear")
    async def gear(self, ctx, governor_id: int):
        """Governor equipment and documented upgrades."""
        data = await self.api.player(governor_id)
        gear = mapping(data, "gov_gear")
        if gear.get("hidden") is True:
            await ctx.send(embed=embed("Governor gear hidden", data,
                                      text(gear.get("message", "Equipment is hidden."), 1000)))
            return
        items = gear.get("items")
        if not isinstance(items, list):
            raise APIError(200, "schema")
        lines = []
        for item in items:
            if not isinstance(item, dict):
                raise APIError(200, "schema")
            lines.append(f"**{text(item.get('name'))}** | Slot {text(item.get('slot'))} | "
                         f"Quality {text(item.get('quality'))} | Tier {text(item.get('tier'))} | "
                         f"Stars {number(item.get('star'))} | Upgrade {number(item.get('strength_level'))} | "
                         f"Score {number(item.get('score'))} | Combat {number(item.get('combat'))}")
        await self._send_pages(ctx, "Governor equipment", lines, data)

    @ks.command(name="compare")
    async def compare(self, ctx, player1: int, player2: int):
        """Compare profiles and all available player ranking values and kingdom positions."""
        first, second = await asyncio.gather(self.api.player(player1), self.api.player(player2))
        await menu(ctx, comparison_pages(first, second, PLAYER_STATS), DEFAULT_CONTROLS)

    @ks.command(name="scout", aliases=["comparekingdoms"], ignore_extra=False)
    async def scout(self, ctx, kingdom1: int, kingdom2: int):
        """Compare two kingdoms and their top alliances, power, TC and Mystic Trial players."""
        kids = (identifier(kingdom1), identifier(kingdom2))
        if kids[0] == kids[1]:
            raise ValueError("Choose two different kingdom IDs.")
        data = await asyncio.gather(*(self.api.kingdom(kid) for kid in kids))
        kingdoms = [mapping(item, "kingdom") for item in data]
        if any(kingdom.get("kid") != kid for kid, kingdom in zip(kids, kingdoms)):
            raise APIError(200, "schema")
        boards, errors = [{}, {}], [{}, {}]
        for index, kid in enumerate(kids):
            for board, _, _ in SCOUT_BOARDS:
                try:
                    boards[index][board] = scout_rows(await self.api.ranks(kid, board), board)
                except APIError as error:
                    if error.status not in (404, 200):
                        raise
                    errors[index][board] = str(error)
        await menu(ctx, scout_embeds(kids, kingdoms, boards, errors), DEFAULT_CONTROLS)

    @ks.command(name="ranks", ignore_extra=False)
    async def ranks(self, ctx, board: str = "personal_power", kid: Optional[int] = None):
        """Leaderboard: ranks [board] [kingdom_id]. Defaults to the saved kingdom."""
        kid = await self._kingdom_id(ctx, kid)
        data = await self.api.ranks(kid, board)
        required = {"aid", "score"} if board.startswith("alliance_") else {"uid", "score"}
        rows = records(data, required)
        if not rows:
            raise APIError(200, "schema")
        lines = [f"{i}. {text(row.get('abbr', row.get('nick_name')))} "
                 f"({text(row.get('governor_id', row.get('aid')))}) - {number(row.get('score'))}"
                 for i, row in enumerate(rows[:100], 1)]
        await self._send_pages(ctx, f"K{kid} {board}", lines, data)

    async def _alliance(self, ctx, kid: Optional[int] = None, tag: Optional[str] = None):
        if kid is None and tag is None:
            settings = await self._settings(ctx)
            kid, tag = settings["kingdom"], settings["alliance"]
        elif kid is None or tag is None:
            raise ValueError("Supply both kingdom ID and case-sensitive alliance tag, "
                             "for example: ks alliance 571 ABC. Omit both for the saved alliance.")
        return await self.api.alliance(identifier(kid), alliance_tag(tag))

    @ks.command(name="alliance", ignore_extra=False)
    async def alliance(self, ctx, kid: Optional[int] = None, tag: Optional[str] = None):
        """Alliance overview: alliance [kingdom_id tag]. Omit both for the saved alliance."""
        data = await self._alliance(ctx, kid, tag)
        alliance = mapping(data, "alliance")
        result = embed(f"[{text(alliance.get('abbr'))}] {text(alliance.get('name'))}", data)
        fields(result, alliance, [("Kingdom", "kid", True), ("Power", "power", True),
                                 ("Members", "count", True), ("Power rank", "power_rank", True),
                                 ("Leader", "leader_name", False)])
        image(result, alliance.get("flag_url"))
        await ctx.send(embed=result)

    @ks.command(name="members", ignore_extra=False)
    async def members(self, ctx, kid: Optional[int] = None, tag: Optional[str] = None):
        """Roster: members [kingdom_id tag]. Stale activity fields are excluded."""
        data = await self._alliance(ctx, kid, tag)
        alliance = mapping(data, "alliance")
        lines = [f"**{text(m.get('nick_name'))}** ({text(m.get('governor_id'))}) | "
                 f"{text(m.get('alliance_rank_label'))} | Power {number(m.get('power'))} | "
                 f"TC {number(m.get('town_center_level'))}" for m in roster(data)]
        await self._send_pages(ctx, f"K{alliance.get('kid')} [{text(alliance.get('abbr'))}] members",
                               lines, data)

    @ks.command(name="top", ignore_extra=False)
    async def top(self, ctx, kid: Optional[int] = None, tag: Optional[str] = None):
        """Ten strongest members: top [kingdom_id tag]. Omit both for saved settings."""
        data = await self._alliance(ctx, kid, tag)
        alliance = mapping(data, "alliance")
        members = [m for m in roster(data) if isinstance(m.get("power"), (int, float))
                   and not isinstance(m.get("power"), bool)]
        members.sort(key=lambda m: m["power"], reverse=True)
        await self._send_pages(ctx, f"K{alliance.get('kid')} [{text(alliance.get('abbr'))}] strongest",
                               [f"{i}. {text(m.get('nick_name'))}: {number(m.get('power'))}"
                                for i, m in enumerate(members[:10], 1)], data)

    @ks.command(name="inactive")
    async def inactive(self, ctx):
        """Disabled: MightPulse activity fields are stale."""
        await ctx.send("Inactive reporting is disabled. The game no longer sends player activity; "
                       "MightPulse's online/last_active_at/last_login values are stale. "
                       "There is no verified alternative activity source in this cog.")

    @ks.command(name="progress")
    async def progress(self, ctx):
        """Power change between the latest two hourly observed roster snapshots."""
        settings = await self._settings(ctx)
        snapshots = settings["snapshots"]
        if len(snapshots) < 2:
            await ctx.send("Two hourly snapshots are needed. Set the alliance/kingdom and key, "
                           "then allow at least one hour between successful snapshots.")
            return
        before, after = snapshots[-2:]
        lines = []
        for uid, member in after["members"].items():
            old = before["members"].get(uid)
            if old and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                           for v in (member.get("power"), old.get("power"))):
                lines.append(f"{text(member['name'])}: "
                             f"{member['power'] - old['power']:+,.0f}")
        await self._send_pages(ctx, "Observed member power progress",
                               [f"{timestamp(before['at'])} -> {timestamp(after['at'])}"] + lines)

    @ks.command(name="changes")
    async def changes(self, ctx):
        """New and departed members between the latest two stored rosters."""
        snapshots = (await self._settings(ctx))["snapshots"]
        if len(snapshots) < 2:
            await ctx.send("Two hourly roster snapshots are needed to detect membership changes.")
            return
        before, after = snapshots[-2:]
        old, new = before["members"], after["members"]
        lines = [f"{timestamp(before['at'])} -> {timestamp(after['at'])}"]
        lines += [f"Joined: {text(new[k]['name'])}" for k in sorted(new.keys() - old.keys())]
        lines += [f"Departed: {text(old[k]['name'])}" for k in sorted(old.keys() - new.keys())]
        if len(lines) == 1:
            lines.append("No membership changes observed.")
        await self._send_pages(ctx, "Observed roster changes", lines)

    @ks.command(name="kingdom", ignore_extra=False)
    async def kingdom(self, ctx, kid: Optional[int] = None):
        """Kingdom statistics: kingdom [kingdom_id]. Omit ID for the saved kingdom."""
        kid = await self._kingdom_id(ctx, kid)
        data = await self.api.kingdom(kid)
        kingdom = mapping(data, "kingdom")
        result = embed(f"Kingdom {kid}", data)
        fields(result, kingdom, [
            ("Name", "name", False), ("Power", "power", True), ("Average power", "avg_power", True),
            ("Power rank", "power_rank", True), ("Players", "player_count", True),
            ("Players on map (not activity)", "located", True), ("Alliances", "alliance_count", True),
            ("Kills", "kills", True), ("Age in days", "age_days", True),
            ("Opened", "opened_on", False),
        ])
        image(result, kingdom.get("banner_url"))
        await ctx.send(embed=result)

    @ks.command(name="kvk", ignore_extra=False)
    async def kvk(self, ctx, kid: Optional[int] = None):
        """Matchup, season and stage: kvk [kingdom_id]. No score request."""
        kid = await self._kingdom_id(ctx, kid)
        await ctx.send(embed=kvk_embed(kid, await self.api.kvk(kid)))

    async def _score_command(self, ctx, kid: Optional[int] = None):
        kid = await self._kingdom_id(ctx, kid)
        matchup, scores, unavailable = await self._kvk_data(kid)
        await ctx.send(embed=kvk_embed(kid, matchup, scores, unavailable, time.time()))

    @ks.command(name="score", ignore_extra=False)
    async def score(self, ctx, kid: Optional[int] = None):
        """Preparation scores: score [kingdom_id]. Not castle battle results."""
        await self._score_command(ctx, kid)

    @ks.command(name="kvkdays", ignore_extra=False)
    async def kvkdays(self, ctx, kid: Optional[int] = None):
        """Preparation days I-V: kvkdays [kingdom_id]. Omit ID for the saved kingdom."""
        await self._score_command(ctx, kid)

    @ks.command(name="matchups")
    async def matchups(self, ctx):
        """Available current-season matchup records."""
        data = await self.api.matchups()
        matches = records(data, {"kingdom", "opponent"})
        if not matches:
            raise APIError(200, "schema")
        lines = []
        for match in matches:
            ours, theirs = match.get("kingdom"), match.get("opponent")
            ours = ours.get("kid") if isinstance(ours, dict) else ours
            theirs = theirs.get("kid") if isinstance(theirs, dict) else theirs
            lines.append(f"K{text(ours)} vs "
                         f"{'Bye' if match.get('bye') is True else 'K' + text(theirs)}")
        await self._send_pages(ctx, f"Current-season matchups (season {text(data.get('season'))})",
                               lines, data)

    @ks.command(name="events", ignore_extra=False)
    async def events(self, ctx, kid: Optional[int] = None):
        """Kingdom calendar: events [kingdom_id]. Not personal alliance events."""
        kid = await self._kingdom_id(ctx, kid)
        data = await self.api.events(kid)
        await self._send_pages(ctx, f"K{kid} event calendar",
                               [f"**{text(e.get('name'))}** | {text(e.get('category'))}\n"
                                f"{timestamp(e.get('begin_ts'))} -> {timestamp(e.get('end_ts'))}"
                                for e in calendar(data)], data)

    @ks.command(name="usage")
    @commands.is_owner()
    async def usage(self, ctx):
        """Bot owner only: shared subscription API allowance."""
        data = await self.api.usage()
        result = embed("MightPulse usage", description=f"Mighty: {data.get('mighty') is True}")
        for label, key in (("Minute", "minute"), ("Daily", "daily")):
            bucket = mapping(data, key)
            result.add_field(name=label, value=f"Used {number(bucket.get('used'))} / "
                             f"{number(bucket.get('limit'))}; remaining {number(bucket.get('remaining'))}"
                             f"\nResets (UTC): {text(bucket.get('resets_at'))}", inline=False)
        await ctx.send(embed=result)

    @commands.group(name="ksset", invoke_without_command=True)
    @commands.is_owner()
    async def ksset(self, ctx):
        """Bot-owner configuration; key is shared, settings are per server."""
        await ctx.send_help()

    @ksset.command(name="apikey")
    async def apikey(self, ctx):
        """Send a private Red shared-token modal. Never pass a key as a command argument."""
        self._token_views = {view for view in self._token_views if not view.is_finished()}
        view = SetApiView(default_service="mightpulse", default_keys={"api_key": ""})
        self._token_views.add(view)
        try:
            await ctx.author.send(
                "Use Red's button/modal below. Service: `mightpulse`; token name: `api_key`. "
                "Enter your key only in the modal, never in a channel or command.",
                view=view,
            )
        except discord.Forbidden:
            view.stop()
            self._token_views.discard(view)
            await ctx.send("Enable DMs from this server, or use Red's `set api mightpulse` "
                           "in a DM with the bot and enter token `api_key` in its modal.")
            return
        await ctx.send("Secure shared-token setup sent by DM. The key is bot-wide; "
                       "kingdom and alliance settings remain server-specific.")

    @ksset.command(name="kingdom")
    @commands.guild_only()
    async def set_kingdom(self, ctx, kid: int):
        """Set this server's kingdom and reset alliance history."""
        await self._change(ctx, kingdom=identifier(kid), snapshots=[], snapshot_attempt=0,
                           auto_attempt=0, reminder_sent={}, reminder_attempt=0)

    @ksset.command(name="alliance")
    @commands.guild_only()
    async def set_alliance(self, ctx, tag: str):
        """Set case-sensitive alliance tag and reset roster history."""
        await self._change(ctx, alliance=alliance_tag(tag), snapshots=[], snapshot_attempt=0)

    @ksset.command(name="channel")
    @commands.guild_only()
    async def set_channel(self, ctx, channel: discord.TextChannel):
        """Set fallback dashboard/reminder channel."""
        self._channel(ctx.guild, channel.id)
        await self._change(ctx, channel=channel.id, message_id=None, auto_attempt=0)

    @ksset.command(name="interval")
    @commands.guild_only()
    async def set_interval(self, ctx, minutes: int):
        """Set dashboard refresh interval, minimum five minutes."""
        await self._change(ctx, interval=self._interval(minutes))

    @ksset.command(name="status")
    @commands.guild_only()
    async def status(self, ctx):
        """Settings and background errors, never credentials."""
        settings = await self._settings(ctx)
        result = embed("Kingshot configuration")
        result.add_field(name="Kingdom / alliance",
                         value=f"{settings['kingdom']} / {text(settings['alliance'])}")
        result.add_field(name="Key", value="Configured (shared)" if await self._token() else "Not set")
        result.add_field(name="Dashboard", value=f"Enabled: {settings['auto_enabled']}\n"
                         f"Interval: {settings['interval']} minutes\n"
                         f"Channel ID: {settings['auto_channel'] or settings['channel']}\n"
                         f"Message ID: {settings['message_id']}\n"
                         f"Last success: {timestamp(settings['auto_success'])}", inline=False)
        result.add_field(name="History / reminders",
                         value=f"Snapshots: {len(settings['snapshots'])} (hourly)\n"
                         f"Reminders: {settings['remind_enabled']}\n"
                         f"Channel ID: {settings['remind_channel'] or settings['channel']}")
        for key in ("auto_error", "snapshot_error", "reminder_error"):
            result.add_field(name=key, value=text(settings[key] or "None"), inline=False)
        await ctx.send(embed=result)

    @ksset.command(name="test")
    async def test_api(self, ctx):
        """Check authentication/subscription via uncounted usage endpoint."""
        data = await self.api.usage()
        await ctx.send("MightPulse authentication succeeded. Mighty access: "
                       + ("yes." if data.get("mighty") is True else "no; events/KvK need Mighty."))

    @ks.group(name="auto", invoke_without_command=True)
    @commands.admin_or_permissions(administrator=True)
    async def auto(self, ctx):
        """Administrator controls for the single editable KvK dashboard."""
        await ctx.send_help()

    @auto.command(name="enable")
    async def auto_enable(self, ctx):
        settings = await self._settings(ctx)
        self._channel(ctx.guild, settings["auto_channel"] or settings["channel"])
        await self._refresh(ctx.guild)
        await self._change(ctx, auto_enabled=True)

    @auto.command(name="disable")
    async def auto_disable(self, ctx):
        await self._change(ctx, auto_enabled=False)

    @auto.command(name="channel")
    async def auto_channel(self, ctx, channel: discord.TextChannel):
        self._channel(ctx.guild, channel.id)
        await self._change(ctx, auto_channel=channel.id, message_id=None, auto_attempt=0)

    @auto.command(name="interval")
    async def auto_interval(self, ctx, minutes: int):
        await self._change(ctx, interval=self._interval(minutes))

    @auto.command(name="refresh")
    async def auto_refresh(self, ctx):
        """Refresh/edit now; shared five-minute API cache remains respected."""
        await self._refresh(ctx.guild)
        await ctx.tick()

    async def _refresh(self, guild):
        async with self._lock(guild.id):
            config = self.config.guild(guild)
            settings = await config.all()
            await config.auto_attempt.set(time.time())
            try:
                await self._dashboard(guild, settings)
            except (APIError, ValueError, discord.HTTPException) as error:
                await config.auto_error.set(self._safe_error(error))
                raise

    async def _dashboard(self, guild, settings):
        channel = self._channel(guild, settings["auto_channel"] or settings["channel"])
        matchup, scores, unavailable = await self._kvk_data(settings["kingdom"])
        updated = time.time()
        result = kvk_embed(settings["kingdom"], matchup, scores, unavailable, updated)
        marker = f"Kingshot dashboard | Guild {guild.id}"
        result.set_footer(text=result.footer.text + " | " + marker)
        message = None
        if settings["message_id"]:
            try:
                message = await channel.fetch_message(settings["message_id"])
            except discord.NotFound:
                message = None
        else:
            # Recover a send interrupted before Config persisted its ID.
            async for candidate in channel.history(limit=50):
                if candidate.author.id == self.bot.user.id and any(
                    item.footer.text and item.footer.text.endswith(marker)
                    for item in candidate.embeds
                ):
                    message = candidate
                    await self.config.guild(guild).message_id.set(candidate.id)
                    break
        if message is not None:
            if message.author.id != self.bot.user.id:
                raise ValueError("Stored dashboard message is not owned by this bot.")
            try:
                await message.edit(embed=result, allowed_mentions=discord.AllowedMentions.none())
            except discord.NotFound:
                message = None
        if message is None:
            message = await channel.send(embed=result, allowed_mentions=discord.AllowedMentions.none())
            await self.config.guild(guild).message_id.set(message.id)
        await self.config.guild(guild).auto_success.set(updated)
        await self.config.guild(guild).auto_error.set(None)

    @ks.group(name="remind", invoke_without_command=True)
    @commands.admin_or_permissions(administrator=True)
    async def remind(self, ctx):
        """Kingdom calendar reminders and explicitly manual alliance events."""
        await ctx.send_help()

    @remind.command(name="enable")
    async def remind_enable(self, ctx):
        settings = await self._settings(ctx)
        self._channel(ctx.guild, settings["remind_channel"] or settings["channel"])
        # Prove access before enabling, rather than reporting success for a non-Mighty key.
        await self.api.events(settings["kingdom"])
        await self._change(ctx, remind_enabled=True, reminder_attempt=0)

    @remind.command(name="disable")
    async def remind_disable(self, ctx):
        await self._change(ctx, remind_enabled=False)

    @remind.command(name="channel")
    async def remind_channel(self, ctx, channel: discord.TextChannel):
        self._channel(ctx.guild, channel.id)
        await self._change(ctx, remind_channel=channel.id)

    @remind.command(name="offsets")
    async def remind_offsets(self, ctx, *hours: int):
        """Set 1-8 reminder offsets in hours (1-168); defaults to 24 and 1."""
        if not 1 <= len(hours) <= 8 or any(not 1 <= h <= 168 for h in hours):
            raise ValueError("Supply 1-8 offsets in hours, each between 1 and 168.")
        await self._change(ctx, remind_offsets=sorted(set(hours), reverse=True))

    @remind.command(name="add")
    async def remind_add(self, ctx, begin_ts: int, *, name: str):
        """Add a manual, one-off alliance event: Unix start seconds then name."""
        if not time.time() < begin_ts <= time.time() + 366 * 86400:
            raise ValueError("Use a future Unix timestamp within the next year.")
        if not 1 <= len(name) <= 100:
            raise ValueError("Event names must contain 1-100 characters.")
        async with self._lock(ctx.guild.id):
            config = self.config.guild(ctx.guild)
            events = await config.manual_events()
            events = [e for e in events if e["begin_ts"] > time.time()]
            if len(events) >= 50:
                raise ValueError("At most 50 future manual events are allowed.")
            events.append({"name": name, "begin_ts": begin_ts, "category": "Manual alliance event"})
            await config.manual_events.set(events)
        await ctx.tick()

    @remind.command(name="remove")
    async def remind_remove(self, ctx, begin_ts: int, *, name: str):
        """Remove the manual event matching its timestamp and exact name."""
        async with self._lock(ctx.guild.id):
            config = self.config.guild(ctx.guild)
            events = await config.manual_events()
            filtered = [e for e in events if not (e["begin_ts"] == begin_ts and e["name"] == name)]
            if len(events) == len(filtered):
                raise ValueError("No matching manual event exists.")
            await config.manual_events.set(filtered)
        await ctx.tick()

    @remind.command(name="list")
    async def remind_list(self, ctx):
        settings = await self._settings(ctx)
        await self._send_pages(ctx, "Event reminder configuration", [
            f"Enabled: {settings['remind_enabled']}",
            f"Offsets (hours): {', '.join(map(str, settings['remind_offsets']))}",
            f"Channel ID: {settings['remind_channel'] or settings['channel']}",
            "Manual events (not provided by MightPulse):",
        ] + [f"{text(e['name'])}: {timestamp(e['begin_ts'])}" for e in settings["manual_events"]])

    @staticmethod
    def _safe_error(error):
        if isinstance(error, (APIError, ValueError)):
            return str(error)
        if isinstance(error, discord.Forbidden):
            return "Discord permissions are missing."
        return "Discord request failed. Check channel and message permissions."

    async def _snapshot(self, guild, settings):
        data = await self.api.alliance(settings["kingdom"], settings["alliance"])
        members = {}
        for member in roster(data):
            uid = member.get("uid")
            if not isinstance(uid, int) or isinstance(uid, bool) or uid <= 0 or str(uid) in members:
                raise APIError(200, "schema")
            members[str(uid)] = {"name": member.get("nick_name"), "power": member.get("power"),
                                 "governor_id": member.get("governor_id")}
        # Do not interpret a partial/error roster as mass departures.
        alliance = mapping(data, "alliance")
        if isinstance(alliance.get("count"), int) and alliance["count"] != len(members):
            raise APIError(200, "schema")
        snapshots = settings["snapshots"]
        if snapshots and data.get("cached_at") is not None:
            if snapshots[-1].get("cached_at") == data["cached_at"]:
                return
        snapshots.append({"at": time.time(), "cached_at": data.get("cached_at"), "members": members})
        await self.config.guild(guild).snapshots.set(snapshots[-2:])

    async def _reminders(self, guild, settings, now):
        channel = self._channel(guild, settings["remind_channel"] or settings["channel"])
        events = calendar(await self.api.events(settings["kingdom"]))
        events = events + settings["manual_events"]
        config = self.config.guild(guild)
        sent = {k: v for k, v in settings["reminder_sent"].items() if v > now - 7 * 86400}
        await config.reminder_sent.set(sent)
        for event in events:
            begin = event.get("begin_ts")
            if not isinstance(begin, (int, float)) or isinstance(begin, bool) or begin <= now:
                continue
            for hours in settings["remind_offsets"]:
                due = begin - hours * 3600
                # Ten-minute delivery window; do not flood catch-up notifications after downtime.
                if not 0 <= now - due < 600:
                    continue
                identity = f"{settings['kingdom']}|{event.get('category')}|{event.get('name')}|{begin}|{hours}"
                key = hashlib.sha256(identity.encode()).hexdigest()
                if key in sent:
                    continue
                result = embed("Upcoming " + text(event.get("name")), description=
                               f"{text(event.get('category'))}\nStarts {timestamp(begin)}\n"
                               f"Reminder: {hours} hour(s) before start.")
                # Reserve durably before sending: at-most-once across process crashes/reloads.
                sent[key] = begin
                await config.reminder_sent.set(sent)
                try:
                    await channel.send(embed=result, allowed_mentions=discord.AllowedMentions.none())
                except (discord.Forbidden, discord.NotFound):
                    del sent[key]
                    await config.reminder_sent.set(sent)
                    raise

    async def _tick_guild(self, guild, now):
        async with self._lock(guild.id):
            config = self.config.guild(guild)
            settings = await config.all()
            jobs = [
                ("snapshot", now - settings["snapshot_attempt"] >= 3600),
                ("auto", settings["auto_enabled"]
                 and now - settings["auto_attempt"] >= settings["interval"] * 60),
                ("reminder", settings["remind_enabled"]
                 and now - settings["reminder_attempt"] >= 300),
            ]
            for job, due in jobs:
                if not due:
                    continue
                await config.get_attr(job + "_attempt").set(now)
                try:
                    if job == "snapshot":
                        await self._snapshot(guild, settings)
                    elif job == "auto":
                        await self._dashboard(guild, settings)
                    else:
                        await self._reminders(guild, settings, now)
                    await config.get_attr(job + "_error").set(None)
                except (APIError, ValueError, discord.HTTPException) as error:
                    await config.get_attr(job + "_error").set(self._safe_error(error))
                    log.warning("Kingshot %s failed in guild %s (%s)", job, guild.id,
                                type(error).__name__)
                except Exception as error:
                    # Keep other guild jobs running, but surface the failure without sensitive details.
                    await config.get_attr(job + "_error").set("Unexpected cog error; inspect Kingshot.")
                    log.error("Kingshot %s failed in guild %s (%s)", job, guild.id,
                              type(error).__name__)

    async def _run(self):
        await self.bot.wait_until_red_ready()
        while not self._closing:
            try:
                if await self._token():
                    for guild_id in await self.config.all_guilds():
                        guild = self.bot.get_guild(guild_id)
                        if guild is not None and not await self.bot.cog_disabled_in_guild(self, guild):
                            await self._tick_guild(guild, time.time())
            except Exception as error:
                log.error("Kingshot scheduler failed (%s)", type(error).__name__)
            await asyncio.sleep(60)
