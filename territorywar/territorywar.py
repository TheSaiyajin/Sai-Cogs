import asyncio
import logging
import random
import time
from io import BytesIO
from typing import Dict, List, Optional, Tuple

import discord
from discord.ext import tasks
from redbot.core import Config, commands
from redbot.core.utils.chat_formatting import humanize_number, pagify

from .battle import (
    battle_public_summary_lines,
    count_active_offensives,
    process_battle_tick,
    reinforcement_log_entry,
    territory_in_active_battle,
)
from .mapdata import build_alpha_map, is_connected, normalize_territory_key
from .minigames import (
    MINIGAMES,
    codebreaker_feedback,
    codebreaker_score,
    compute_power_gain,
    generate_codebreaker_secret,
    generate_memory_sequence,
    memory_score,
    validate_codebreaker_guess,
)
from .render import render_map_png


LOG = logging.getLogger("red.territorywar")


class TerritoryWar(commands.Cog):
    """Alpha territory war with two alliances and officer-led battles."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=581106319227, force_registration=True)
        self.config.register_guild(
            state="idle",
            season_id=0,
            registration_open=False,
            registered_players=[],
            map_id="alpha21",
            map_channel_id=0,
            feed_channel_id=0,
            map_message={"channel_id": 0, "message_id": 0},
            alliances={},
            territories={},
            players={},
            battles={},
            season_history=[],
            prep_end_ts=0.0,
            war_start_ts=0.0,
            war_end_ts=0.0,
            capital_unlock_ts=0.0,
            last_map_update_ts=0.0,
            minigame_sessions={},
            timings={
                "prep_hours": 6.0,
                "war_days": 7.0,
                "battle_interval_minutes": 30.0,
                "leader_inactive_hours": 24.0,
                "capital_unlock_day": 5.0,
                "session_timeout_minutes": 10.0,
                "map_update_minutes": 5.0,
            },
            values={
                "battle_tick_damage": 100,
                "neutral_defense_power": 400,
                "max_offensive_battles": 2,
                "capture_protection_minutes": 60,
                "starter_defense_power": 250,
                "capital_defense_power": 600,
            },
        )
        self._guild_locks: Dict[int, asyncio.Lock] = {}
        self.scheduler.start()

    def cog_unload(self):
        if self.scheduler.is_running():
            self.scheduler.cancel()

    @staticmethod
    def _now_ts() -> float:
        return float(time.time())

    @staticmethod
    def _fmt_ts(ts: float) -> str:
        if not ts:
            return "n/a"
        return f"<t:{int(ts)}:f>"

    def _lock_for(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self._guild_locks:
            self._guild_locks[guild_id] = asyncio.Lock()
        return self._guild_locks[guild_id]

    async def _ensure_alliance_state(self, guild: discord.Guild):
        guild_conf = self.config.guild(guild)
        alliances = await guild_conf.alliances()
        if alliances:
            return
        defaults = {
            "1": {
                "name": "Alliance 1",
                "members": [],
                "leader_id": 0,
                "advisor_ids": [],
                "reserve_power": 0,
                "log": [],
                "officer_activity": {},
                "last_leader_action_ts": 0.0,
            },
            "2": {
                "name": "Alliance 2",
                "members": [],
                "leader_id": 0,
                "advisor_ids": [],
                "reserve_power": 0,
                "log": [],
                "officer_activity": {},
                "last_leader_action_ts": 0.0,
            },
        }
        await guild_conf.alliances.set(defaults)

    async def _build_fresh_territory_state(self, guild: discord.Guild) -> Dict[str, Dict[str, object]]:
        await self._ensure_alliance_state(guild)
        values = await self.config.guild(guild).values()
        neutral_power = int(values.get("neutral_defense_power", 400))
        starter_power = int(values.get("starter_defense_power", 250))
        capital_power = int(values.get("capital_defense_power", 600))
        map_def = build_alpha_map()
        territories = {}
        for key, territory in map_def["territories"].items():
            owner = int(territory.starter_owner)
            if owner == 0:
                defense = neutral_power
            elif territory.is_capital:
                defense = capital_power
            else:
                defense = starter_power
            territories[key] = {
                "owner": owner,
                "defense_power": defense,
                "protected_until": 0.0,
                "last_captured_ts": 0.0,
            }
        return territories

    async def _reset_season_state(self, guild: discord.Guild):
        guild_conf = self.config.guild(guild)
        await self._ensure_alliance_state(guild)
        alliances = await guild_conf.alliances()
        for key in ("1", "2"):
            alliances[key]["reserve_power"] = 0
            alliances[key]["log"] = []
            alliances[key]["officer_activity"] = {}
            alliances[key]["last_leader_action_ts"] = 0.0
        await guild_conf.alliances.set(alliances)

        players = await guild_conf.players()
        for player in players.values():
            player["contribution"] = 0
            player["best_scores"] = {game: 0 for game in MINIGAMES}
            player["alliance"] = 0
        await guild_conf.players.set(players)

        await guild_conf.territories.set(await self._build_fresh_territory_state(guild))
        await guild_conf.battles.set({})
        await guild_conf.minigame_sessions.set({})
        await guild_conf.prep_end_ts.set(0.0)
        await guild_conf.war_start_ts.set(0.0)
        await guild_conf.war_end_ts.set(0.0)
        await guild_conf.capital_unlock_ts.set(0.0)

    async def _append_alliance_log(self, guild: discord.Guild, alliance_id: int, text: str):
        guild_conf = self.config.guild(guild)
        alliances = await guild_conf.alliances()
        key = str(alliance_id)
        if key not in alliances:
            return
        entry = {"ts": self._now_ts(), "text": text}
        alliances[key].setdefault("log", []).append(entry)
        if len(alliances[key]["log"]) > 150:
            alliances[key]["log"] = alliances[key]["log"][-150:]
        await guild_conf.alliances.set(alliances)

    async def _note_officer_activity(self, guild: discord.Guild, alliance_id: int, member_id: int):
        guild_conf = self.config.guild(guild)
        alliances = await guild_conf.alliances()
        key = str(alliance_id)
        if key not in alliances:
            return

        now_ts = self._now_ts()
        alliances[key].setdefault("officer_activity", {})[str(member_id)] = now_ts
        if int(alliances[key].get("leader_id", 0)) == int(member_id):
            alliances[key]["last_leader_action_ts"] = now_ts
        await guild_conf.alliances.set(alliances)

    async def _maybe_promote_inactive_leader(self, guild: discord.Guild):
        guild_conf = self.config.guild(guild)
        timings = await guild_conf.timings()
        threshold_secs = float(timings.get("leader_inactive_hours", 24.0)) * 3600.0
        if threshold_secs <= 0:
            return

        alliances = await guild_conf.alliances()
        now_ts = self._now_ts()

        changed = False
        for key in ("1", "2"):
            alliance = alliances.get(key)
            if not alliance:
                continue
            leader_id = int(alliance.get("leader_id", 0))
            if leader_id <= 0:
                continue
            last_action = float(alliance.get("last_leader_action_ts", 0.0))
            if not last_action:
                last_action = now_ts
                alliance["last_leader_action_ts"] = last_action
            if now_ts - last_action < threshold_secs:
                continue

            advisors = [int(x) for x in alliance.get("advisor_ids", []) if int(x) > 0]
            activity = alliance.get("officer_activity", {})
            if not advisors:
                continue

            best_advisor = sorted(
                advisors,
                key=lambda advisor_id: float(activity.get(str(advisor_id), 0.0)),
                reverse=True,
            )[0]
            if best_advisor <= 0:
                continue

            alliance["leader_id"] = best_advisor
            alliance["advisor_ids"] = [aid for aid in advisors if aid != best_advisor]
            alliance.setdefault("log", []).append(
                {
                    "ts": now_ts,
                    "text": f"Auto-promotion: <@{best_advisor}> became leader due to inactivity.",
                }
            )
            changed = True

        if changed:
            await guild_conf.alliances.set(alliances)
            await self._send_feed_message(guild, "Leader inactivity promotion executed.")

    async def _ensure_player_entry(self, guild: discord.Guild, user_id: int) -> Dict[str, object]:
        guild_conf = self.config.guild(guild)
        players = await guild_conf.players()
        key = str(user_id)
        if key not in players:
            players[key] = {
                "alliance": 0,
                "contribution": 0,
                "best_scores": {game: 0 for game in MINIGAMES},
            }
            await guild_conf.players.set(players)
        return players[key]

    async def _award_minigame_score(self, guild: discord.Guild, user_id: int, game: str, score: int) -> Tuple[int, int, int]:
        guild_conf = self.config.guild(guild)
        players = await guild_conf.players()
        key = str(user_id)
        if key not in players:
            players[key] = {
                "alliance": 0,
                "contribution": 0,
                "best_scores": {minigame: 0 for minigame in MINIGAMES},
            }
        player = players[key]
        old_best = int(player.setdefault("best_scores", {}).get(game, 0))
        new_best, gain = compute_power_gain(old_best, score)
        player["best_scores"][game] = new_best
        player["contribution"] = int(player.get("contribution", 0)) + gain
        alliance_id = int(player.get("alliance", 0))
        await guild_conf.players.set(players)

        if gain > 0 and alliance_id in (1, 2):
            alliances = await guild_conf.alliances()
            alliance = alliances[str(alliance_id)]
            alliance["reserve_power"] = int(alliance.get("reserve_power", 0)) + gain
            await guild_conf.alliances.set(alliances)
            await self._append_alliance_log(
                guild,
                alliance_id,
                f"Minigame contribution by <@{user_id}>: +{gain} reserve power from {game}.",
            )
        return old_best, new_best, gain

    async def _assign_alliances_and_officers(self, guild: discord.Guild) -> Tuple[int, int]:
        guild_conf = self.config.guild(guild)
        members = await guild_conf.registered_players()
        members = [int(member_id) for member_id in members]
        random.shuffle(members)

        left = members[::2]
        right = members[1::2]

        alliances = await guild_conf.alliances()
        for key in ("1", "2"):
            alliances[key]["members"] = []
            alliances[key]["leader_id"] = 0
            alliances[key]["advisor_ids"] = []
            alliances[key]["reserve_power"] = 0
            alliances[key]["log"] = []
            alliances[key]["officer_activity"] = {}
            alliances[key]["last_leader_action_ts"] = self._now_ts()

        alliances["1"]["members"] = left
        alliances["2"]["members"] = right

        for alliance_key in ("1", "2"):
            pool = list(alliances[alliance_key]["members"])
            random.shuffle(pool)
            if pool:
                alliances[alliance_key]["leader_id"] = pool[0]
            advisors = pool[1:3]
            alliances[alliance_key]["advisor_ids"] = advisors

        players = await guild_conf.players()
        for user_id in left:
            entry = players.setdefault(
                str(user_id),
                {"alliance": 0, "contribution": 0, "best_scores": {game: 0 for game in MINIGAMES}},
            )
            entry["alliance"] = 1
            entry.setdefault("best_scores", {game: 0 for game in MINIGAMES})
        for user_id in right:
            entry = players.setdefault(
                str(user_id),
                {"alliance": 0, "contribution": 0, "best_scores": {game: 0 for game in MINIGAMES}},
            )
            entry["alliance"] = 2
            entry.setdefault("best_scores", {game: 0 for game in MINIGAMES})

        await guild_conf.players.set(players)
        await guild_conf.alliances.set(alliances)
        return len(left), len(right)

    async def _send_feed_message(self, guild: discord.Guild, text: str):
        channel_id = await self.config.guild(guild).feed_channel_id()
        if not channel_id:
            return
        channel = guild.get_channel(int(channel_id))
        if channel is None:
            return
        try:
            await channel.send(text)
        except discord.HTTPException:
            LOG.warning("Failed to send feed message in guild %s", guild.id)

    async def _render_map_file(self, guild: discord.Guild) -> Optional[discord.File]:
        guild_conf = self.config.guild(guild)
        map_def = build_alpha_map()
        territories = await guild_conf.territories()
        battles = await guild_conf.battles()
        try:
            image_bytes = render_map_png(map_def, territories, battles)
        except Exception:
            LOG.exception("Map rendering failed for guild %s", guild.id)
            return None
        return discord.File(BytesIO(image_bytes), filename="territorywar_map.png")

    async def _update_map_message(self, guild: discord.Guild, force: bool = False):
        guild_conf = self.config.guild(guild)
        map_channel_id = await guild_conf.map_channel_id()
        if not map_channel_id:
            return

        now_ts = self._now_ts()
        timings = await guild_conf.timings()
        interval_secs = float(timings.get("map_update_minutes", 5.0)) * 60.0
        last_ts = float(await guild_conf.last_map_update_ts())
        if not force and interval_secs > 0 and now_ts - last_ts < interval_secs:
            return

        channel = guild.get_channel(int(map_channel_id))
        if channel is None:
            return

        map_message = await guild_conf.map_message()
        msg_channel_id = int(map_message.get("channel_id", 0))
        msg_id = int(map_message.get("message_id", 0))

        file = await self._render_map_file(guild)
        if file is None:
            return

        content = f"Territory War map update - {self._fmt_ts(now_ts)}"

        message_obj = None
        if msg_channel_id and msg_id and msg_channel_id == channel.id:
            try:
                message_obj = await channel.fetch_message(msg_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message_obj = None

        try:
            if message_obj:
                await message_obj.edit(content=content, attachments=[file])
            else:
                message_obj = await channel.send(content=content, file=file)
                await guild_conf.map_message.set({"channel_id": channel.id, "message_id": message_obj.id})
            await guild_conf.last_map_update_ts.set(now_ts)
        except discord.HTTPException:
            LOG.warning("Failed to update map message in guild %s", guild.id)

    async def _is_officer(self, guild: discord.Guild, user_id: int) -> Tuple[bool, int, str]:
        alliances = await self.config.guild(guild).alliances()
        for alliance_id in (1, 2):
            alliance = alliances.get(str(alliance_id), {})
            if int(alliance.get("leader_id", 0)) == int(user_id):
                return True, alliance_id, "leader"
            advisors = [int(a) for a in alliance.get("advisor_ids", [])]
            if int(user_id) in advisors:
                return True, alliance_id, "advisor"
        return False, 0, ""

    async def _player_alliance_id(self, guild: discord.Guild, user_id: int) -> int:
        players = await self.config.guild(guild).players()
        return int(players.get(str(user_id), {}).get("alliance", 0))

    async def _start_war_if_ready(self, guild: discord.Guild):
        guild_conf = self.config.guild(guild)
        state = await guild_conf.state()
        if state != "prep":
            return

        now_ts = self._now_ts()
        prep_end_ts = float(await guild_conf.prep_end_ts())
        if now_ts < prep_end_ts:
            return

        timings = await guild_conf.timings()
        war_days = float(timings.get("war_days", 7.0))
        capital_unlock_day = float(timings.get("capital_unlock_day", 5.0))

        war_start = now_ts
        war_end = war_start + max(0.1, war_days) * 86400.0
        capital_unlock_ts = war_start + max(0.0, capital_unlock_day) * 86400.0

        await guild_conf.state.set("war")
        await guild_conf.war_start_ts.set(war_start)
        await guild_conf.war_end_ts.set(war_end)
        await guild_conf.capital_unlock_ts.set(capital_unlock_ts)
        await self._send_feed_message(guild, "Preparation ended. The territory war has begun.")
        await self._update_map_message(guild, force=True)

    async def _finish_war(self, guild: discord.Guild, winner_alliance: int, reason: str):
        guild_conf = self.config.guild(guild)
        alliances = await guild_conf.alliances()
        territories = await guild_conf.territories()
        season_id = int(await guild_conf.season_id())

        owned_1 = sum(1 for t in territories.values() if int(t.get("owner", 0)) == 1)
        owned_2 = sum(1 for t in territories.values() if int(t.get("owner", 0)) == 2)

        history = await guild_conf.season_history()
        history.append(
            {
                "season_id": season_id,
                "winner": winner_alliance,
                "ended_ts": self._now_ts(),
                "reason": reason,
                "owned_1": owned_1,
                "owned_2": owned_2,
            }
        )
        if len(history) > 50:
            history = history[-50:]

        await guild_conf.season_history.set(history)
        await self._send_feed_message(
            guild,
            f"Season {season_id} finished. Winner: Alliance {winner_alliance}. Reason: {reason}",
        )

        await guild_conf.state.set("idle")
        await self._reset_season_state(guild)
        await self._update_map_message(guild, force=True)

    async def _check_war_end(self, guild: discord.Guild):
        guild_conf = self.config.guild(guild)
        if await guild_conf.state() != "war":
            return
        now_ts = self._now_ts()

        map_def = build_alpha_map()
        territories = await guild_conf.territories()
        for key, territory in map_def["territories"].items():
            if not territory.is_capital:
                continue
            owner = int(territories.get(key, {}).get("owner", 0))
            if key == "a_cap" and owner == 2:
                await self._finish_war(guild, 2, "Captured enemy capital")
                return
            if key == "b_cap" and owner == 1:
                await self._finish_war(guild, 1, "Captured enemy capital")
                return

        war_end = float(await guild_conf.war_end_ts())
        if war_end and now_ts >= war_end:
            owned_1 = sum(1 for t in territories.values() if int(t.get("owner", 0)) == 1)
            owned_2 = sum(1 for t in territories.values() if int(t.get("owner", 0)) == 2)
            winner = 1 if owned_1 > owned_2 else 2
            await self._finish_war(guild, winner, "Time limit reached")

    async def _process_battle_ticks(self, guild: discord.Guild):
        guild_conf = self.config.guild(guild)
        if await guild_conf.state() != "war":
            return

        battles = await guild_conf.battles()
        if not battles:
            return

        timings = await guild_conf.timings()
        values = await guild_conf.values()
        interval_secs = max(60.0, float(timings.get("battle_interval_minutes", 30.0)) * 60.0)
        damage = int(values.get("battle_tick_damage", 100))
        protection_secs = max(0.0, float(values.get("capture_protection_minutes", 60)) * 60.0)
        now_ts = self._now_ts()

        territories = await guild_conf.territories()
        changed = False
        for battle_id, battle in list(battles.items()):
            if battle.get("status") != "active":
                continue
            next_tick = float(battle.get("next_tick_ts", 0.0))
            if next_tick <= 0:
                next_tick = now_ts

            while next_tick <= now_ts and battle.get("status") == "active":
                battle = process_battle_tick(battle, damage, now_ts)
                next_tick += interval_secs
            battle["next_tick_ts"] = next_tick

            if battle.get("status") == "resolved":
                source = battle.get("source")
                target = battle.get("target")
                winner_side = battle.get("winner_side")
                survivor = int(battle.get("surviving_power", 0))
                attacker_alliance = int(battle.get("attacker_alliance", 0))
                defender_alliance = int(battle.get("defender_alliance", 0))

                if winner_side == "attacker":
                    territories[target]["owner"] = attacker_alliance
                    territories[target]["defense_power"] = survivor
                    territories[target]["protected_until"] = now_ts + protection_secs
                    territories[target]["last_captured_ts"] = now_ts
                    await self._append_alliance_log(
                        guild,
                        attacker_alliance,
                        f"Battle {battle_id} won: captured {target} with {survivor} power remaining.",
                    )
                else:
                    if defender_alliance in (1, 2):
                        territories[target]["owner"] = defender_alliance
                    territories[target]["defense_power"] = survivor
                    await self._append_alliance_log(
                        guild,
                        max(defender_alliance, attacker_alliance),
                        f"Battle {battle_id} defended at {target} with {survivor} power remaining.",
                    )
                changed = True
            battles[battle_id] = battle

        if changed:
            await guild_conf.territories.set(territories)
            await self._update_map_message(guild, force=True)

        await guild_conf.battles.set(battles)

    @tasks.loop(minutes=1)
    async def scheduler(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            lock = self._lock_for(guild.id)
            if lock.locked():
                continue
            async with lock:
                try:
                    await self._start_war_if_ready(guild)
                    await self._process_battle_ticks(guild)
                    await self._maybe_promote_inactive_leader(guild)
                    await self._check_war_end(guild)
                    await self._update_map_message(guild, force=False)
                except Exception:
                    LOG.exception("Scheduler error for guild %s", guild.id)

    @scheduler.before_loop
    async def _before_scheduler(self):
        await self.bot.wait_until_ready()

    async def _require_state(self, ctx, expected: str) -> bool:
        state = await self.config.guild(ctx.guild).state()
        if state != expected:
            await ctx.send(f"This command requires state `{expected}`. Current state: `{state}`.")
            return False
        return True

    async def _show_status(self, ctx):
        guild_conf = self.config.guild(ctx.guild)
        await self._ensure_alliance_state(ctx.guild)
        viewer_alliance = await self._player_alliance_id(ctx.guild, ctx.author.id)
        can_view_all_reserves = ctx.author.guild_permissions.manage_guild

        state = await guild_conf.state()
        season_id = await guild_conf.season_id()
        reg_open = await guild_conf.registration_open()
        registered = await guild_conf.registered_players()
        prep_end = await guild_conf.prep_end_ts()
        war_end = await guild_conf.war_end_ts()
        cap_unlock = await guild_conf.capital_unlock_ts()

        alliances = await guild_conf.alliances()
        territories = await guild_conf.territories()
        owned_1 = sum(1 for t in territories.values() if int(t.get("owner", 0)) == 1)
        owned_2 = sum(1 for t in territories.values() if int(t.get("owner", 0)) == 2)

        embed = discord.Embed(title="Territory War Status", color=discord.Color.blurple())
        embed.add_field(name="State", value=state, inline=True)
        embed.add_field(name="Season", value=str(season_id), inline=True)
        embed.add_field(name="Registration", value="open" if reg_open else "closed", inline=True)
        embed.add_field(name="Registered players", value=str(len(registered)), inline=True)
        embed.add_field(name="Prep ends", value=self._fmt_ts(prep_end), inline=True)
        embed.add_field(name="War ends", value=self._fmt_ts(war_end), inline=True)
        embed.add_field(name="Capital unlock", value=self._fmt_ts(cap_unlock), inline=True)
        embed.add_field(name="Alliance 1 territories", value=str(owned_1), inline=True)
        embed.add_field(name="Alliance 2 territories", value=str(owned_2), inline=True)

        for aid in ("1", "2"):
            alliance = alliances.get(aid, {})
            leader = int(alliance.get("leader_id", 0))
            advisors = ", ".join(f"<@{int(x)}>" for x in alliance.get("advisor_ids", [])) or "none"
            reserve = int(alliance.get("reserve_power", 0))
            if can_view_all_reserves or int(aid) == int(viewer_alliance):
                reserve_text = humanize_number(reserve)
            else:
                reserve_text = "hidden"
            embed.add_field(
                name=f"Alliance {aid}",
                value=(
                    f"Leader: {('<@' + str(leader) + '>') if leader else 'none'}\n"
                    f"Advisors: {advisors}\n"
                    f"Reserve: {reserve_text}"
                ),
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.group(name="war")
    @commands.guild_only()
    async def war_group(self, ctx):
        """Territory war commands."""
        if ctx.invoked_subcommand is None:
            await self._show_status(ctx)

    @war_group.command(name="status")
    async def war_status(self, ctx):
        """Show current season status."""
        await self._show_status(ctx)

    @war_group.command(name="join")
    async def war_join(self, ctx):
        """Join open registration for the next season."""
        guild_conf = self.config.guild(ctx.guild)
        if not await guild_conf.registration_open():
            await ctx.send("Registration is not currently open.")
            return

        user_id = ctx.author.id
        async with guild_conf.registered_players() as registered:
            if user_id in registered:
                await ctx.send("You are already registered.")
                return
            registered.append(user_id)

        await self._ensure_player_entry(ctx.guild, user_id)
        await ctx.send("You joined registration.")

    @war_group.command(name="leave")
    async def war_leave(self, ctx):
        """Leave open registration."""
        guild_conf = self.config.guild(ctx.guild)
        if not await guild_conf.registration_open():
            await ctx.send("Registration is not currently open.")
            return

        user_id = ctx.author.id
        async with guild_conf.registered_players() as registered:
            if user_id not in registered:
                await ctx.send("You are not registered.")
                return
            registered.remove(user_id)
        await ctx.send("You left registration.")

    @war_group.command(name="map")
    async def war_map(self, ctx):
        """Render the current territory map."""
        file = await self._render_map_file(ctx.guild)
        if file is None:
            await ctx.send("Map rendering failed. Check logs and dependencies.")
            return
        await ctx.send(file=file)

    @war_group.command(name="profile")
    async def war_profile(self, ctx, member: Optional[discord.Member] = None):
        """Show war profile and minigame personal bests."""
        member = member or ctx.author
        players = await self.config.guild(ctx.guild).players()
        entry = players.get(str(member.id))
        if not entry:
            await ctx.send("No war profile found for that member yet.")
            return

        alliance_id = int(entry.get("alliance", 0))
        contribution = int(entry.get("contribution", 0))
        best = entry.get("best_scores", {})

        embed = discord.Embed(title=f"War Profile: {member.display_name}", color=discord.Color.green())
        embed.add_field(name="Alliance", value=str(alliance_id or "Unassigned"), inline=True)
        embed.add_field(name="Contribution", value=humanize_number(contribution), inline=True)
        lines = [f"{game}: {int(best.get(game, 0))}" for game in MINIGAMES]
        embed.add_field(name="Personal best scores", value="\n".join(lines), inline=False)
        await ctx.send(embed=embed)

    @war_group.group(name="play")
    async def war_play_group(self, ctx):
        """Play always-available minigames."""
        if ctx.invoked_subcommand is None:
            await ctx.send("Use: `[p]war play codebreaker start|guess` or `[p]war play memory start|answer`.")

    async def _get_active_session(self, guild: discord.Guild, user_id: int) -> Optional[Dict[str, object]]:
        sessions = await self.config.guild(guild).minigame_sessions()
        session = sessions.get(str(user_id))
        if not session:
            return None
        if float(session.get("expires_at", 0.0)) <= self._now_ts():
            async with self.config.guild(guild).minigame_sessions() as mutable:
                mutable.pop(str(user_id), None)
            return None
        return session

    @war_play_group.group(name="codebreaker")
    async def war_play_codebreaker(self, ctx):
        """Codebreaker minigame."""
        if ctx.invoked_subcommand is None:
            await ctx.send("Use: `[p]war play codebreaker start` then `[p]war play codebreaker guess <4 unique digits>`." )

    @war_play_codebreaker.command(name="start")
    async def war_play_codebreaker_start(self, ctx):
        """Start a codebreaker session."""
        if await self._get_active_session(ctx.guild, ctx.author.id):
            await ctx.send("You already have an active minigame session. Finish or wait for timeout.")
            return

        timings = await self.config.guild(ctx.guild).timings()
        timeout_secs = max(60.0, float(timings.get("session_timeout_minutes", 10.0)) * 60.0)
        session = {
            "game": "codebreaker",
            "secret": generate_codebreaker_secret(),
            "attempts_used": 0,
            "max_attempts": 8,
            "started_ts": self._now_ts(),
            "expires_at": self._now_ts() + timeout_secs,
        }

        async with self.config.guild(ctx.guild).minigame_sessions() as sessions:
            sessions[str(ctx.author.id)] = session

        await ctx.send(
            "Codebreaker started. Guess 4 unique digits with `[p]war play codebreaker guess <digits>`. "
            "You have 8 attempts."
        )

    @war_play_codebreaker.command(name="guess")
    async def war_play_codebreaker_guess(self, ctx, guess: str):
        """Submit a codebreaker guess."""
        session = await self._get_active_session(ctx.guild, ctx.author.id)
        if not session or session.get("game") != "codebreaker":
            await ctx.send("No active codebreaker session. Start one first.")
            return

        if not validate_codebreaker_guess(guess):
            await ctx.send("Guess must be 4 unique digits, like `5831`.")
            return

        secret = session["secret"]
        max_attempts = int(session.get("max_attempts", 8))
        attempts_used = int(session.get("attempts_used", 0)) + 1
        solved = guess == secret

        async with self.config.guild(ctx.guild).minigame_sessions() as sessions:
            current = sessions.get(str(ctx.author.id), session)
            current["attempts_used"] = attempts_used
            sessions[str(ctx.author.id)] = current

            if solved or attempts_used >= max_attempts:
                sessions.pop(str(ctx.author.id), None)

        feedback = codebreaker_feedback(secret, guess)
        if solved:
            score = codebreaker_score(max_attempts, attempts_used, True)
            old_best, new_best, gain = await self._award_minigame_score(
                ctx.guild, ctx.author.id, "codebreaker", score
            )
            await ctx.send(
                f"Solved in {attempts_used}/{max_attempts} attempts. Score: {score}. "
                f"Best: {old_best} -> {new_best}. Alliance reserve gain: +{gain}."
            )
            await self._update_map_message(ctx.guild, force=True)
            return

        if attempts_used >= max_attempts:
            score = 0
            old_best, new_best, gain = await self._award_minigame_score(
                ctx.guild, ctx.author.id, "codebreaker", score
            )
            await ctx.send(
                f"No attempts left. The code was `{secret}`. Score: {score}. "
                f"Best: {old_best} -> {new_best}. Alliance reserve gain: +{gain}."
            )
            return

        await ctx.send(
            f"Exact: {feedback['exact']} | Partial: {feedback['partial']} | "
            f"Attempts left: {max_attempts - attempts_used}."
        )

    @war_play_group.group(name="memory")
    async def war_play_memory(self, ctx):
        """Memory sequence minigame."""
        if ctx.invoked_subcommand is None:
            await ctx.send("Use: `[p]war play memory start` then `[p]war play memory answer <sequence>`." )

    @war_play_memory.command(name="start")
    async def war_play_memory_start(self, ctx):
        """Start a memory sequence session."""
        if await self._get_active_session(ctx.guild, ctx.author.id):
            await ctx.send("You already have an active minigame session. Finish or wait for timeout.")
            return

        timings = await self.config.guild(ctx.guild).timings()
        timeout_secs = max(60.0, float(timings.get("session_timeout_minutes", 10.0)) * 60.0)
        sequence = generate_memory_sequence(length=7)
        session = {
            "game": "memory",
            "sequence": sequence,
            "started_ts": self._now_ts(),
            "expires_at": self._now_ts() + timeout_secs,
        }
        async with self.config.guild(ctx.guild).minigame_sessions() as sessions:
            sessions[str(ctx.author.id)] = session

        await ctx.send(
            "Memorize this and reply with `[p]war play memory answer <sequence>`:\n"
            f"`{sequence}`\n"
            "Only positional matches count toward score."
        )

    @war_play_memory.command(name="answer")
    async def war_play_memory_answer(self, ctx, *, answer: str):
        """Submit your memory sequence answer."""
        session = await self._get_active_session(ctx.guild, ctx.author.id)
        if not session or session.get("game") != "memory":
            await ctx.send("No active memory session. Start one first.")
            return

        sequence = str(session.get("sequence", ""))
        score = memory_score(sequence, answer)

        async with self.config.guild(ctx.guild).minigame_sessions() as sessions:
            sessions.pop(str(ctx.author.id), None)

        old_best, new_best, gain = await self._award_minigame_score(ctx.guild, ctx.author.id, "memory", score)
        await ctx.send(
            f"Memory score: {score}. Best: {old_best} -> {new_best}. "
            f"Alliance reserve gain: +{gain}."
        )
        if gain > 0:
            await self._update_map_message(ctx.guild, force=True)

    @war_group.command(name="leaderboard")
    async def war_leaderboard(self, ctx):
        """Show contribution leaderboard."""
        players = await self.config.guild(ctx.guild).players()
        rows: List[Tuple[int, int]] = []
        for user_key, data in players.items():
            rows.append((int(user_key), int(data.get("contribution", 0))))
        rows.sort(key=lambda item: item[1], reverse=True)
        rows = rows[:20]
        if not rows:
            await ctx.send("No contributions yet.")
            return

        lines = [f"{idx}. <@{user_id}> - {humanize_number(score)}" for idx, (user_id, score) in enumerate(rows, 1)]
        await ctx.send("Contribution leaderboard:\n" + "\n".join(lines))

    @war_group.command(name="battles")
    async def war_battles(self, ctx):
        """Show active battles."""
        battles = await self.config.guild(ctx.guild).battles()
        lines = battle_public_summary_lines(battles)
        if not lines:
            await ctx.send("No active battles.")
            return

        for battle_id, battle in battles.items():
            if battle.get("status") != "active":
                continue
            lines.append(f"{battle_id} next tick: {self._fmt_ts(float(battle.get('next_tick_ts', 0.0)))}")
            reinforcements = battle.get("reinforcements", [])[-5:]
            if reinforcements:
                for entry in reinforcements:
                    lines.append(
                        f"  +{int(entry.get('amount', 0))} by <@{int(entry.get('actor_id', 0))}> "
                        f"to A{int(entry.get('alliance_id', 0))} at {self._fmt_ts(float(entry.get('ts', 0.0)))}"
                    )

        for page in pagify("\n".join(lines), delims=["\n"], page_length=1800):
            await ctx.send(page)

    async def _validate_officer_and_state(self, ctx) -> Tuple[bool, int]:
        is_officer, alliance_id, _role = await self._is_officer(ctx.guild, ctx.author.id)
        if not is_officer:
            await ctx.send("Only alliance leaders or advisors can use this command.")
            return False, 0
        state = await self.config.guild(ctx.guild).state()
        if state not in ("prep", "war"):
            await ctx.send("War actions are only allowed during prep or war.")
            return False, 0
        return True, alliance_id

    @war_group.group(name="officer")
    async def war_officer_group(self, ctx):
        """Officer war actions."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @war_officer_group.command(name="allocate")
    async def war_officer_allocate(self, ctx, territory: str, amount: int):
        """Allocate alliance reserve power to a friendly territory."""
        ok, alliance_id = await self._validate_officer_and_state(ctx)
        if not ok:
            return

        territory = normalize_territory_key(territory)
        if amount <= 0:
            await ctx.send("Amount must be positive.")
            return

        guild_conf = self.config.guild(ctx.guild)
        alliances = await guild_conf.alliances()
        territories = await guild_conf.territories()

        if territory not in territories:
            await ctx.send("Unknown territory key.")
            return
        if int(territories[territory].get("owner", 0)) != alliance_id:
            await ctx.send("You can only allocate to friendly territories.")
            return

        reserve = int(alliances[str(alliance_id)].get("reserve_power", 0))
        if reserve < amount:
            await ctx.send(f"Not enough reserve. Available: {reserve}.")
            return

        alliances[str(alliance_id)]["reserve_power"] = reserve - amount
        territories[territory]["defense_power"] = int(territories[territory].get("defense_power", 0)) + amount

        await guild_conf.alliances.set(alliances)
        await guild_conf.territories.set(territories)
        await self._note_officer_activity(ctx.guild, alliance_id, ctx.author.id)
        await self._append_alliance_log(
            ctx.guild, alliance_id, f"<@{ctx.author.id}> allocated {amount} to {territory}."
        )
        await self._update_map_message(ctx.guild, force=True)
        await ctx.send(f"Allocated {amount} power to {territory}.")

    @war_officer_group.command(name="withdraw")
    async def war_officer_withdraw(self, ctx, territory: str, amount: int):
        """Withdraw power from a peaceful friendly territory to alliance reserve."""
        ok, alliance_id = await self._validate_officer_and_state(ctx)
        if not ok:
            return

        territory = normalize_territory_key(territory)
        if amount <= 0:
            await ctx.send("Amount must be positive.")
            return

        guild_conf = self.config.guild(ctx.guild)
        battles = await guild_conf.battles()
        if territory_in_active_battle(battles, territory):
            await ctx.send("Cannot withdraw from a territory in an active battle.")
            return

        alliances = await guild_conf.alliances()
        territories = await guild_conf.territories()
        if territory not in territories:
            await ctx.send("Unknown territory key.")
            return
        if int(territories[territory].get("owner", 0)) != alliance_id:
            await ctx.send("You can only withdraw from friendly territories.")
            return

        current_defense = int(territories[territory].get("defense_power", 0))
        if current_defense < amount:
            await ctx.send(f"Not enough territory defense power. Available: {current_defense}.")
            return

        territories[territory]["defense_power"] = current_defense - amount
        alliances[str(alliance_id)]["reserve_power"] = int(alliances[str(alliance_id)].get("reserve_power", 0)) + amount
        await guild_conf.territories.set(territories)
        await guild_conf.alliances.set(alliances)
        await self._note_officer_activity(ctx.guild, alliance_id, ctx.author.id)
        await self._append_alliance_log(
            ctx.guild, alliance_id, f"<@{ctx.author.id}> withdrew {amount} from {territory}."
        )
        await self._update_map_message(ctx.guild, force=True)
        await ctx.send(f"Withdrew {amount} power from {territory}.")

    @war_officer_group.command(name="attack")
    async def war_officer_attack(self, ctx, source: str, target: str, amount: int):
        """Start an attack from a connected territory."""
        ok, alliance_id = await self._validate_officer_and_state(ctx)
        if not ok:
            return

        guild_conf = self.config.guild(ctx.guild)
        if await guild_conf.state() != "war":
            await ctx.send("Attacks are only allowed during war.")
            return

        source = normalize_territory_key(source)
        target = normalize_territory_key(target)
        if amount <= 0:
            await ctx.send("Amount must be positive.")
            return

        map_def = build_alpha_map()
        if source not in map_def["territories"] or target not in map_def["territories"]:
            await ctx.send("Invalid territory key.")
            return
        if not is_connected(map_def, source, target):
            await ctx.send("Those territories are not connected.")
            return

        territories = await guild_conf.territories()
        if int(territories[source].get("owner", 0)) != alliance_id:
            await ctx.send("Source territory must be friendly.")
            return
        if int(territories[target].get("owner", 0)) == alliance_id:
            await ctx.send("Target must be enemy or neutral.")
            return

        cap_unlock = float(await guild_conf.capital_unlock_ts())
        now_ts = self._now_ts()
        target_def = map_def["territories"][target]
        if target_def.is_capital and now_ts < cap_unlock:
            await ctx.send("Capitals are locked until the configured unlock day.")
            return

        protected_until = float(territories[target].get("protected_until", 0.0))
        if protected_until > now_ts:
            await ctx.send("Target is temporarily protected after a recent capture.")
            return

        alliances = await guild_conf.alliances()
        reserve = int(alliances[str(alliance_id)].get("reserve_power", 0))
        if reserve < amount:
            await ctx.send(f"Not enough reserve. Available: {reserve}.")
            return

        battles = await guild_conf.battles()
        values = await guild_conf.values()
        max_offensives = int(values.get("max_offensive_battles", 2))
        if count_active_offensives(battles, alliance_id) >= max_offensives:
            await ctx.send(f"Alliance already has max offensive battles ({max_offensives}).")
            return
        if territory_in_active_battle(battles, target):
            await ctx.send("Target is already involved in an active battle.")
            return

        defender_alliance = int(territories[target].get("owner", 0))
        defender_power = int(territories[target].get("defense_power", 0))
        if defender_alliance == 0:
            defender_power = max(defender_power, int(values.get("neutral_defense_power", 400)))

        timings = await guild_conf.timings()
        interval_secs = max(60.0, float(timings.get("battle_interval_minutes", 30.0)) * 60.0)
        battle_id = f"B{int(now_ts)}{random.randint(100, 999)}"
        battles[battle_id] = {
            "id": battle_id,
            "status": "active",
            "source": source,
            "target": target,
            "attacker_alliance": alliance_id,
            "defender_alliance": defender_alliance,
            "attacker_power": amount,
            "defender_power": defender_power,
            "created_ts": now_ts,
            "next_tick_ts": now_ts + interval_secs,
            "reinforcements": [reinforcement_log_entry(alliance_id, amount, ctx.author.id, now_ts)],
        }

        alliances[str(alliance_id)]["reserve_power"] = reserve - amount
        await guild_conf.battles.set(battles)
        await guild_conf.alliances.set(alliances)

        await self._note_officer_activity(ctx.guild, alliance_id, ctx.author.id)
        await self._append_alliance_log(
            ctx.guild,
            alliance_id,
            f"<@{ctx.author.id}> started attack {battle_id}: {source} -> {target} with {amount}.",
        )
        await self._update_map_message(ctx.guild, force=True)
        await ctx.send(f"Attack started: {battle_id} from {source} to {target}.")

    @war_officer_group.command(name="reinforce")
    async def war_officer_reinforce(self, ctx, battle_id: str, amount: int):
        """Reinforce a side of an active battle from reserve."""
        ok, alliance_id = await self._validate_officer_and_state(ctx)
        if not ok:
            return

        if amount <= 0:
            await ctx.send("Amount must be positive.")
            return

        guild_conf = self.config.guild(ctx.guild)
        battles = await guild_conf.battles()
        battle = battles.get(battle_id)
        if not battle or battle.get("status") != "active":
            await ctx.send("Active battle not found.")
            return

        attacker_alliance = int(battle.get("attacker_alliance", 0))
        defender_alliance = int(battle.get("defender_alliance", 0))
        if alliance_id not in (attacker_alliance, defender_alliance):
            await ctx.send("Your alliance is not part of this battle.")
            return

        alliances = await guild_conf.alliances()
        reserve = int(alliances[str(alliance_id)].get("reserve_power", 0))
        if reserve < amount:
            await ctx.send(f"Not enough reserve. Available: {reserve}.")
            return

        alliances[str(alliance_id)]["reserve_power"] = reserve - amount
        if alliance_id == attacker_alliance:
            battle["attacker_power"] = int(battle.get("attacker_power", 0)) + amount
        else:
            battle["defender_power"] = int(battle.get("defender_power", 0)) + amount

        battle.setdefault("reinforcements", []).append(
            reinforcement_log_entry(alliance_id, amount, ctx.author.id, self._now_ts())
        )
        battles[battle_id] = battle

        await guild_conf.alliances.set(alliances)
        await guild_conf.battles.set(battles)
        await self._note_officer_activity(ctx.guild, alliance_id, ctx.author.id)
        await self._append_alliance_log(
            ctx.guild, alliance_id, f"<@{ctx.author.id}> reinforced {battle_id} with {amount}."
        )
        await self._update_map_message(ctx.guild, force=True)
        await ctx.send(f"Reinforced {battle_id} with {amount} power.")

    @war_group.group(name="admin")
    @commands.admin_or_permissions(manage_guild=True)
    async def war_admin_group(self, ctx):
        """Admin controls for territory war."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @war_admin_group.command(name="setmapchannel")
    async def war_admin_set_map_channel(self, ctx, channel: discord.TextChannel):
        """Set the map channel for the persistent map message."""
        await self.config.guild(ctx.guild).map_channel_id.set(channel.id)
        await ctx.send(f"Map channel set to {channel.mention}.")

    @war_admin_group.command(name="setfeedchannel")
    async def war_admin_set_feed_channel(self, ctx, channel: discord.TextChannel):
        """Set the war feed channel."""
        await self.config.guild(ctx.guild).feed_channel_id.set(channel.id)
        await ctx.send(f"Feed channel set to {channel.mention}.")

    @war_admin_group.command(name="openreg")
    async def war_admin_open_registration(self, ctx):
        """Open player registration."""
        guild_conf = self.config.guild(ctx.guild)
        await self._ensure_alliance_state(ctx.guild)
        await guild_conf.registration_open.set(True)
        await guild_conf.state.set("registration")
        await guild_conf.registered_players.set([])
        await ctx.send("Registration opened. Players can now use `[p]war join`.")

    @war_admin_group.command(name="closereg")
    async def war_admin_close_registration(self, ctx):
        """Close registration, shuffle players, assign officers, and start prep."""
        guild_conf = self.config.guild(ctx.guild)
        if not await guild_conf.registration_open():
            await ctx.send("Registration is not open.")
            return

        registered = await guild_conf.registered_players()
        if len(registered) < 4:
            await ctx.send("At least 4 registered players are required.")
            return

        left_count, right_count = await self._assign_alliances_and_officers(ctx.guild)
        timings = await guild_conf.timings()
        prep_hours = float(timings.get("prep_hours", 6.0))

        await guild_conf.registration_open.set(False)
        await guild_conf.state.set("prep")
        now_ts = self._now_ts()
        await guild_conf.prep_end_ts.set(now_ts + max(0.0, prep_hours) * 3600.0)
        await guild_conf.battles.set({})
        await guild_conf.territories.set(await self._build_fresh_territory_state(ctx.guild))

        season_id = int(await guild_conf.season_id()) + 1
        await guild_conf.season_id.set(season_id)

        await ctx.send(
            f"Registration closed. Alliance split: {left_count} vs {right_count}. "
            f"Preparation started for {prep_hours} hours."
        )
        await self._update_map_message(ctx.guild, force=True)

    @war_admin_group.command(name="start")
    async def war_admin_start(self, ctx):
        """Force-start war immediately (skips remaining prep)."""
        guild_conf = self.config.guild(ctx.guild)
        state = await guild_conf.state()
        if state not in ("prep", "registration", "idle"):
            await ctx.send(f"Cannot start from state `{state}`.")
            return

        if state in ("registration", "idle"):
            registered = await guild_conf.registered_players()
            if len(registered) < 4:
                await ctx.send("Need at least 4 registered players to start.")
                return
            await self._assign_alliances_and_officers(ctx.guild)
            await guild_conf.territories.set(await self._build_fresh_territory_state(ctx.guild))
            season_id = int(await guild_conf.season_id()) + 1
            await guild_conf.season_id.set(season_id)

        await guild_conf.state.set("prep")
        await guild_conf.prep_end_ts.set(self._now_ts())
        await self._start_war_if_ready(ctx.guild)
        await ctx.send("War started.")

    @war_admin_group.command(name="stop")
    async def war_admin_stop(self, ctx):
        """Stop the current season without winner."""
        await self.config.guild(ctx.guild).state.set("idle")
        await self.config.guild(ctx.guild).battles.set({})
        await self.config.guild(ctx.guild).minigame_sessions.set({})
        await ctx.send("Season stopped.")
        await self._update_map_message(ctx.guild, force=True)

    @war_admin_group.command(name="reset")
    async def war_admin_reset(self, ctx):
        """Reset seasonal power, map ownership, and scores."""
        await self._reset_season_state(ctx.guild)
        await self.config.guild(ctx.guild).state.set("idle")
        await self.config.guild(ctx.guild).registration_open.set(False)
        await self.config.guild(ctx.guild).registered_players.set([])
        await ctx.send("Seasonal state reset.")
        await self._update_map_message(ctx.guild, force=True)

    @war_admin_group.command(name="timing")
    async def war_admin_timing(self, ctx, key: str, value: float):
        """Set timing values.

        Keys: prep_hours, war_days, battle_interval_minutes, leader_inactive_hours,
        capital_unlock_day, session_timeout_minutes, map_update_minutes.
        """
        key = str(key).strip()
        allowed = {
            "prep_hours",
            "war_days",
            "battle_interval_minutes",
            "leader_inactive_hours",
            "capital_unlock_day",
            "session_timeout_minutes",
            "map_update_minutes",
        }
        if key not in allowed:
            await ctx.send("Invalid timing key.")
            return
        async with self.config.guild(ctx.guild).timings() as timings:
            timings[key] = float(value)
        await ctx.send(f"Timing `{key}` set to {value}.")

    @war_admin_group.command(name="value")
    async def war_admin_value(self, ctx, key: str, value: int):
        """Set battle/value settings.

        Keys: battle_tick_damage, neutral_defense_power, max_offensive_battles,
        capture_protection_minutes, starter_defense_power, capital_defense_power.
        """
        key = str(key).strip()
        allowed = {
            "battle_tick_damage",
            "neutral_defense_power",
            "max_offensive_battles",
            "capture_protection_minutes",
            "starter_defense_power",
            "capital_defense_power",
        }
        if key not in allowed:
            await ctx.send("Invalid value key.")
            return
        async with self.config.guild(ctx.guild).values() as values:
            values[key] = int(value)
        await ctx.send(f"Value `{key}` set to {value}.")

    @war_admin_group.command(name="setleader")
    async def war_admin_setleader(self, ctx, alliance_id: int, member: discord.Member):
        """Manually assign leader."""
        if alliance_id not in (1, 2):
            await ctx.send("Alliance must be 1 or 2.")
            return
        await self._ensure_alliance_state(ctx.guild)
        async with self.config.guild(ctx.guild).alliances() as alliances:
            alliances[str(alliance_id)]["leader_id"] = member.id
            advisors = [int(x) for x in alliances[str(alliance_id)].get("advisor_ids", [])]
            advisors = [a for a in advisors if a != member.id]
            alliances[str(alliance_id)]["advisor_ids"] = advisors
        await ctx.send(f"Leader for Alliance {alliance_id} set to {member.mention}.")

    @war_admin_group.command(name="setadvisor")
    async def war_admin_setadvisor(self, ctx, alliance_id: int, member: discord.Member):
        """Add or keep member as advisor."""
        if alliance_id not in (1, 2):
            await ctx.send("Alliance must be 1 or 2.")
            return
        await self._ensure_alliance_state(ctx.guild)
        async with self.config.guild(ctx.guild).alliances() as alliances:
            leader_id = int(alliances[str(alliance_id)].get("leader_id", 0))
            if leader_id == member.id:
                await ctx.send("That member is the leader. Use setleader to replace first.")
                return
            advisors = [int(x) for x in alliances[str(alliance_id)].get("advisor_ids", [])]
            if member.id not in advisors:
                advisors.append(member.id)
            alliances[str(alliance_id)]["advisor_ids"] = advisors[:2]
        await ctx.send(f"Advisor for Alliance {alliance_id} set to include {member.mention}.")

    @war_admin_group.command(name="forceresolve")
    async def war_admin_forceresolve(self, ctx, battle_id: str, winner: str):
        """Force-resolve a battle. Winner must be attacker or defender."""
        guild_conf = self.config.guild(ctx.guild)
        battles = await guild_conf.battles()
        battle = battles.get(battle_id)
        if not battle or battle.get("status") != "active":
            await ctx.send("Active battle not found.")
            return

        winner = winner.strip().lower()
        if winner not in ("attacker", "defender"):
            await ctx.send("Winner must be `attacker` or `defender`.")
            return

        battle["status"] = "resolved"
        battle["winner_side"] = winner
        battle["surviving_power"] = int(battle.get("attacker_power" if winner == "attacker" else "defender_power", 0))
        battles[battle_id] = battle
        await guild_conf.battles.set(battles)

        await self._process_battle_ticks(ctx.guild)
        await ctx.send(f"Battle {battle_id} force-resolved as {winner}.")

    @war_admin_group.command(name="regeneratemap")
    async def war_admin_regeneratemap(self, ctx):
        """Regenerate or recreate the persistent map message."""
        await self._update_map_message(ctx.guild, force=True)
        await ctx.send("Map message regeneration attempted.")


async def setup(bot):
    await bot.add_cog(TerritoryWar(bot))
