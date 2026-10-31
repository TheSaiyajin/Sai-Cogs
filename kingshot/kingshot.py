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
    calendar, embed, fields, image, kvk_embed, mapping, number, pages,
    records, roster, text, timestamp,
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
)


class Kingshot(commands.Cog):
    """Kingshot statistics and Mighty preparation score tracking."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=5702026103101, force_registration=True)
        self.config.register_guild(**GUILD_DEFAULTS)
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
        # Game IDs are not linked to Discord users.
        return

    async def cog_command_error(self, ctx, error):
        original = getattr(error, "original", error)
        if isinstance(original, (APIError, ValueError)):
            await ctx.send(str(original), allowed_mentions=discord.AllowedMentions.none())
        elif isinstance(original, (commands.BadArgument, commands.MissingRequiredArgument)):
            await ctx.send("Invalid or missing argument. See the command's help.")
        elif isinstance(original, discord.Forbidden):
            await ctx.send("I lack a required Discord permission (view/send/embed/read history).")
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
        """Compare recorded player statistics."""
        first, second = await asyncio.gather(self.api.player(player1), self.api.player(player2))
        left, right = mapping(first, "player"), mapping(second, "player")
        result = embed(f"{text(left.get('nick_name'), 90)} vs {text(right.get('nick_name'), 90)}")
        for label, key, _ in PLAYER_STATS:
            result.add_field(name=label,
                             value=f"{number(left.get(key))} / {number(right.get(key))}", inline=False)
        result.set_footer(text=f"MightPulse | Ages (seconds): {number(first.get('age_seconds'))} / "
                               f"{number(second.get('age_seconds'))}; not necessarily simultaneous")
        await ctx.send(embed=result)

    @ks.command(name="ranks")
    async def ranks(self, ctx, board: str = "personal_power"):
        """Kingdom leaderboard; defaults to personal_power."""
        settings = await self._settings(ctx)
        data = await self.api.ranks(settings["kingdom"], board)
        required = {"aid", "score"} if board.startswith("alliance_") else {"uid", "score"}
        rows = records(data, required)
        if not rows:
            raise APIError(200, "schema")
        lines = [f"{i}. {text(row.get('abbr', row.get('nick_name')))} "
                 f"({text(row.get('governor_id', row.get('aid')))}) - {number(row.get('score'))}"
                 for i, row in enumerate(rows[:100], 1)]
        await self._send_pages(ctx, f"K{settings['kingdom']} {board}", lines, data)

    async def _alliance(self, ctx):
        settings = await self._settings(ctx)
        return await self.api.alliance(settings["kingdom"], settings["alliance"])

    @ks.command(name="alliance")
    async def alliance(self, ctx):
        """Configured alliance overview."""
        data = await self._alliance(ctx)
        alliance = mapping(data, "alliance")
        result = embed(f"[{text(alliance.get('abbr'))}] {text(alliance.get('name'))}", data)
        fields(result, alliance, [("Kingdom", "kid", True), ("Power", "power", True),
                                 ("Members", "count", True), ("Power rank", "power_rank", True),
                                 ("Leader", "leader_name", False)])
        image(result, alliance.get("flag_url"))
        await ctx.send(embed=result)

    @ks.command(name="members")
    async def members(self, ctx):
        """Paginated alliance roster. Activity fields are intentionally excluded."""
        data = await self._alliance(ctx)
        lines = [f"**{text(m.get('nick_name'))}** ({text(m.get('governor_id'))}) | "
                 f"{text(m.get('alliance_rank_label'))} | Power {number(m.get('power'))} | "
                 f"TC {number(m.get('town_center_level'))}" for m in roster(data)]
        await self._send_pages(ctx, "Alliance members", lines, data)

    @ks.command(name="top")
    async def top(self, ctx):
        """Ten strongest recorded alliance members with published power."""
        data = await self._alliance(ctx)
        members = [m for m in roster(data) if isinstance(m.get("power"), (int, float))
                   and not isinstance(m.get("power"), bool)]
        members.sort(key=lambda m: m["power"], reverse=True)
        await self._send_pages(ctx, "Strongest alliance members",
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

    @ks.command(name="kingdom")
    async def kingdom(self, ctx):
        """Kingdom statistics; on-map player count is not recent activity."""
        settings = await self._settings(ctx)
        data = await self.api.kingdom(settings["kingdom"])
        kingdom = mapping(data, "kingdom")
        result = embed(f"Kingdom {settings['kingdom']}", data)
        fields(result, kingdom, [
            ("Name", "name", False), ("Power", "power", True), ("Average power", "avg_power", True),
            ("Power rank", "power_rank", True), ("Players", "player_count", True),
            ("Players on map (not activity)", "located", True), ("Alliances", "alliance_count", True),
            ("Kills", "kills", True), ("Age in days", "age_days", True),
            ("Opened", "opened_on", False),
        ])
        image(result, kingdom.get("banner_url"))
        await ctx.send(embed=result)

    @ks.command(name="kvk")
    async def kvk(self, ctx):
        """Current matchup, season and stage (no score request)."""
        kid = (await self._settings(ctx))["kingdom"]
        await ctx.send(embed=kvk_embed(kid, await self.api.kvk(kid)))

    async def _score_command(self, ctx):
        kid = (await self._settings(ctx))["kingdom"]
        matchup, scores, unavailable = await self._kvk_data(kid)
        await ctx.send(embed=kvk_embed(kid, matchup, scores, unavailable, time.time()))

    @ks.command(name="score")
    async def score(self, ctx):
        """Current preparation scores and standing, not castle battle results."""
        await self._score_command(ctx)

    @ks.command(name="kvkdays")
    async def kvkdays(self, ctx):
        """All published preparation days I-V with status and scores."""
        await self._score_command(ctx)

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

    @ks.command(name="events")
    async def events(self, ctx):
        """Documented kingdom calendar, not personal alliance events."""
        kid = (await self._settings(ctx))["kingdom"]
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
