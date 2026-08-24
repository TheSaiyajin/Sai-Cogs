from __future__ import annotations

import asyncio
import logging
import random
import statistics
import time
from typing import Dict, List, Optional, Sequence, Tuple

import aiohttp
import discord
from redbot.core import Config, bank, commands
from redbot.core.utils.chat_formatting import humanize_number, pagify

from . import cards

log = logging.getLogger("red.sai-cogs.tradingcards")


class TradingCards(commands.Cog):
    """Pokémon trading card collection game using Red economy credits."""

    SCHEMA_VERSION = 1
    API_BASE = "https://api.pokemontcg.io/v2"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=836205104177, force_registration=True)
        self.config.register_guild(
            schema_version=self.SCHEMA_VERSION,
            enabled_sets=dict(cards.DEFAULT_ENABLED_SETS),
            pack_prices=dict(cards.DEFAULT_PACK_PRICES),
            set_cache={},
            active_trades={},
            active_listings={},
            trade_history=[],
            market_history=[],
            locked_cards={},
            max_buy=cards.DEFAULT_MAX_BUY,
            max_open=cards.DEFAULT_MAX_OPEN,
            trade_expiry_minutes=cards.DEFAULT_TRADE_EXPIRY_MINUTES,
            market_tax_percent=cards.DEFAULT_MARKET_TAX_PERCENT,
            id_counters={"card": 0, "trade": 0, "listing": 0},
            server_pack_limit=cards.DEFAULT_SERVER_PACK_LIMIT,
            server_pack_limit_window_hours=cards.DEFAULT_SERVER_PACK_LIMIT_WINDOW_HOURS,
            server_pack_purchases={"window_start": 0, "count": 0},
        )
        self.config.register_member(
            owned_cards={},
            packs={},
            favorites=[],
            pull_stats={"total_pulls": 0, "reverse_pulls": 0, "high_rarity_pulls": 0},
            completed_trade_count=0,
            packs_opened_count=0,
        )
        self._guild_locks: Dict[int, asyncio.Lock] = {}
        self._session: Optional[aiohttp.ClientSession] = None
        self._rng = random.SystemRandom()
        self._expiry_task = self.bot.loop.create_task(self._trade_expiry_loop())

    def cog_unload(self):
        if self._expiry_task and not self._expiry_task.done():
            self._expiry_task.cancel()
        if self._session and not self._session.closed:
            self.bot.loop.create_task(self._session.close())

    async def _trade_expiry_loop(self):
        await self.bot.wait_until_red_ready()
        while True:
            try:
                guild_ids = [int(gid) for gid in (await self.config.all_guilds()).keys()]
                for guild_id in guild_ids:
                    await self._expire_trades_for_guild(guild_id)
            except Exception as exc:
                log.exception("trade expiry loop error")
            await asyncio.sleep(30)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session and not self._session.closed:
            return self._session
        timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_connect=10, sock_read=20)
        self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    def _guild_lock(self, guild_id: int) -> asyncio.Lock:
        lock = self._guild_locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self._guild_locks[guild_id] = lock
        return lock

    async def _get_api_headers(self) -> Dict[str, str]:
        token_data = await self.bot.get_shared_api_tokens("pokemontcg")
        api_key = (
            token_data.get("api_key")
            or token_data.get("key")
            or token_data.get("token")
            or token_data.get("apikey")
            or ""
        )
        headers = {"Accept": "application/json"}
        if api_key:
            headers["X-Api-Key"] = api_key
        return headers

    async def _api_get(self, endpoint: str, params: Optional[Dict] = None) -> Dict:
        url = f"{self.API_BASE}{endpoint}"
        session = await self._get_session()
        headers = await self._get_api_headers()
        async with session.get(url, params=params or {}, headers=headers) as response:
            if response.status == 429:
                retry_after = response.headers.get("Retry-After", "unknown")
                raise RuntimeError(
                    f"Pokémon TCG API rate limited the request. Retry after {retry_after} seconds."
                )
            if response.status >= 500:
                raise RuntimeError("Pokémon TCG API is currently unavailable (server error).")
            if response.status >= 400:
                text = await response.text()
                raise RuntimeError(f"Pokémon TCG API request failed ({response.status}): {text[:200]}")
            return await response.json()

    async def _fetch_set_cards(self, set_id: str) -> List[Dict]:
        all_cards: List[Dict] = []
        page = 1
        while True:
            payload = await self._api_get(
                "/cards",
                {
                    "q": f"set.id:{set_id}",
                    "pageSize": 250,
                    "page": page,
                    "orderBy": "number",
                },
            )
            data = payload.get("data") or []
            all_cards.extend(data)
            count = len(data)
            page_size = int(payload.get("pageSize", 250))
            total_count = int(payload.get("totalCount", 0))
            if count < page_size or len(all_cards) >= total_count:
                break
            page += 1
        return all_cards

    async def _refresh_set_cache_for_guild(self, guild_id: int, set_id: str) -> Tuple[bool, str]:
        set_name = cards.SUPPORTED_SETS.get(set_id, set_id)
        try:
            api_cards = await self._fetch_set_cards(set_id)
        except asyncio.TimeoutError:
            return False, f"{set_name}: API timeout."
        except RuntimeError as exc:
            return False, f"{set_name}: {exc}"
        except aiohttp.ClientError as exc:
            return False, f"{set_name}: network error ({exc})."

        if not api_cards:
            return False, f"{set_name}: no cards returned by API."
        cache_entry = cards.build_set_cache(api_cards, set_id, set_name)
        if not cache_entry["cards"]:
            return False, f"{set_name}: no valid cards were cached."

        async with self.config.guild_from_id(guild_id).set_cache() as set_cache:
            set_cache[set_id] = cache_entry
        return True, f"{set_name}: cached {len(cache_entry['cards'])} cards."

    async def _refresh_all_sets_for_guild(self, guild_id: int) -> List[str]:
        results: List[str] = []
        for set_id in cards.SUPPORTED_SETS:
            ok, msg = await self._refresh_set_cache_for_guild(guild_id, set_id)
            if ok:
                results.append(f"✅ {msg}")
            else:
                results.append(f"⚠️ {msg}")
        return results

    async def _ensure_set_cache(self, guild_id: int, set_id: str) -> bool:
        set_cache = await self.config.guild_from_id(guild_id).set_cache()
        entry = set_cache.get(set_id)
        if entry and entry.get("cards"):
            return True
        ok, _msg = await self._refresh_set_cache_for_guild(guild_id, set_id)
        return ok

    @staticmethod
    def _resolve_set_token(token: str) -> Optional[str]:
        return cards.normalize_set_token(token)

    async def _next_card_instance_id(self, guild_id: int) -> str:
        async with self.config.guild_from_id(guild_id).id_counters() as counters:
            next_value = int(counters.get("card", 0)) + 1
            counters["card"] = next_value
        return cards.new_instance_id(next_value)

    async def _next_trade_id(self, guild_id: int) -> str:
        async with self.config.guild_from_id(guild_id).id_counters() as counters:
            next_value = int(counters.get("trade", 0)) + 1
            counters["trade"] = next_value
        return f"TRD-{next_value:010d}"

    async def _next_listing_id(self, guild_id: int) -> str:
        async with self.config.guild_from_id(guild_id).id_counters() as counters:
            next_value = int(counters.get("listing", 0)) + 1
            counters["listing"] = next_value
        return f"LST-{next_value:010d}"

    async def _find_member_trade(self, guild_id: int, member_id: int) -> Tuple[Optional[str], Optional[Dict]]:
        active_trades = await self.config.guild_from_id(guild_id).active_trades()
        key = str(member_id)
        for trade_id, trade in active_trades.items():
            offers = trade.get("offers", {})
            if key in offers:
                return trade_id, trade
        return None, None

    def _trade_other_member_id(self, trade: Dict, member_id: int) -> Optional[int]:
        offers = trade.get("offers", {})
        for key in offers:
            if int(key) != int(member_id):
                return int(key)
        return None

    async def _expire_trades_for_guild(self, guild_id: int):
        """Acquire the guild lock and expire stale trades.

        Callers that already hold the guild lock MUST use
        ``_expire_trades_for_guild_unlocked`` instead to avoid deadlocking on
        the non-reentrant ``asyncio.Lock``.
        """
        lock = self._guild_lock(guild_id)
        async with lock:
            await self._expire_trades_for_guild_unlocked(guild_id)

    async def _expire_trades_for_guild_unlocked(self, guild_id: int):
        """Expire stale trades. The caller must already hold the guild lock."""
        now = cards.now_ts()
        guild_conf = self.config.guild_from_id(guild_id)
        active_trades = await guild_conf.active_trades()
        if not active_trades:
            return
        locked_cards = await guild_conf.locked_cards()
        changed = False
        for trade_id, trade in list(active_trades.items()):
            if cards.trade_is_expired(trade, now=now):
                self._unlock_trade_cards(locked_cards, trade_id)
                del active_trades[trade_id]
                changed = True
        if changed:
            await guild_conf.active_trades.set(active_trades)
            await guild_conf.locked_cards.set(locked_cards)

    def _unlock_trade_cards(self, locked_cards: Dict, trade_id: str):
        for instance_id, lock in list(locked_cards.items()):
            if str(lock.get("lock_type")) == "trade" and str(lock.get("ref_id")) == str(trade_id):
                locked_cards.pop(instance_id, None)

    def _unlock_listing_card(self, locked_cards: Dict, listing_id: str):
        for instance_id, lock in list(locked_cards.items()):
            if str(lock.get("lock_type")) == "market" and str(lock.get("ref_id")) == str(listing_id):
                locked_cards.pop(instance_id, None)

    async def _cancel_trade(self, guild_id: int, trade_id: str, reason: str):
        guild_conf = self.config.guild_from_id(guild_id)
        active_trades = await guild_conf.active_trades()
        trade = active_trades.get(trade_id)
        if not trade:
            return
        locked_cards = await guild_conf.locked_cards()
        self._unlock_trade_cards(locked_cards, trade_id)
        del active_trades[trade_id]
        await guild_conf.active_trades.set(active_trades)
        await guild_conf.locked_cards.set(locked_cards)

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        member_ids = [int(mid) for mid in (trade.get("offers") or {}).keys()]
        embed = discord.Embed(
            title="Trade Cancelled",
            description=reason,
            color=discord.Color.orange(),
        )
        for member_id in member_ids:
            member = guild.get_member(member_id)
            if member:
                try:
                    await member.send(embed=embed)
                except discord.Forbidden:
                    pass

    @staticmethod
    def _build_trade_embed(trade_id: str, trade: Dict, guild: discord.Guild) -> discord.Embed:
        embed = discord.Embed(
            title=f"Trade {trade_id}",
            description="Both members must confirm the final offer.",
            color=discord.Color.blurple(),
        )
        expires_at = int(trade.get("expires_at", 0))
        embed.add_field(name="Expires", value=f"<t:{expires_at}:R>", inline=False)
        offers = trade.get("offers", {})
        for member_id_str, offer in offers.items():
            member = guild.get_member(int(member_id_str))
            display = member.mention if member else f"`{member_id_str}`"
            card_lines = offer.get("cards", [])
            card_text = "\n".join(f"- `{card_id}`" for card_id in card_lines) if card_lines else "- None"
            credits = int(offer.get("credits", 0))
            confirmed = "✅ Confirmed" if offer.get("confirmed") else "❌ Not confirmed"
            embed.add_field(
                name=f"{display} gives",
                value=f"{card_text}\n- Credits: {humanize_number(credits)}\n- {confirmed}",
                inline=False,
            )
        return embed

    async def _pull_pack_cards(self, guild_id: int, set_id: str) -> Tuple[List[Dict], Dict]:
        set_cache = await self.config.guild_from_id(guild_id).set_cache()
        set_entry = set_cache.get(set_id) or {}
        card_snapshots = set_entry.get("cards") or []
        pools = cards.build_card_pools(card_snapshots)
        if not pools["common"] or not pools["uncommon"] or not pools["reverse_eligible"]:
            raise RuntimeError("Set cache is missing required rarity pools for pack simulation.")
        if not cards.validate_rare_odds_total(cards.RARE_SLOT_ODDS):
            raise RuntimeError("Rare slot odds configuration is invalid (must total 100).")
        if not any(pools["rare_tiers"].get(tier) for tier in cards.RARE_TIER_ORDER):
            raise RuntimeError("Set cache has no rare-or-better cards available.")

        pulled: List[Dict] = []
        common_cards = cards.choose_multiple_cards(self._rng, pools["common"], 6)
        uncommon_cards = cards.choose_multiple_cards(self._rng, pools["uncommon"], 3)
        for card in common_cards + uncommon_cards:
            pulled.append(
                {
                    "api_card_id": card["card_id"],
                    "name": card["name"],
                    "set_id": card["set_id"],
                    "set_name": card["set_name"],
                    "set_number": card["set_number"],
                    "printed_number": card["printed_number"],
                    "rarity": card["rarity"],
                    "small_image": card["small_image"],
                    "large_image": card["large_image"],
                    "api_url": card["api_url"],
                    "artist": card["artist"],
                    "finish": cards.pull_finish_for_rarity(card["rarity"]),
                }
            )

        reverse_card = cards.choose_card_from_pool(self._rng, pools["reverse_eligible"])
        if reverse_card is None:
            raise RuntimeError("No eligible reverse-holo card could be selected.")
        pulled.append(
            {
                "api_card_id": reverse_card["card_id"],
                "name": reverse_card["name"],
                "set_id": reverse_card["set_id"],
                "set_name": reverse_card["set_name"],
                "set_number": reverse_card["set_number"],
                "printed_number": reverse_card["printed_number"],
                "rarity": reverse_card["rarity"],
                "small_image": reverse_card["small_image"],
                "large_image": reverse_card["large_image"],
                "api_url": reverse_card["api_url"],
                "artist": reverse_card["artist"],
                "finish": "Reverse Holo",
            }
        )

        requested_tier = cards.roll_weighted_rare_tier(self._rng)
        selected_tier = cards.nearest_available_tier(requested_tier, pools["rare_tiers"])
        if selected_tier is None:
            raise RuntimeError("No available card was found for the rare slot.")
        rare_card = cards.choose_card_from_pool(self._rng, pools["rare_tiers"][selected_tier])
        if rare_card is None:
            raise RuntimeError("Rare slot card selection failed.")
        rare_finish = cards.pull_finish_for_rarity(rare_card["rarity"])
        if selected_tier != "regular_or_holo" and rare_finish == "Normal":
            rare_finish = "Holo"
        pulled.append(
            {
                "api_card_id": rare_card["card_id"],
                "name": rare_card["name"],
                "set_id": rare_card["set_id"],
                "set_name": rare_card["set_name"],
                "set_number": rare_card["set_number"],
                "printed_number": rare_card["printed_number"],
                "rarity": rare_card["rarity"],
                "small_image": rare_card["small_image"],
                "large_image": rare_card["large_image"],
                "api_url": rare_card["api_url"],
                "artist": rare_card["artist"],
                "finish": rare_finish,
                "rare_tier": selected_tier,
            }
        )
        energy = self._rng.choice(
            ["Grass", "Fire", "Water", "Lightning", "Psychic", "Fighting", "Darkness", "Metal"]
        )
        return pulled, {"energy": f"{energy} {cards.BASIC_ENERGY_LABEL}", "rare_tier": selected_tier}

    async def _member_owned_cards(self, member: discord.Member) -> Dict:
        return await self.config.member(member).owned_cards()

    async def _member_favorites(self, member: discord.Member) -> List[str]:
        return await self.config.member(member).favorites()

    async def _find_card_globally(
        self, guild: discord.Guild, instance_id: str
    ) -> Tuple[Optional[discord.Member], Optional[Dict]]:
        all_members = await self.config.all_members(guild)
        for member_id_str, data in all_members.items():
            card_instance = (data.get("owned_cards") or {}).get(instance_id)
            if card_instance:
                member = guild.get_member(int(member_id_str))
                return member, card_instance
        return None, None

    async def _ensure_schema(self, guild_id: int):
        version = int(await self.config.guild_from_id(guild_id).schema_version())
        if version >= self.SCHEMA_VERSION:
            return
        await self.config.guild_from_id(guild_id).schema_version.set(self.SCHEMA_VERSION)

    @commands.group(name="tcg", aliases=["TCG"], case_insensitive=True, autohelp=False)
    @commands.guild_only()
    async def tcg(self, ctx: commands.Context):
        """Pokémon trading card game commands."""
        await self._ensure_schema(ctx.guild.id)
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @tcg.command(name="sets")
    async def tcg_sets(self, ctx: commands.Context):
        """List all currently supported sets."""
        guild_conf = self.config.guild(ctx.guild)
        enabled_sets = await guild_conf.enabled_sets()
        set_cache = await guild_conf.set_cache()
        embed = discord.Embed(title="Supported TCG Sets", color=discord.Color.blurple())
        for set_id, set_name in cards.SUPPORTED_SETS.items():
            enabled = bool(enabled_sets.get(set_id, True))
            cache_entry = set_cache.get(set_id) or {}
            cached_total = int((cache_entry.get("counts") or {}).get("total", 0))
            embed.add_field(
                name=f"{set_name} (`{set_id}`)",
                value=f"Enabled: {'Yes' if enabled else 'No'}\nCached cards: {cached_total}",
                inline=False,
            )
        await ctx.send(embed=embed)

    @tcg.command(name="shop")
    async def tcg_shop(self, ctx: commands.Context):
        """Show available packs, prices, and artwork."""
        guild_conf = self.config.guild(ctx.guild)
        enabled_sets = await guild_conf.enabled_sets()
        pack_prices = await guild_conf.pack_prices()
        set_cache = await guild_conf.set_cache()
        embeds: List[discord.Embed] = []
        for set_id, set_name in cards.SUPPORTED_SETS.items():
            if not bool(enabled_sets.get(set_id, True)):
                continue
            price = int(pack_prices.get(set_id, cards.DEFAULT_PACK_PRICES[set_id]))
            entry = set_cache.get(set_id) or {}
            embed = discord.Embed(
                title=f"{set_name} (`{set_id}`)",
                description=f"Price: **{humanize_number(price)} credits**",
                color=discord.Color.green(),
            )
            embed.add_field(
                name="Buy",
                value=f"`{ctx.clean_prefix}tcg buy {set_id} [amount]`",
                inline=False,
            )
            artwork_url = str(entry.get("artwork_url", "")).strip()
            if artwork_url:
                embed.set_image(url=artwork_url)
            embeds.append(embed)
        if not embeds:
            await ctx.send("No sets are currently enabled in this server.")
            return
        for embed in embeds:
            await ctx.send(embed=embed)

    @tcg.command(name="set")
    async def tcg_set(self, ctx: commands.Context, set_token: str):
        """Show details for one set."""
        set_id = self._resolve_set_token(set_token)
        if not set_id:
            await ctx.send("Unknown set. Use `[p]tcg sets` to view valid set IDs.")
            return
        guild_conf = self.config.guild(ctx.guild)
        enabled_sets = await guild_conf.enabled_sets()
        pack_prices = await guild_conf.pack_prices()
        cache = await guild_conf.set_cache()
        entry = cache.get(set_id) or {}
        if not entry.get("cards"):
            ensured = await self._ensure_set_cache(ctx.guild.id, set_id)
            if not ensured:
                await ctx.send(
                    "Set data is unavailable right now and could not be refreshed. Try again later."
                )
                return
            cache = await guild_conf.set_cache()
            entry = cache.get(set_id) or {}
        total_cards = int((entry.get("counts") or {}).get("total", 0))
        member_cards = await self._member_owned_cards(ctx.author)
        owned_unique = len(
            {
                c["api_card_id"]
                for c in member_cards.values()
                if str(c.get("set_id")) == set_id and c.get("api_card_id")
            }
        )
        completion = cards.completion_percent(owned_unique, total_cards)
        embed = discord.Embed(
            title=f"{cards.SUPPORTED_SETS[set_id]} (`{set_id}`)",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Enabled", value="Yes" if enabled_sets.get(set_id, True) else "No", inline=True)
        embed.add_field(
            name="Pack Price",
            value=f"{humanize_number(int(pack_prices.get(set_id, cards.DEFAULT_PACK_PRICES[set_id])))} credits",
            inline=True,
        )
        embed.add_field(name="Cards Cached", value=str(total_cards), inline=True)
        embed.add_field(
            name="Your Completion",
            value=f"{owned_unique}/{total_cards} ({completion}%)",
            inline=False,
        )
        odds_text = "\n".join(
            f"- {tier.replace('_', ' ').title()}: {weight}%"
            for tier, weight in cards.RARE_SLOT_ODDS
        )
        embed.add_field(
            name="Approximate Rare Slot Odds (Simulated)",
            value=odds_text,
            inline=False,
        )
        artwork_url = str(entry.get("artwork_url", "")).strip()
        if artwork_url:
            embed.set_image(url=artwork_url)
        await ctx.send(embed=embed)

    @tcg.command(name="buy")
    @commands.cooldown(2, 8, commands.BucketType.member)
    async def tcg_buy(self, ctx: commands.Context, set_token: str, amount: int = 1):
        """Buy unopened booster packs with credits."""
        if amount <= 0:
            await ctx.send("Amount must be greater than 0.")
            return
        set_id = self._resolve_set_token(set_token)
        if not set_id:
            await ctx.send("Unknown set token.")
            return
        guild_lock = self._guild_lock(ctx.guild.id)
        async with guild_lock:
            guild_conf = self.config.guild(ctx.guild)
            enabled_sets = await guild_conf.enabled_sets()
            if not enabled_sets.get(set_id, True):
                await ctx.send("That set is currently disabled in this server.")
                return
            max_buy = int(await guild_conf.max_buy())
            if amount > max_buy:
                await ctx.send(f"You can buy at most {max_buy} packs per command.")
                return
            pack_prices = await guild_conf.pack_prices()
            pack_price = int(pack_prices.get(set_id, cards.DEFAULT_PACK_PRICES[set_id]))
            if pack_price <= 0:
                await ctx.send("This set has an invalid pack price. Ask an admin to fix it.")
                return
            total_cost = pack_price * amount
            if not await bank.can_spend(ctx.author, total_cost):
                await ctx.send(
                    f"You need {humanize_number(total_cost)} credits, but your balance is too low."
                )
                return

            server_pack_limit = int(await guild_conf.server_pack_limit())
            server_pack_limit_window_hours = float(await guild_conf.server_pack_limit_window_hours())
            server_pack_state_before = await guild_conf.server_pack_purchases()
            allowed, new_server_pack_state, limit_message = cards.check_server_pack_purchase_limit(
                server_pack_state_before, server_pack_limit, server_pack_limit_window_hours, amount
            )
            if not allowed:
                await ctx.send(limit_message)
                return

            # --- Snapshot phase ---
            member_conf = self.config.member(ctx.author)
            packs_before = await member_conf.packs()
            packs = dict(packs_before)
            packs[set_id] = int(packs.get(set_id, 0)) + amount

            withdraw_done = False
            packs_saved = False
            server_state_saved = False
            try:
                await bank.withdraw_credits(ctx.author, total_cost)
                withdraw_done = True
                await member_conf.packs.set(packs)
                packs_saved = True
                await guild_conf.server_pack_purchases.set(new_server_pack_state)
                server_state_saved = True
            except Exception as exc:
                if packs_saved:
                    await member_conf.packs.set(packs_before)
                if server_state_saved:
                    await guild_conf.server_pack_purchases.set(server_pack_state_before)
                if withdraw_done:
                    await bank.deposit_credits(ctx.author, total_cost)
                await ctx.send(
                    f"Purchase failed and was rolled back: {exc}. No credits were charged."
                )
                return
        await ctx.send(
            f"You bought **{amount}** `{set_id}` pack(s) for **{humanize_number(total_cost)}** credits."
        )

    @tcg.command(name="packs")
    async def tcg_packs(self, ctx: commands.Context):
        """Show your unopened packs."""
        packs = await self.config.member(ctx.author).packs()
        if not packs:
            await ctx.send("You do not have any unopened packs.")
            return
        lines = []
        for set_id, amount in sorted(packs.items()):
            if int(amount) <= 0:
                continue
            lines.append(f"- {cards.set_display_name(set_id)} (`{set_id}`): **{amount}**")
        if not lines:
            await ctx.send("You do not have any unopened packs.")
            return
        embed = discord.Embed(title=f"{ctx.author.display_name}'s Packs", color=discord.Color.blurple())
        embed.description = "\n".join(lines)
        await ctx.send(embed=embed)

    @tcg.command(name="open")
    @commands.cooldown(1, 8, commands.BucketType.member)
    async def tcg_open(self, ctx: commands.Context, set_token: str, amount: Optional[int] = None):
        """Open one unopened pack and collect its cards.

        Packs can only be opened one at a time. Passing an amount other than
        1 is no longer supported and will not open any packs.
        """
        if amount is not None and amount != 1:
            await ctx.send(
                "Packs can only be opened one at a time now. "
                f"Use `{ctx.clean_prefix}tcg open {set_token}` (no amount) to open a single pack."
            )
            return
        set_id = self._resolve_set_token(set_token)
        if not set_id:
            await ctx.send("Unknown set token.")
            return
        guild_lock = self._guild_lock(ctx.guild.id)
        async with guild_lock:
            if not await self._ensure_set_cache(ctx.guild.id, set_id):
                await ctx.send(
                    "Set data is unavailable. The API may be down and no cache is currently available."
                )
                return
            member_conf = self.config.member(ctx.author)
            packs_before = await member_conf.packs()
            owned_packs = int(packs_before.get(set_id, 0))
            if owned_packs < 1:
                await ctx.send(f"You do not have any unopened `{set_id}` pack(s).")
                return

            # --- Snapshot phase: every piece of member state this command may
            # touch is captured up-front so it can be restored exactly if any
            # step below fails. ---
            owned_cards_before = await member_conf.owned_cards()
            pull_stats_before = await member_conf.pull_stats()
            packs_opened_before = int(await member_conf.packs_opened_count())

            try:
                pulled_cards, metadata = await self._pull_pack_cards(ctx.guild.id, set_id)
            except RuntimeError as exc:
                await ctx.send(f"Could not open pack: {exc}. No packs were consumed.")
                return

            owned_cards = dict(owned_cards_before)
            pull_stats = dict(pull_stats_before)
            packs = dict(packs_before)

            added_instances: List[Dict] = []
            for pulled in pulled_cards:
                instance_id = await self._next_card_instance_id(ctx.guild.id)
                instance = {
                    "instance_id": instance_id,
                    "api_card_id": pulled["api_card_id"],
                    "name": pulled["name"],
                    "set_id": pulled["set_id"],
                    "set_name": pulled["set_name"],
                    "set_number": pulled["set_number"],
                    "printed_number": pulled["printed_number"],
                    "rarity": pulled["rarity"],
                    "finish": pulled["finish"],
                    "small_image": pulled["small_image"],
                    "large_image": pulled["large_image"],
                    "api_url": pulled["api_url"],
                    "artist": pulled["artist"],
                    "pull_timestamp": cards.now_ts(),
                    "original_puller_id": ctx.author.id,
                    "current_owner_id": ctx.author.id,
                }
                owned_cards[instance_id] = instance
                added_instances.append(instance)
            reverse_count = sum(1 for card_instance in added_instances if card_instance["finish"] == "Reverse Holo")
            high_rarity_count = cards.high_rarity_pull_count(added_instances)
            pull_stats["total_pulls"] = int(pull_stats.get("total_pulls", 0)) + len(added_instances)
            pull_stats["reverse_pulls"] = int(pull_stats.get("reverse_pulls", 0)) + reverse_count
            pull_stats["high_rarity_pulls"] = int(pull_stats.get("high_rarity_pulls", 0)) + high_rarity_count

            tier = str(metadata.get("rare_tier"))
            special_hit = tier in {"ultra_rare", "special_illustration_rare", "hyper_rare"}
            best_pull = max(
                added_instances,
                key=lambda ci: cards.RARE_TIER_ORDER.index(
                    cards.map_rarity_to_rare_tier(ci.get("rarity")) or "regular_or_holo"
                ),
            )
            embed = discord.Embed(
                title=f"{cards.set_display_name(set_id)} Pack",
                description=(
                    "Odds are simulated for gameplay and are not official Pokémon Company pull odds.\n"
                    f"Energy slot: **{metadata['energy']}** (display only, not collectible)"
                ),
                color=discord.Color.green(),
            )
            normal_lines = [
                cards.format_card_line(ci)
                for ci in added_instances
                if ci["finish"] != "Reverse Holo"
                and (cards.map_rarity_to_rare_tier(ci.get("rarity")) or "regular_or_holo") == "regular_or_holo"
            ]
            highlight_lines = [
                cards.format_card_line(ci)
                for ci in added_instances
                if ci["finish"] == "Reverse Holo"
                or (cards.map_rarity_to_rare_tier(ci.get("rarity")) or "regular_or_holo") != "regular_or_holo"
            ]
            embed.add_field(
                name="Normal Pulls",
                value="\n".join(normal_lines[:12]) or "None",
                inline=False,
            )
            embed.add_field(
                name="Highlights",
                value="\n".join(highlight_lines[:12]) or "None",
                inline=False,
            )
            if best_pull.get("large_image"):
                embed.set_image(url=best_pull["large_image"])

            packs[set_id] = owned_packs - 1
            if packs[set_id] <= 0:
                packs.pop(set_id, None)

            owned_cards_saved = False
            packs_saved = False
            pull_stats_saved = False
            packs_opened_saved = False
            try:
                await member_conf.owned_cards.set(owned_cards)
                owned_cards_saved = True
                await member_conf.packs.set(packs)
                packs_saved = True
                await member_conf.pull_stats.set(pull_stats)
                pull_stats_saved = True
                await member_conf.packs_opened_count.set(packs_opened_before + 1)
                packs_opened_saved = True
            except Exception as exc:
                if packs_opened_saved:
                    await member_conf.packs_opened_count.set(packs_opened_before)
                if pull_stats_saved:
                    await member_conf.pull_stats.set(pull_stats_before)
                if packs_saved:
                    await member_conf.packs.set(packs_before)
                if owned_cards_saved:
                    await member_conf.owned_cards.set(owned_cards_before)
                await ctx.send(
                    f"Pack opening failed and was fully rolled back: {exc}. "
                    "Your pack, cards, and stats were restored to their prior state."
                )
                return

        await ctx.send(embed=embed)
        if special_hit:
            await ctx.send("🎉 Incredible luck! You hit an unusually rare pull.")

    @tcg.command(name="collection")
    async def tcg_collection(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """Collection overview."""
        target = member or ctx.author
        member_conf = self.config.member(target)
        owned_cards = await member_conf.owned_cards()
        favorites = set(await member_conf.favorites())
        card_list = list(owned_cards.values())
        total_physical = len(card_list)
        unique_cards = len({card.get("api_card_id") for card in card_list if card.get("api_card_id")})
        reverse_count = sum(1 for card in card_list if card.get("finish") == "Reverse Holo")
        duplicate_count = max(0, total_physical - unique_cards)
        rarity_counts: Dict[str, int] = {}
        for card in card_list:
            rarity = cards.rarity_for_display(card.get("rarity"))
            rarity_counts[rarity] = rarity_counts.get(rarity, 0) + 1

        set_cache = await self.config.guild(ctx.guild).set_cache()
        unique_by_set = cards.owned_unique_counts_by_set(card_list)
        completion_lines = []
        for set_id, set_name in cards.SUPPORTED_SETS.items():
            total_set_cards = int((set_cache.get(set_id) or {}).get("counts", {}).get("total", 0))
            owned_unique = int(unique_by_set.get(set_id, 0))
            completion = cards.completion_percent(owned_unique, total_set_cards)
            completion_lines.append(f"- `{set_id}` {set_name}: {owned_unique}/{total_set_cards} ({completion}%)")
        rarity_text = ", ".join(f"{name}: {count}" for name, count in sorted(rarity_counts.items()))
        if not rarity_text:
            rarity_text = "No cards yet."
        embed = discord.Embed(
            title=f"{target.display_name}'s TCG Collection",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Total Physical Cards", value=str(total_physical), inline=True)
        embed.add_field(name="Unique Cards", value=str(unique_cards), inline=True)
        embed.add_field(name="Duplicates", value=str(duplicate_count), inline=True)
        embed.add_field(name="Reverse Holo", value=str(reverse_count), inline=True)
        embed.add_field(name="Favorites", value=str(len(favorites)), inline=True)
        embed.add_field(name="Rarity Counts", value=rarity_text[:1024], inline=False)
        embed.add_field(name="Set Completion", value="\n".join(completion_lines)[:1024], inline=False)
        await ctx.send(embed=embed)

    @tcg.command(name="cards")
    async def tcg_cards(
        self, ctx: commands.Context, member: Optional[discord.Member] = None, set_token: Optional[str] = None
    ):
        """Browse owned cards."""
        target = member or ctx.author
        set_id = None
        if set_token:
            set_id = self._resolve_set_token(set_token)
            if not set_id:
                await ctx.send("Unknown set token.")
                return
        owned_cards = await self.config.member(target).owned_cards()
        rows = []
        for instance in owned_cards.values():
            if set_id and str(instance.get("set_id")) != set_id:
                continue
            pull_ts = int(instance.get("pull_timestamp", 0))
            rows.append(
                f"{cards.format_card_line(instance)} • Pulled <t:{pull_ts}:R>"
            )
        if not rows:
            await ctx.send("No cards found for that filter.")
            return
        rows.sort()
        pages = list(pagify("\n".join(rows), delims=["\n"], page_length=1800))
        for idx, page in enumerate(pages, start=1):
            embed = discord.Embed(
                title=f"{target.display_name}'s Cards ({idx}/{len(pages)})",
                description=page,
                color=discord.Color.blurple(),
            )
            await ctx.send(embed=embed)

    @tcg.command(name="card")
    async def tcg_card(self, ctx: commands.Context, instance_id: str):
        """Inspect one card instance by ID."""
        owner, instance = await self._find_card_globally(ctx.guild, instance_id.strip())
        if not instance:
            await ctx.send("Card instance not found in this server.")
            return
        pull_ts = int(instance.get("pull_timestamp", 0))
        embed = discord.Embed(
            title=f"{instance.get('name', 'Unknown')} • `{instance.get('instance_id')}`",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Owner", value=owner.mention if owner else "Unknown", inline=True)
        embed.add_field(name="Set", value=f"{instance.get('set_name')} (`{instance.get('set_id')}`)", inline=True)
        embed.add_field(
            name="Number",
            value=f"{instance.get('printed_number', '?')}/{instance.get('set_number', '?')}",
            inline=True,
        )
        embed.add_field(name="Rarity", value=cards.rarity_for_display(instance.get("rarity")), inline=True)
        embed.add_field(name="Finish", value=str(instance.get("finish", "Normal")), inline=True)
        embed.add_field(name="Pulled", value=f"<t:{pull_ts}:F>", inline=True)
        embed.add_field(name="API URL", value=str(instance.get("api_url", "N/A")), inline=False)
        if instance.get("large_image"):
            embed.set_image(url=instance["large_image"])
        await ctx.send(embed=embed)

    @tcg.command(name="duplicates")
    async def tcg_duplicates(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """Show duplicate cards."""
        target = member or ctx.author
        owned_cards = await self.config.member(target).owned_cards()
        per_card: Dict[str, Dict] = {}
        for instance in owned_cards.values():
            key = str(instance.get("api_card_id"))
            data = per_card.setdefault(
                key,
                {
                    "name": instance.get("name"),
                    "set_id": instance.get("set_id"),
                    "count": 0,
                },
            )
            data["count"] += 1
        duplicates = [v for v in per_card.values() if v["count"] > 1]
        if not duplicates:
            await ctx.send("No duplicates found.")
            return
        duplicates.sort(key=lambda item: item["count"], reverse=True)
        lines = [f"- {d['name']} (`{d['set_id']}`): x{d['count']}" for d in duplicates]
        pages = list(pagify("\n".join(lines), delims=["\n"], page_length=1800))
        for idx, page in enumerate(pages, 1):
            embed = discord.Embed(
                title=f"{target.display_name}'s Duplicates ({idx}/{len(pages)})",
                description=page,
                color=discord.Color.orange(),
            )
            await ctx.send(embed=embed)

    @tcg.command(name="missing")
    async def tcg_missing(self, ctx: commands.Context, set_token: str):
        """Show missing cards from one set."""
        set_id = self._resolve_set_token(set_token)
        if not set_id:
            await ctx.send("Unknown set token.")
            return
        if not await self._ensure_set_cache(ctx.guild.id, set_id):
            await ctx.send("Set cache is unavailable.")
            return
        cache = await self.config.guild(ctx.guild).set_cache()
        all_cards = (cache.get(set_id) or {}).get("cards") or []
        owned_cards = await self.config.member(ctx.author).owned_cards()
        owned_unique = {c.get("api_card_id") for c in owned_cards.values() if c.get("set_id") == set_id}
        missing_lines = []
        for card_data in all_cards:
            if card_data.get("card_id") not in owned_unique:
                missing_lines.append(
                    f"- {card_data.get('name')} ({card_data.get('printed_number')}) • {card_data.get('rarity')}"
                )
        if not missing_lines:
            await ctx.send("You have completed this set.")
            return
        pages = list(pagify("\n".join(missing_lines), delims=["\n"], page_length=1800))
        for idx, page in enumerate(pages, 1):
            embed = discord.Embed(
                title=f"Missing from {cards.set_display_name(set_id)} ({idx}/{len(pages)})",
                description=page,
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)

    @tcg.command(name="completion")
    async def tcg_completion(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """Show set completion percentages."""
        target = member or ctx.author
        cache = await self.config.guild(ctx.guild).set_cache()
        owned_cards = await self.config.member(target).owned_cards()
        unique_by_set = cards.owned_unique_counts_by_set(owned_cards.values())
        lines = []
        for set_id, set_name in cards.SUPPORTED_SETS.items():
            set_total = int((cache.get(set_id) or {}).get("counts", {}).get("total", 0))
            owned_unique = int(unique_by_set.get(set_id, 0))
            percent = cards.completion_percent(owned_unique, set_total)
            lines.append(f"- `{set_id}` {set_name}: **{percent}%** ({owned_unique}/{set_total})")
        embed = discord.Embed(
            title=f"{target.display_name}'s Set Completion",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed)

    @tcg.command(name="favorite")
    async def tcg_favorite(self, ctx: commands.Context, instance_id: str):
        """Toggle favorite lock status on your card."""
        member_conf = self.config.member(ctx.author)
        owned_cards = await member_conf.owned_cards()
        card_instance = owned_cards.get(instance_id)
        if not card_instance:
            await ctx.send("You do not own that card instance.")
            return
        favorites = set(await member_conf.favorites())
        if instance_id in favorites:
            favorites.remove(instance_id)
            await member_conf.favorites.set(list(favorites))
            await ctx.send(f"Removed `{instance_id}` from favorites.")
            return
        favorites.add(instance_id)
        await member_conf.favorites.set(list(favorites))
        await ctx.send(f"Added `{instance_id}` to favorites.")

    @tcg.group(name="trade", case_insensitive=True, autohelp=False)
    async def tcg_trade(self, ctx: commands.Context):
        """Direct player trading commands."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @tcg_trade.command(name="start")
    async def tcg_trade_start(self, ctx: commands.Context, member: discord.Member):
        """Start a trade with another member."""
        if member.id == ctx.author.id:
            await ctx.send("You cannot trade with yourself.")
            return
        if member.bot:
            await ctx.send("You cannot trade with bot accounts.")
            return
        lock = self._guild_lock(ctx.guild.id)
        async with lock:
            await self._expire_trades_for_guild_unlocked(ctx.guild.id)
            your_trade_id, _ = await self._find_member_trade(ctx.guild.id, ctx.author.id)
            their_trade_id, _ = await self._find_member_trade(ctx.guild.id, member.id)
            if your_trade_id:
                await ctx.send("You are already in an active trade.")
                return
            if their_trade_id:
                await ctx.send("That member is already in an active trade.")
                return
            trade_id = await self._next_trade_id(ctx.guild.id)
            expiry_minutes = int(await self.config.guild(ctx.guild).trade_expiry_minutes())
            trade = {
                "trade_id": trade_id,
                "created_at": cards.now_ts(),
                "updated_at": cards.now_ts(),
                "expires_at": cards.now_ts() + (expiry_minutes * 60),
                "offers": {
                    str(ctx.author.id): {"cards": [], "credits": 0, "confirmed": False},
                    str(member.id): {"cards": [], "credits": 0, "confirmed": False},
                },
            }
            active_trades = await self.config.guild(ctx.guild).active_trades()
            active_trades[trade_id] = trade
            await self.config.guild(ctx.guild).active_trades.set(active_trades)
        embed = self._build_trade_embed(trade_id, trade, ctx.guild)
        await ctx.send(f"Trade started: **{trade_id}**")
        await ctx.send(embed=embed)

    @tcg_trade.command(name="add")
    async def tcg_trade_add(self, ctx: commands.Context, instance_id: str):
        """Add one card to your active trade offer."""
        lock = self._guild_lock(ctx.guild.id)
        async with lock:
            trade_id, trade = await self._find_member_trade(ctx.guild.id, ctx.author.id)
            if not trade_id or not trade:
                await ctx.send("You are not in an active trade.")
                return
            if cards.trade_is_expired(trade):
                await self._cancel_trade(ctx.guild.id, trade_id, "Trade expired.")
                await ctx.send("That trade already expired.")
                return
            member_key = str(ctx.author.id)
            offer = trade["offers"][member_key]
            if instance_id in offer["cards"]:
                await ctx.send("That card is already in your offer.")
                return
            member_conf = self.config.member(ctx.author)
            owned_cards = await member_conf.owned_cards()
            favorites = set(await member_conf.favorites())
            instance = owned_cards.get(instance_id)
            listings = await self.config.guild(ctx.guild).active_listings()
            listed_card_ids = [str(item.get("card_instance_id")) for item in listings.values()]
            locked_cards = await self.config.guild(ctx.guild).locked_cards()
            valid, reason = cards.market_listing_is_valid_for_member_card(
                card=instance,
                member_id=ctx.author.id,
                favorites=list(favorites),
                lock_data=locked_cards,
                listed_card_ids=listed_card_ids,
            )
            if not valid:
                await ctx.send(reason)
                return
            if not cards.lock_card(locked_cards, instance_id, ctx.author.id, "trade", trade_id):
                await ctx.send("That card is already locked by another transaction.")
                return
            offer["cards"].append(instance_id)
            cards.reset_trade_confirmations(trade)
            trade["updated_at"] = cards.now_ts()
            active_trades = await self.config.guild(ctx.guild).active_trades()
            active_trades[trade_id] = trade
            await self.config.guild(ctx.guild).active_trades.set(active_trades)
            await self.config.guild(ctx.guild).locked_cards.set(locked_cards)
        await ctx.send(f"Added `{instance_id}` to trade `{trade_id}`. Confirmations were reset.")

    @tcg_trade.command(name="remove")
    async def tcg_trade_remove(self, ctx: commands.Context, instance_id: str):
        """Remove one card from your trade offer."""
        lock = self._guild_lock(ctx.guild.id)
        async with lock:
            trade_id, trade = await self._find_member_trade(ctx.guild.id, ctx.author.id)
            if not trade_id or not trade:
                await ctx.send("You are not in an active trade.")
                return
            offer = trade["offers"][str(ctx.author.id)]
            if instance_id not in offer["cards"]:
                await ctx.send("That card is not in your offer.")
                return
            offer["cards"].remove(instance_id)
            cards.reset_trade_confirmations(trade)
            trade["updated_at"] = cards.now_ts()
            active_trades = await self.config.guild(ctx.guild).active_trades()
            active_trades[trade_id] = trade
            locked_cards = await self.config.guild(ctx.guild).locked_cards()
            cards.unlock_card(locked_cards, instance_id, lock_type="trade", ref_id=trade_id)
            await self.config.guild(ctx.guild).active_trades.set(active_trades)
            await self.config.guild(ctx.guild).locked_cards.set(locked_cards)
        await ctx.send(f"Removed `{instance_id}` from trade `{trade_id}`. Confirmations were reset.")

    @tcg_trade.command(name="credits")
    async def tcg_trade_credits(self, ctx: commands.Context, amount: int):
        """Set your offered credits in the active trade."""
        if amount < 0:
            await ctx.send("Credits must be 0 or greater.")
            return
        lock = self._guild_lock(ctx.guild.id)
        async with lock:
            trade_id, trade = await self._find_member_trade(ctx.guild.id, ctx.author.id)
            if not trade_id or not trade:
                await ctx.send("You are not in an active trade.")
                return
            trade["offers"][str(ctx.author.id)]["credits"] = int(amount)
            cards.reset_trade_confirmations(trade)
            trade["updated_at"] = cards.now_ts()
            active_trades = await self.config.guild(ctx.guild).active_trades()
            active_trades[trade_id] = trade
            await self.config.guild(ctx.guild).active_trades.set(active_trades)
        await ctx.send(
            f"Set your offered credits to **{humanize_number(amount)}** in trade `{trade_id}`. Confirmations reset."
        )

    @tcg_trade.command(name="view")
    async def tcg_trade_view(self, ctx: commands.Context):
        """Show your active trade details."""
        lock = self._guild_lock(ctx.guild.id)
        async with lock:
            trade_id, trade = await self._find_member_trade(ctx.guild.id, ctx.author.id)
            if not trade_id or not trade:
                await ctx.send("You are not in an active trade.")
                return
            if cards.trade_is_expired(trade):
                await self._cancel_trade(ctx.guild.id, trade_id, "Trade expired.")
                await ctx.send("Your active trade expired and was cancelled.")
                return
            await ctx.send(embed=self._build_trade_embed(trade_id, trade, ctx.guild))

    @tcg_trade.command(name="confirm")
    @commands.cooldown(1, 5, commands.BucketType.member)
    async def tcg_trade_confirm(self, ctx: commands.Context):
        """Confirm your current trade offer."""
        lock = self._guild_lock(ctx.guild.id)
        async with lock:
            trade_id, trade = await self._find_member_trade(ctx.guild.id, ctx.author.id)
            if not trade_id or not trade:
                await ctx.send("You are not in an active trade.")
                return
            if cards.trade_is_expired(trade):
                await self._cancel_trade(ctx.guild.id, trade_id, "Trade expired.")
                await ctx.send("The trade expired before confirmation.")
                return
            your_key = str(ctx.author.id)
            other_member_id = self._trade_other_member_id(trade, ctx.author.id)
            if other_member_id is None:
                await ctx.send("Trade data is invalid.")
                return
            other_key = str(other_member_id)
            trade["offers"][your_key]["confirmed"] = True
            other_confirmed = bool(trade["offers"][other_key].get("confirmed"))
            if not other_confirmed:
                active_trades = await self.config.guild(ctx.guild).active_trades()
                active_trades[trade_id] = trade
                await self.config.guild(ctx.guild).active_trades.set(active_trades)
                await ctx.send("Your confirmation was saved. Waiting for the other member.")
                return

            member_a = ctx.guild.get_member(int(your_key))
            member_b = ctx.guild.get_member(int(other_key))
            if not member_a or not member_b:
                await self._cancel_trade(ctx.guild.id, trade_id, "A participant is no longer in the server.")
                await ctx.send("Trade cancelled because a participant left the server.")
                return
            offer_a = trade["offers"][your_key]
            offer_b = trade["offers"][other_key]

            conf_a = self.config.member(member_a)
            conf_b = self.config.member(member_b)
            cards_a = await conf_a.owned_cards()
            cards_b = await conf_b.owned_cards()
            listings = await self.config.guild(ctx.guild).active_listings()
            listed_ids = {str(v.get("card_instance_id")) for v in listings.values()}
            locked_cards = await self.config.guild(ctx.guild).locked_cards()

            for instance_id in offer_a.get("cards", []):
                c_instance = cards_a.get(instance_id)
                if not c_instance:
                    await self._cancel_trade(ctx.guild.id, trade_id, "One offered card is no longer owned.")
                    await ctx.send("Trade cancelled because a card is no longer owned.")
                    return
                if instance_id in listed_ids:
                    await self._cancel_trade(ctx.guild.id, trade_id, "One offered card is currently listed on market.")
                    await ctx.send("Trade cancelled because an offered card is listed on market.")
                    return
                lock_data = locked_cards.get(instance_id)
                if not lock_data or lock_data.get("lock_type") != "trade" or lock_data.get("ref_id") != trade_id:
                    await self._cancel_trade(ctx.guild.id, trade_id, "One offered card lock is invalid.")
                    await ctx.send("Trade cancelled because a card lock became invalid.")
                    return
            for instance_id in offer_b.get("cards", []):
                c_instance = cards_b.get(instance_id)
                if not c_instance:
                    await self._cancel_trade(ctx.guild.id, trade_id, "One offered card is no longer owned.")
                    await ctx.send("Trade cancelled because a card is no longer owned.")
                    return
                if instance_id in listed_ids:
                    await self._cancel_trade(ctx.guild.id, trade_id, "One offered card is currently listed on market.")
                    await ctx.send("Trade cancelled because an offered card is listed on market.")
                    return
                lock_data = locked_cards.get(instance_id)
                if not lock_data or lock_data.get("lock_type") != "trade" or lock_data.get("ref_id") != trade_id:
                    await self._cancel_trade(ctx.guild.id, trade_id, "One offered card lock is invalid.")
                    await ctx.send("Trade cancelled because a card lock became invalid.")
                    return

            credits_a = int(offer_a.get("credits", 0))
            credits_b = int(offer_b.get("credits", 0))
            if not await bank.can_spend(member_a, credits_a):
                cards.reset_trade_confirmations(trade)
                active_trades = await self.config.guild(ctx.guild).active_trades()
                active_trades[trade_id] = trade
                await self.config.guild(ctx.guild).active_trades.set(active_trades)
                await ctx.send(f"{member_a.display_name} no longer has enough credits.")
                return
            if not await bank.can_spend(member_b, credits_b):
                cards.reset_trade_confirmations(trade)
                active_trades = await self.config.guild(ctx.guild).active_trades()
                active_trades[trade_id] = trade
                await self.config.guild(ctx.guild).active_trades.set(active_trades)
                await ctx.send(f"{member_b.display_name} no longer has enough credits.")
                return

            # --- Snapshot phase: capture everything the transaction may mutate so we
            # can restore it exactly if any step below fails. ---
            balance_a_before = await bank.get_balance(member_a)
            balance_b_before = await bank.get_balance(member_b)
            cards_a_before = dict(cards_a)
            cards_b_before = dict(cards_b)
            trade_before = cards.copy_trade(trade)
            completed_a_before = int(await conf_a.completed_trade_count())
            completed_b_before = int(await conf_b.completed_trade_count())

            updated_a = dict(cards_a)
            updated_b = dict(cards_b)
            for instance_id in offer_a.get("cards", []):
                moved = cards.move_card_instance(updated_a.pop(instance_id), member_b.id)
                updated_b[instance_id] = moved
            for instance_id in offer_b.get("cards", []):
                moved = cards.move_card_instance(updated_b.pop(instance_id), member_a.id)
                updated_a[instance_id] = moved

            # Collapse the two-directional credit offers into a single net transfer
            # instead of performing up to four separate bank operations.
            direction, net_amount = cards.net_credit_transfer(credits_a, credits_b)
            net_from = member_a if direction == "a_to_b" else (member_b if direction == "b_to_a" else None)
            net_to = member_b if direction == "a_to_b" else (member_a if direction == "b_to_a" else None)

            credit_withdrawn = False
            credit_deposited = False
            cards_a_saved = False
            cards_b_saved = False
            completed_a_saved = False
            completed_b_saved = False
            try:
                if net_amount > 0:
                    if not await bank.can_spend(net_from, net_amount):
                        cards.reset_trade_confirmations(trade)
                        active_trades = await self.config.guild(ctx.guild).active_trades()
                        active_trades[trade_id] = trade
                        await self.config.guild(ctx.guild).active_trades.set(active_trades)
                        await ctx.send(f"{net_from.display_name} no longer has enough credits.")
                        return
                    await bank.withdraw_credits(net_from, net_amount)
                    credit_withdrawn = True
                    await bank.deposit_credits(net_to, net_amount)
                    credit_deposited = True

                await conf_a.owned_cards.set(updated_a)
                cards_a_saved = True
                await conf_b.owned_cards.set(updated_b)
                cards_b_saved = True
                await conf_a.completed_trade_count.set(completed_a_before + 1)
                completed_a_saved = True
                await conf_b.completed_trade_count.set(completed_b_before + 1)
                completed_b_saved = True
            except Exception as exc:
                # Restore state in the reverse order it was mutated.
                if cards_b_saved:
                    await conf_b.owned_cards.set(cards_b_before)
                if cards_a_saved:
                    await conf_a.owned_cards.set(cards_a_before)
                if completed_a_saved:
                    await conf_a.completed_trade_count.set(completed_a_before)
                if completed_b_saved:
                    await conf_b.completed_trade_count.set(completed_b_before)
                if credit_deposited:
                    reclaim_failed = False
                    try:
                        await bank.withdraw_credits(net_to, net_amount)
                    except Exception:
                        reclaim_failed = True
                    if not reclaim_failed:
                        await bank.deposit_credits(net_from, net_amount)
                    else:
                        log.critical(
                            "Failed to reclaim %s credits from %s while rolling back trade %s; "
                            "funds may be duplicated.",
                            net_amount,
                            net_to,
                            trade_id,
                        )
                elif credit_withdrawn:
                    await bank.deposit_credits(net_from, net_amount)
                cards.reset_trade_confirmations(trade)
                active_trades = await self.config.guild(ctx.guild).active_trades()
                active_trades[trade_id] = trade_before
                await self.config.guild(ctx.guild).active_trades.set(active_trades)
                await ctx.send(
                    f"Trade failed and was rolled back: {exc}. No credits or cards were exchanged."
                )
                return

            balance_a_after = await bank.get_balance(member_a)
            balance_b_after = await bank.get_balance(member_b)
            if net_amount > 0:
                expected_a = balance_a_before - (net_amount if net_from is member_a else -net_amount)
                expected_b = balance_b_before - (net_amount if net_from is member_b else -net_amount)
                if balance_a_after != expected_a or balance_b_after != expected_b:
                    log.warning(
                        "Post-trade balance mismatch for trade %s: expected (%s, %s) got (%s, %s)",
                        trade_id,
                        expected_a,
                        expected_b,
                        balance_a_after,
                        balance_b_after,
                    )

            guild_conf = self.config.guild(ctx.guild)
            active_trades = await guild_conf.active_trades()
            locked_cards = await guild_conf.locked_cards()
            trade_history = await guild_conf.trade_history()
            for card_id in offer_a.get("cards", []) + offer_b.get("cards", []):
                cards.unlock_card(locked_cards, card_id, lock_type="trade", ref_id=trade_id)
            active_trades.pop(trade_id, None)
            cards.append_bounded(
                trade_history,
                {
                    "trade_id": trade_id,
                    "completed_at": cards.now_ts(),
                    "user_a": member_a.id,
                    "user_b": member_b.id,
                    "cards_a_to_b": list(offer_a.get("cards", [])),
                    "cards_b_to_a": list(offer_b.get("cards", [])),
                    "credits_a_to_b": credits_a,
                    "credits_b_to_a": credits_b,
                },
                cards.MAX_HISTORY_TRADES,
            )
            await guild_conf.active_trades.set(active_trades)
            await guild_conf.locked_cards.set(locked_cards)
            await guild_conf.trade_history.set(trade_history)

        await ctx.send(
            f"✅ Trade `{trade_id}` completed.\n"
            f"{member_a.mention} ↔️ {member_b.mention}"
        )

    @tcg_trade.command(name="decline")
    async def tcg_trade_decline(self, ctx: commands.Context):
        """Decline your active trade."""
        lock = self._guild_lock(ctx.guild.id)
        async with lock:
            trade_id, trade = await self._find_member_trade(ctx.guild.id, ctx.author.id)
            if not trade_id or not trade:
                await ctx.send("You are not in an active trade.")
                return
            await self._cancel_trade(ctx.guild.id, trade_id, f"Trade declined by {ctx.author.display_name}.")
        await ctx.send("Trade declined.")

    @tcg_trade.command(name="cancel")
    async def tcg_trade_cancel(self, ctx: commands.Context):
        """Cancel your active trade."""
        lock = self._guild_lock(ctx.guild.id)
        async with lock:
            trade_id, trade = await self._find_member_trade(ctx.guild.id, ctx.author.id)
            if not trade_id or not trade:
                await ctx.send("You are not in an active trade.")
                return
            await self._cancel_trade(ctx.guild.id, trade_id, f"Trade cancelled by {ctx.author.display_name}.")
        await ctx.send("Trade cancelled.")

    @tcg.group(name="market", case_insensitive=True, autohelp=False)
    async def tcg_market(self, ctx: commands.Context):
        """Player marketplace commands."""
        if ctx.invoked_subcommand is None:
            listings = await self.config.guild(ctx.guild).active_listings()
            if not listings:
                await ctx.send("No active listings.")
                return
            lines = [
                f"- `{listing_id}` • {item.get('card_name')} (`{item.get('set_id')}`) • "
                f"{item.get('rarity')} • {humanize_number(int(item.get('price', 0)))} credits"
                for listing_id, item in sorted(listings.items(), key=lambda x: int(x[1].get("price", 0)))
            ]
            pages = list(pagify("\n".join(lines), delims=["\n"], page_length=1800))
            for idx, page in enumerate(pages, 1):
                embed = discord.Embed(
                    title=f"Active Listings ({idx}/{len(pages)})",
                    description=page,
                    color=discord.Color.green(),
                )
                await ctx.send(embed=embed)

    @tcg_market.command(name="list")
    async def tcg_market_list(self, ctx: commands.Context, instance_id: str, credit_price: int):
        """List a card for member-set price."""
        if credit_price < 1:
            await ctx.send("Price must be at least 1 credit.")
            return
        lock = self._guild_lock(ctx.guild.id)
        async with lock:
            member_conf = self.config.member(ctx.author)
            owned_cards = await member_conf.owned_cards()
            favorites = await member_conf.favorites()
            card_instance = owned_cards.get(instance_id)
            guild_conf = self.config.guild(ctx.guild)
            active_listings = await guild_conf.active_listings()
            lock_data = await guild_conf.locked_cards()
            listed_ids = [str(v.get("card_instance_id")) for v in active_listings.values()]
            valid, reason = cards.market_listing_is_valid_for_member_card(
                card=card_instance,
                member_id=ctx.author.id,
                favorites=favorites,
                lock_data=lock_data,
                listed_card_ids=listed_ids,
            )
            if not valid:
                await ctx.send(reason)
                return
            listing_id = await self._next_listing_id(ctx.guild.id)
            lock_ok = cards.lock_card(lock_data, instance_id, ctx.author.id, "market", listing_id)
            if not lock_ok:
                await ctx.send("That card is already locked by another transaction.")
                return
            active_listings[listing_id] = {
                "listing_id": listing_id,
                "seller_id": ctx.author.id,
                "card_instance_id": instance_id,
                "price": int(credit_price),
                "created_at": cards.now_ts(),
                "card_name": card_instance.get("name"),
                "set_id": card_instance.get("set_id"),
                "set_name": card_instance.get("set_name"),
                "rarity": card_instance.get("rarity"),
                "finish": card_instance.get("finish"),
                "small_image": card_instance.get("small_image"),
                "large_image": card_instance.get("large_image"),
            }
            await guild_conf.active_listings.set(active_listings)
            await guild_conf.locked_cards.set(lock_data)
        await ctx.send(
            f"Listed `{instance_id}` as `{listing_id}` for **{humanize_number(credit_price)}** credits."
        )

    @tcg_market.command(name="buy")
    @commands.cooldown(1, 5, commands.BucketType.member)
    async def tcg_market_buy(self, ctx: commands.Context, listing_id: str):
        """Buy a listed card."""
        lock = self._guild_lock(ctx.guild.id)
        async with lock:
            guild_conf = self.config.guild(ctx.guild)
            active_listings = await guild_conf.active_listings()
            listing = active_listings.get(listing_id)
            if not listing:
                await ctx.send("Listing not found.")
                return
            seller_id = int(listing.get("seller_id", 0))
            if seller_id == ctx.author.id:
                await ctx.send("You cannot buy your own listing.")
                return
            seller = ctx.guild.get_member(seller_id)
            if not seller:
                lock_data = await guild_conf.locked_cards()
                self._unlock_listing_card(lock_data, listing_id)
                active_listings.pop(listing_id, None)
                await guild_conf.locked_cards.set(lock_data)
                await guild_conf.active_listings.set(active_listings)
                await ctx.send("Listing was invalid and has been cancelled.")
                return
            seller_conf = self.config.member(seller)
            seller_cards = await seller_conf.owned_cards()
            instance_id = str(listing.get("card_instance_id"))
            card_instance = seller_cards.get(instance_id)
            lock_data = await guild_conf.locked_cards()
            lock_info = lock_data.get(instance_id)

            # Full listing validity re-check immediately before purchase, per spec:
            # listing exists, seller still owns the card, current_owner_id matches
            # seller, lock exists with lock_type == "market", owner_id == seller_id,
            # and ref_id == listing_id.
            listing_valid = (
                bool(card_instance)
                and int(card_instance.get("current_owner_id", 0)) == seller_id
                and bool(lock_info)
                and str(lock_info.get("lock_type")) == "market"
                and int(lock_info.get("owner_id", 0)) == seller_id
                and str(lock_info.get("ref_id")) == str(listing_id)
            )
            if not listing_valid:
                self._unlock_listing_card(lock_data, listing_id)
                active_listings.pop(listing_id, None)
                await guild_conf.locked_cards.set(lock_data)
                await guild_conf.active_listings.set(active_listings)
                await ctx.send("Listing became invalid and has been cancelled.")
                return
            price = int(listing.get("price", 0))
            if price < 1:
                await ctx.send("Listing price is invalid.")
                return
            if not await bank.can_spend(ctx.author, price):
                await ctx.send("You do not have enough credits.")
                return
            tax_percent = float(await guild_conf.market_tax_percent())
            tax_amount, seller_payout = cards.calculate_market_tax(price, tax_percent)

            # --- Snapshot phase ---
            buyer_conf = self.config.member(ctx.author)
            buyer_cards_before = await buyer_conf.owned_cards()
            seller_cards_before = dict(seller_cards)
            active_listings_before = dict(active_listings)
            lock_data_before = dict(lock_data)

            withdraw_done = False
            deposit_done = False
            seller_cards_saved = False
            buyer_cards_saved = False
            try:
                await bank.withdraw_credits(ctx.author, price)
                withdraw_done = True
                await bank.deposit_credits(seller, seller_payout)
                deposit_done = True

                buyer_cards = dict(buyer_cards_before)
                seller_cards_updated = dict(seller_cards_before)
                moved = cards.move_card_instance(seller_cards_updated.pop(instance_id), ctx.author.id)
                buyer_cards[instance_id] = moved

                await seller_conf.owned_cards.set(seller_cards_updated)
                seller_cards_saved = True
                await buyer_conf.owned_cards.set(buyer_cards)
                buyer_cards_saved = True

                active_listings.pop(listing_id, None)
                cards.unlock_card(lock_data, instance_id, lock_type="market", ref_id=listing_id)
                market_history = await guild_conf.market_history()
                cards.append_bounded(
                    market_history,
                    {
                        "listing_id": listing_id,
                        "card_instance_id": instance_id,
                        "card_name": listing.get("card_name"),
                        "set_id": listing.get("set_id"),
                        "rarity": listing.get("rarity"),
                        "finish": listing.get("finish"),
                        "price": price,
                        "tax": tax_amount,
                        "seller_payout": seller_payout,
                        "seller_id": seller.id,
                        "buyer_id": ctx.author.id,
                        "sold_at": cards.now_ts(),
                    },
                    cards.MAX_HISTORY_SALES,
                )
                await guild_conf.active_listings.set(active_listings)
                await guild_conf.locked_cards.set(lock_data)
                await guild_conf.market_history.set(market_history)
            except Exception as exc:
                # Restore ownership state exactly as it was before this purchase.
                if buyer_cards_saved:
                    await buyer_conf.owned_cards.set(buyer_cards_before)
                if seller_cards_saved:
                    await seller_conf.owned_cards.set(seller_cards_before)
                await guild_conf.active_listings.set(active_listings_before)
                await guild_conf.locked_cards.set(lock_data_before)
                if deposit_done:
                    reclaim_failed = False
                    try:
                        await bank.withdraw_credits(seller, seller_payout)
                    except Exception:
                        reclaim_failed = True
                    if not reclaim_failed:
                        await bank.deposit_credits(ctx.author, price)
                    else:
                        log.critical(
                            "Failed to reclaim %s credits from seller %s while rolling back "
                            "purchase of listing %s; funds may be duplicated.",
                            seller_payout,
                            seller.id,
                            listing_id,
                        )
                elif withdraw_done:
                    await bank.deposit_credits(ctx.author, price)
                await ctx.send(
                    f"Purchase failed and was rolled back: {exc}. No credits or cards were exchanged."
                )
                return
        await ctx.send(
            f"✅ Purchased `{listing_id}` for **{humanize_number(price)}** credits "
            f"(tax: {humanize_number(tax_amount)}, seller received {humanize_number(seller_payout)})."
        )

    @tcg_market.command(name="cancel")
    async def tcg_market_cancel(self, ctx: commands.Context, listing_id: str):
        """Cancel one of your listings."""
        lock = self._guild_lock(ctx.guild.id)
        async with lock:
            guild_conf = self.config.guild(ctx.guild)
            active_listings = await guild_conf.active_listings()
            listing = active_listings.get(listing_id)
            if not listing:
                await ctx.send("Listing not found.")
                return
            is_owner = int(listing.get("seller_id", 0)) == ctx.author.id
            is_admin = ctx.author.guild_permissions.manage_guild
            if not is_owner and not is_admin:
                await ctx.send("Only the seller or a server manager can cancel this listing.")
                return
            active_listings.pop(listing_id, None)
            lock_data = await guild_conf.locked_cards()
            self._unlock_listing_card(lock_data, listing_id)
            await guild_conf.active_listings.set(active_listings)
            await guild_conf.locked_cards.set(lock_data)
        await ctx.send(f"Cancelled listing `{listing_id}`.")

    @tcg_market.command(name="mine")
    async def tcg_market_mine(self, ctx: commands.Context):
        """Show your active listings."""
        listings = await self.config.guild(ctx.guild).active_listings()
        mine = [v for v in listings.values() if int(v.get("seller_id", 0)) == ctx.author.id]
        if not mine:
            await ctx.send("You do not have any active listings.")
            return
        lines = [
            f"- `{item['listing_id']}` • `{item['card_instance_id']}` • {item['card_name']} • "
            f"{humanize_number(int(item['price']))} credits"
            for item in sorted(mine, key=lambda x: int(x.get("price", 0)), reverse=True)
        ]
        embed = discord.Embed(title=f"{ctx.author.display_name}'s Listings", description="\n".join(lines), color=discord.Color.blurple())
        await ctx.send(embed=embed)

    @tcg_market.command(name="view")
    async def tcg_market_view(self, ctx: commands.Context, listing_id: str):
        """View one listing."""
        listing = (await self.config.guild(ctx.guild).active_listings()).get(listing_id)
        if not listing:
            await ctx.send("Listing not found.")
            return
        seller = ctx.guild.get_member(int(listing.get("seller_id", 0)))
        embed = discord.Embed(
            title=f"Listing {listing_id}",
            color=discord.Color.green(),
        )
        embed.add_field(name="Card", value=f"{listing.get('card_name')} (`{listing.get('card_instance_id')}`)", inline=False)
        embed.add_field(name="Set", value=f"{listing.get('set_name')} (`{listing.get('set_id')}`)", inline=True)
        embed.add_field(name="Rarity", value=str(listing.get("rarity")), inline=True)
        embed.add_field(name="Finish", value=str(listing.get("finish")), inline=True)
        embed.add_field(name="Price", value=f"{humanize_number(int(listing.get('price', 0)))} credits", inline=True)
        embed.add_field(name="Seller", value=seller.mention if seller else "Unknown", inline=True)
        if listing.get("large_image"):
            embed.set_image(url=listing["large_image"])
        await ctx.send(embed=embed)

    @tcg_market.command(name="search")
    async def tcg_market_search(self, ctx: commands.Context, *, query: str):
        """Search active listings by card name, set, or rarity."""
        clean_query = cards.sanitize_search_text(query)
        if not clean_query:
            await ctx.send("Search text is empty after sanitization.")
            return
        listings = await self.config.guild(ctx.guild).active_listings()
        matched = []
        for listing in listings.values():
            if cards.card_matches_search(
                {
                    "name": listing.get("card_name"),
                    "set_name": listing.get("set_name"),
                    "rarity": listing.get("rarity"),
                    "set_id": listing.get("set_id"),
                },
                clean_query,
            ):
                matched.append(listing)
        if not matched:
            await ctx.send("No active listings matched that search.")
            return
        lines = [
            f"- `{item['listing_id']}` • {item['card_name']} (`{item['set_id']}`) • "
            f"{item['rarity']} • {humanize_number(int(item['price']))} credits"
            for item in sorted(matched, key=lambda x: int(x.get("price", 0)))
        ]
        pages = list(pagify("\n".join(lines), delims=["\n"], page_length=1800))
        for idx, page in enumerate(pages, 1):
            embed = discord.Embed(
                title=f"Market Search: {clean_query} ({idx}/{len(pages)})",
                description=page,
                color=discord.Color.blurple(),
            )
            await ctx.send(embed=embed)

    @tcg_market.command(name="history")
    async def tcg_market_history(self, ctx: commands.Context, *, card_name: str = ""):
        """Show completed member sale history."""
        history = await self.config.guild(ctx.guild).market_history()
        if card_name:
            token = cards.sanitize_search_text(card_name).lower()
            filtered = [entry for entry in history if token in str(entry.get("card_name", "")).lower()]
        else:
            filtered = list(history)
        if not filtered:
            await ctx.send("No matching market history entries were found.")
            return
        prices = [int(item.get("price", 0)) for item in filtered]
        summary = cards.summarize_sales(prices)
        lines = []
        for entry in filtered[-20:]:
            sold_ts = int(entry.get("sold_at", 0))
            lines.append(
                f"- <t:{sold_ts}:R> • {entry.get('card_name')} (`{entry.get('set_id')}`) "
                f"sold for {humanize_number(int(entry.get('price', 0)))} credits"
            )
        embed = discord.Embed(
            title="Member-Created Market Sale History",
            description="\n".join(lines) or "No sales.",
            color=discord.Color.dark_teal(),
        )
        if summary:
            embed.add_field(
                name="Member-Created Price Stats",
                value=(
                    f"Latest: {humanize_number(summary['latest'])}\n"
                    f"Lowest: {humanize_number(summary['lowest'])}\n"
                    f"Highest: {humanize_number(summary['highest'])}\n"
                    f"Median: {humanize_number(summary['median'])}"
                ),
                inline=False,
            )
        embed.set_footer(text="These are member sale records, not official or fixed card values.")
        await ctx.send(embed=embed)

    @tcg.command(name="leaderboard")
    async def tcg_leaderboard(self, ctx: commands.Context, category: str):
        """Show a leaderboard."""
        normalized = category.strip().lower()
        valid = {"cards", "unique", "rare", "completion", "trades"}
        if normalized not in valid:
            await ctx.send("Invalid category. Use: cards, unique, rare, completion, trades.")
            return
        all_members = await self.config.all_members(ctx.guild)
        cache = await self.config.guild(ctx.guild).set_cache()
        rows: List[Tuple[int, float]] = []
        for member_id_str, member_data in all_members.items():
            member_id = int(member_id_str)
            owned_cards = list((member_data.get("owned_cards") or {}).values())
            if normalized == "cards":
                metric = float(len(owned_cards))
            elif normalized == "unique":
                metric = float(len({c.get("api_card_id") for c in owned_cards if c.get("api_card_id")}))
            elif normalized == "rare":
                metric = float(cards.high_rarity_pull_count(owned_cards))
            elif normalized == "trades":
                metric = float(int(member_data.get("completed_trade_count", 0)))
            else:
                unique_by_set = cards.owned_unique_counts_by_set(owned_cards)
                percentages = []
                for set_id in cards.SUPPORTED_SETS:
                    total_set = int((cache.get(set_id) or {}).get("counts", {}).get("total", 0))
                    percentages.append(cards.completion_percent(int(unique_by_set.get(set_id, 0)), total_set))
                metric = statistics.mean(percentages) if percentages else 0.0
            if metric <= 0:
                continue
            rows.append((member_id, metric))
        if not rows:
            await ctx.send("No leaderboard data yet.")
            return
        rows.sort(key=lambda item: item[1], reverse=True)
        lines = []
        for index, (member_id, metric) in enumerate(rows[:20], 1):
            member = ctx.guild.get_member(member_id)
            name = member.display_name if member else f"User {member_id}"
            metric_text = f"{metric:.2f}%" if normalized == "completion" else humanize_number(int(metric))
            lines.append(f"{index}. **{name}** — {metric_text}")
        embed = discord.Embed(
            title=f"TCG Leaderboard: {normalized.title()}",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await ctx.send(embed=embed)

    @commands.group(name="tcgset", case_insensitive=True, autohelp=False)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def tcgset(self, ctx: commands.Context):
        """TCG admin settings."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @tcgset.command(name="price")
    async def tcgset_price(self, ctx: commands.Context, set_token: str, credits: int):
        """Set pack price."""
        if credits < 1:
            await ctx.send("Price must be at least 1 credit.")
            return
        set_id = self._resolve_set_token(set_token)
        if not set_id:
            await ctx.send("Unknown set token.")
            return
        async with self.config.guild(ctx.guild).pack_prices() as prices:
            prices[set_id] = int(credits)
        await ctx.send(f"Price for `{set_id}` set to {humanize_number(credits)} credits.")

    @tcgset.command(name="enable")
    async def tcgset_enable(self, ctx: commands.Context, set_token: str):
        set_id = self._resolve_set_token(set_token)
        if not set_id:
            await ctx.send("Unknown set token.")
            return
        async with self.config.guild(ctx.guild).enabled_sets() as enabled:
            enabled[set_id] = True
        await ctx.send(f"Enabled `{set_id}`.")

    @tcgset.command(name="disable")
    async def tcgset_disable(self, ctx: commands.Context, set_token: str):
        set_id = self._resolve_set_token(set_token)
        if not set_id:
            await ctx.send("Unknown set token.")
            return
        async with self.config.guild(ctx.guild).enabled_sets() as enabled:
            enabled[set_id] = False
        await ctx.send(f"Disabled `{set_id}`.")

    @tcgset.command(name="maxbuy")
    async def tcgset_maxbuy(self, ctx: commands.Context, amount: int):
        if amount < 1:
            await ctx.send("maxbuy must be at least 1.")
            return
        await self.config.guild(ctx.guild).max_buy.set(int(amount))
        await ctx.send(f"Set max buy amount to {amount}.")

    @tcgset.command(name="maxopen")
    async def tcgset_maxopen(self, ctx: commands.Context, amount: int):
        """Deprecated: packs can only be opened one at a time now."""
        if amount < 1:
            await ctx.send("maxopen must be at least 1.")
            return
        await self.config.guild(ctx.guild).max_open.set(int(amount))
        await ctx.send(
            f"Set max open amount to {amount}. Note: `{ctx.clean_prefix}tcg open` now always "
            "opens exactly one pack per use regardless of this setting."
        )

    @tcgset.command(name="serverpacklimit")
    async def tcgset_serverpacklimit(
        self,
        ctx: commands.Context,
        amount: int,
        window_hours: Optional[float] = None,
        reset_window: bool = False,
    ):
        """Set a server-wide cap on packs purchased within a rolling time window.

        Use 0 to disable the server-wide limit entirely. Pass `True` for
        `reset_window` to immediately clear the current window's usage count.
        """
        if amount < 0:
            await ctx.send("The server pack limit must be 0 (disabled) or greater.")
            return
        if window_hours is not None and window_hours <= 0:
            await ctx.send("The window (in hours) must be greater than 0.")
            return
        guild_conf = self.config.guild(ctx.guild)
        await guild_conf.server_pack_limit.set(int(amount))
        if window_hours is not None:
            await guild_conf.server_pack_limit_window_hours.set(float(window_hours))
        if reset_window:
            await guild_conf.server_pack_purchases.set({"window_start": cards.now_ts(), "count": 0})
        current_window_hours = float(await guild_conf.server_pack_limit_window_hours())
        if amount <= 0:
            await ctx.send("Server-wide pack purchase limit disabled.")
        else:
            reset_note = " The current window's usage was reset." if reset_window else ""
            await ctx.send(
                f"Server-wide pack purchase limit set to **{amount}** pack(s) per "
                f"{current_window_hours:g} hour(s).{reset_note}"
            )

    @tcgset.command(name="tradeexpiry")
    async def tcgset_tradeexpiry(self, ctx: commands.Context, minutes: int):
        if minutes < 1:
            await ctx.send("Trade expiry must be at least 1 minute.")
            return
        await self.config.guild(ctx.guild).trade_expiry_minutes.set(int(minutes))
        await ctx.send(f"Trade expiry set to {minutes} minute(s).")

    @tcgset.command(name="markettax")
    async def tcgset_markettax(self, ctx: commands.Context, percentage: float):
        if percentage < 0 or percentage > 100:
            await ctx.send("Market tax must be between 0 and 100.")
            return
        await self.config.guild(ctx.guild).market_tax_percent.set(float(percentage))
        await ctx.send(f"Marketplace tax set to {round(percentage, 2)}%.")

    @tcgset.command(name="refreshsets")
    async def tcgset_refreshsets(self, ctx: commands.Context):
        """Refresh cached card data from the Pokémon TCG API."""
        await ctx.send("Refreshing set cache...")
        results = await self._refresh_all_sets_for_guild(ctx.guild.id)
        for page in pagify("\n".join(results), delims=["\n"], page_length=1900):
            await ctx.send(page)

    @tcgset.command(name="settings")
    async def tcgset_settings(self, ctx: commands.Context):
        guild_conf = self.config.guild(ctx.guild)
        enabled_sets = await guild_conf.enabled_sets()
        pack_prices = await guild_conf.pack_prices()
        set_cache = await guild_conf.set_cache()
        embed = discord.Embed(title="TCG Settings", color=discord.Color.blurple())
        embed.add_field(name="Max Buy", value=str(await guild_conf.max_buy()), inline=True)
        embed.add_field(name="Max Open", value="1 (fixed)", inline=True)
        embed.add_field(name="Trade Expiry", value=f"{await guild_conf.trade_expiry_minutes()} min", inline=True)
        embed.add_field(name="Market Tax", value=f"{await guild_conf.market_tax_percent()}%", inline=True)
        server_pack_limit = int(await guild_conf.server_pack_limit())
        server_pack_limit_window_hours = float(await guild_conf.server_pack_limit_window_hours())
        if server_pack_limit > 0:
            server_pack_purchases = await guild_conf.server_pack_purchases()
            limit_value = (
                f"{int(server_pack_purchases.get('count', 0))}/{server_pack_limit} used "
                f"per {server_pack_limit_window_hours:g}h"
            )
        else:
            limit_value = "Disabled"
        embed.add_field(name="Server Pack Limit", value=limit_value, inline=True)
        lines = []
        for set_id, set_name in cards.SUPPORTED_SETS.items():
            enabled = enabled_sets.get(set_id, True)
            price = int(pack_prices.get(set_id, cards.DEFAULT_PACK_PRICES[set_id]))
            cached = int((set_cache.get(set_id) or {}).get("counts", {}).get("total", 0))
            lines.append(
                f"- `{set_id}` {set_name}: {'Enabled' if enabled else 'Disabled'} | "
                f"{humanize_number(price)} credits | cache: {cached}"
            )
        embed.add_field(name="Sets", value="\n".join(lines), inline=False)
        await ctx.send(embed=embed)


    @tcgset.command(name="repairlocks")
    async def tcgset_repairlocks(self, ctx: commands.Context):
        """Rebuild lock state from active trades/listings."""
        lock = self._guild_lock(ctx.guild.id)
        async with lock:
            guild_conf = self.config.guild(ctx.guild)
            trades = await guild_conf.active_trades()
            listings = await guild_conf.active_listings()
            rebuilt: Dict[str, Dict] = {}
            for trade_id, trade in trades.items():
                for member_id, offer in (trade.get("offers") or {}).items():
                    for instance_id in offer.get("cards", []):
                        rebuilt[str(instance_id)] = {
                            "owner_id": int(member_id),
                            "lock_type": "trade",
                            "ref_id": trade_id,
                            "locked_at": cards.now_ts(),
                        }
            for listing_id, listing in listings.items():
                instance_id = str(listing.get("card_instance_id"))
                rebuilt[instance_id] = {
                    "owner_id": int(listing.get("seller_id", 0)),
                    "lock_type": "market",
                    "ref_id": listing_id,
                    "locked_at": cards.now_ts(),
                }
            await guild_conf.locked_cards.set(rebuilt)
        await ctx.send(f"Repaired locks. Active locks now: {len(rebuilt)}")

    @tcgset.command(name="resetmember")
    async def tcgset_resetmember(self, ctx: commands.Context, member: discord.Member):
        """Delete all TCG data for a member in this guild."""
        warning = (
            f"This will permanently delete TCG data for **{member.display_name}** in this server.\n"
            "Type `CONFIRM` within 20 seconds to continue."
        )
        await ctx.send(warning)

        def check(message: discord.Message) -> bool:
            return (
                message.author.id == ctx.author.id
                and message.channel.id == ctx.channel.id
                and message.content.strip().upper() == "CONFIRM"
            )

        try:
            await self.bot.wait_for("message", check=check, timeout=20)
        except asyncio.TimeoutError:
            await ctx.send("Reset cancelled (confirmation timeout).")
            return

        lock = self._guild_lock(ctx.guild.id)
        async with lock:
            guild_conf = self.config.guild(ctx.guild)
            active_trades = await guild_conf.active_trades()
            locked_cards = await guild_conf.locked_cards()
            for trade_id, trade in list(active_trades.items()):
                if str(member.id) in (trade.get("offers") or {}):
                    self._unlock_trade_cards(locked_cards, trade_id)
                    del active_trades[trade_id]
            active_listings = await guild_conf.active_listings()
            for listing_id, listing in list(active_listings.items()):
                if int(listing.get("seller_id", 0)) == member.id:
                    self._unlock_listing_card(locked_cards, listing_id)
                    del active_listings[listing_id]
            await guild_conf.active_trades.set(active_trades)
            await guild_conf.active_listings.set(active_listings)
            await guild_conf.locked_cards.set(locked_cards)
            await self.config.member(member).clear()
        await ctx.send(f"Reset TCG data for {member.mention}.")

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        all_guilds = await self.config.all_guilds()
        for guild_id_str in all_guilds.keys():
            guild_id = int(guild_id_str)
            lock = self._guild_lock(guild_id)
            async with lock:
                guild_conf = self.config.guild_from_id(guild_id)
                active_trades = await guild_conf.active_trades()
                active_listings = await guild_conf.active_listings()
                locked_cards = await guild_conf.locked_cards()
                trade_history = await guild_conf.trade_history()
                market_history = await guild_conf.market_history()

                for trade_id, trade in list(active_trades.items()):
                    if str(user_id) in (trade.get("offers") or {}):
                        self._unlock_trade_cards(locked_cards, trade_id)
                        del active_trades[trade_id]
                for listing_id, listing in list(active_listings.items()):
                    if int(listing.get("seller_id", 0)) == int(user_id):
                        self._unlock_listing_card(locked_cards, listing_id)
                        del active_listings[listing_id]
                for entry in trade_history:
                    if int(entry.get("user_a", 0)) == int(user_id):
                        entry["user_a"] = 0
                    if int(entry.get("user_b", 0)) == int(user_id):
                        entry["user_b"] = 0
                for entry in market_history:
                    if int(entry.get("seller_id", 0)) == int(user_id):
                        entry["seller_id"] = 0
                    if int(entry.get("buyer_id", 0)) == int(user_id):
                        entry["buyer_id"] = 0
                for instance_id, lock_data in list(locked_cards.items()):
                    if int(lock_data.get("owner_id", 0)) == int(user_id):
                        del locked_cards[instance_id]

                all_members = await self.config.all_members(guild_id)
                for member_id_str, member_data in all_members.items():
                    owned_cards = dict((member_data.get("owned_cards") or {}))
                    changed = False
                    for instance_id, instance in owned_cards.items():
                        if int(instance.get("original_puller_id", 0)) == int(user_id):
                            instance["original_puller_id"] = 0
                            owned_cards[instance_id] = instance
                            changed = True
                    if changed:
                        await self.config.member_from_ids(int(guild_id), int(member_id_str)).owned_cards.set(
                            owned_cards
                        )
                await guild_conf.active_trades.set(active_trades)
                await guild_conf.active_listings.set(active_listings)
                await guild_conf.locked_cards.set(locked_cards)
                await guild_conf.trade_history.set(trade_history)
                await guild_conf.market_history.set(market_history)
                await self.config.member_from_ids(guild_id, user_id).clear()


async def setup(bot):
    await bot.add_cog(TradingCards(bot))
