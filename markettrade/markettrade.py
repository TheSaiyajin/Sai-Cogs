import random
import time

import discord
from redbot.core import Config, bank, commands
from redbot.core.utils.chat_formatting import humanize_number
from discord.ext import tasks


class MarketTrade(commands.Cog):
    """A simple in-server fake market for coins and stocks."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=219084771503, force_registration=True)
        self.config.register_guild(
            assets={},
            update_interval_minutes=10,
            last_update_ts=0.0,
            seeded=False,
            prices_cache={},
            live_prices_message={},
            price_history={},
            active_events={},
            random_events_enabled=True,
            random_event_chance_percent=6.0,
            event_announce_channel_id=0,
        )
        self.config.register_member(holdings={}, cost_basis={}, realized_profit={}, auto_orders={})
        self.price_updater.start()

    def cog_unload(self):
        self.price_updater.cancel()

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return symbol.strip().upper()

    def _build_default_assets(self):
        return {
            "BTC": {
                "name": "Bitcoin",
                "kind": "crypto",
                "price": 1200.0,
                "min_price": 100.0,
                "max_price": 100000.0,
                "volatility": 0.08,
                "risk": 1.2,
                "momentum": 0.68,
                "reversal_accel": 0.08,
                "drift": 0.0012,
                "bull_bias": 0.07,
                "trend": 0,
                "trend_streak": 0,
            },
            "ETH": {
                "name": "Ethereum",
                "kind": "crypto",
                "price": 700.0,
                "min_price": 50.0,
                "max_price": 50000.0,
                "volatility": 0.08,
                "risk": 1.15,
                "momentum": 0.66,
                "reversal_accel": 0.08,
                "drift": 0.001,
                "bull_bias": 0.06,
                "trend": 0,
                "trend_streak": 0,
            },
            "AAPL": {
                "name": "Apple",
                "kind": "stock",
                "price": 350.0,
                "min_price": 10.0,
                "max_price": 10000.0,
                "volatility": 0.05,
                "risk": 1.0,
                "momentum": 0.58,
                "reversal_accel": 0.09,
                "drift": 0.0007,
                "bull_bias": 0.05,
                "trend": 0,
                "trend_streak": 0,
            },
        }

    async def _ensure_guild_initialized(self, guild_id: int):
        guild_conf = self.config.guild_from_id(guild_id)
        if await guild_conf.seeded():
            return

        async with guild_conf.assets() as assets:
            if not assets:
                assets.update(self._build_default_assets())

        seeded_assets = await guild_conf.assets()
        await self._record_prices_snapshot(guild_conf, seeded_assets)
        await guild_conf.seeded.set(True)

    async def _get_assets(self, guild):
        await self._ensure_guild_initialized(guild.id)
        return await self.config.guild(guild).assets()

    @staticmethod
    def _build_sparkline(values):
        bars = "▁▂▃▄▅▆▇█"
        if not values:
            return ""
        min_value = min(values)
        max_value = max(values)
        if max_value == min_value:
            return bars[0] * len(values)
        step = (max_value - min_value) / (len(bars) - 1)
        return "".join(bars[min(len(bars) - 1, int((value - min_value) / step))] for value in values)

    async def _append_price_history(self, guild_conf, symbol: str, price: float):
        async with guild_conf.price_history() as history:
            symbol_history = list(history.get(symbol, []))
            symbol_history.append(round(float(price), 2))
            history[symbol] = symbol_history[-120:]

    async def _record_prices_snapshot(self, guild_conf, assets):
        for symbol, asset in assets.items():
            await self._append_price_history(guild_conf, symbol, float(asset["price"]))

    @staticmethod
    def _format_event_line(event_data):
        change_percent = round(float(event_data.get("change_per_tick", 0.0)) * 100, 2)
        remaining_ticks = max(0, int(event_data.get("remaining_ticks", 0)))
        direction = "+" if change_percent >= 0 else ""
        return f"⚡ {direction}{change_percent}% x {remaining_ticks} tick(s)"

    def _build_prices_text(self, assets, active_events=None):
        active_events = active_events or {}
        lines = []
        for symbol, asset in sorted(assets.items()):
            trend = int(asset.get("trend", 0))
            trend_icon = "↗️" if trend > 0 else "↘️" if trend < 0 else "➡️"
            event_text = ""
            event_data = active_events.get(symbol)
            if isinstance(event_data, dict):
                event_text = f" | {self._format_event_line(event_data)}"
            lines.append(
                f"- `{symbol}` ({asset['kind']}) {asset['name']}: "
                f"{humanize_number(asset['price'])} credits {trend_icon}{event_text}"
            )
        return "Current prices:\n" + "\n".join(lines)

    @staticmethod
    def _roll_random_event(active_events, assets, chance_percent: float):
        chance = min(100.0, max(0.0, float(chance_percent))) / 100.0
        if random.random() >= chance:
            return active_events, None

        available_symbols = [symbol for symbol in assets.keys() if symbol not in active_events]
        if not available_symbols:
            return active_events, None

        selected_symbol = random.choice(available_symbols)
        random_percent = round(random.uniform(3.0, 10.0), 2)
        signed_percent = random_percent if random.random() < 0.5 else random_percent * -1
        active_events[selected_symbol] = {
            "change_per_tick": round(signed_percent / 100.0, 4),
            "remaining_ticks": random.randint(2, 10),
            "source": "random",
        }
        return active_events, (selected_symbol, dict(active_events[selected_symbol]))

    async def _announce_event_message(self, guild_id: int, message: str):
        guild_conf = self.config.guild_from_id(guild_id)
        channel_id = int(await guild_conf.event_announce_channel_id())
        if not channel_id:
            return

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return

        channel = guild.get_channel(channel_id)
        if channel is None:
            await guild_conf.event_announce_channel_id.set(0)
            return

        try:
            await channel.send(message)
        except (discord.Forbidden, discord.HTTPException):
            return

    async def _update_live_prices_message(self, guild_id: int):
        guild_conf = self.config.guild_from_id(guild_id)
        live_data = await guild_conf.live_prices_message()
        channel_id = int(live_data.get("channel_id", 0))
        message_id = int(live_data.get("message_id", 0))
        if not channel_id or not message_id:
            return

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return

        channel = guild.get_channel(channel_id)
        if channel is None:
            await guild_conf.live_prices_message.set({})
            return

        assets = await guild_conf.assets()
        if not assets:
            return

        active_events = await guild_conf.active_events()
        prices_text = self._build_prices_text(assets, active_events)
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            await guild_conf.live_prices_message.set({})
            return
        except (discord.Forbidden, discord.HTTPException):
            return

        await message.edit(content=prices_text)

    @staticmethod
    def _default_asset_behavior(kind: str):
        if kind == "crypto":
            return {
                "volatility": 0.08,
                "risk": 1.2,
                "momentum": 0.68,
                "reversal_accel": 0.08,
                "drift": 0.001,
                "bull_bias": 0.06,
            }
        return {
            "volatility": 0.05,
            "risk": 1.0,
            "momentum": 0.58,
            "reversal_accel": 0.09,
            "drift": 0.0006,
            "bull_bias": 0.05,
        }

    def _behavior_profile(self, kind: str, profile: str):
        defaults = self._default_asset_behavior(kind)
        profiles = {
            "stable": {
                "volatility": 0.035 if kind == "stock" else 0.05,
                "risk": 0.9 if kind == "stock" else 1.0,
                "momentum": 0.55,
                "reversal_accel": 0.1,
                "drift": 0.0004 if kind == "stock" else 0.0007,
                "bull_bias": 0.04,
            },
            "wild": {
                "volatility": 0.085 if kind == "stock" else 0.12,
                "risk": 1.3 if kind == "stock" else 1.55,
                "momentum": 0.7,
                "reversal_accel": 0.07,
                "drift": 0.0008 if kind == "stock" else 0.0012,
                "bull_bias": 0.06,
            },
            "uptrend": {
                "volatility": defaults["volatility"],
                "risk": defaults["risk"],
                "momentum": min(0.9, defaults["momentum"] + 0.05),
                "reversal_accel": defaults["reversal_accel"],
                "drift": defaults["drift"] + 0.0008,
                "bull_bias": min(0.3, defaults["bull_bias"] + 0.08),
            },
            "downtrend": {
                "volatility": defaults["volatility"],
                "risk": defaults["risk"],
                "momentum": min(0.9, defaults["momentum"] + 0.02),
                "reversal_accel": defaults["reversal_accel"],
                "drift": defaults["drift"] - 0.0012,
                "bull_bias": max(-0.3, defaults["bull_bias"] - 0.16),
            },
            "swing": {
                "volatility": 0.06 if kind == "stock" else 0.095,
                "risk": 1.2 if kind == "stock" else 1.35,
                "momentum": 0.52,
                "reversal_accel": 0.14,
                "drift": 0.0,
                "bull_bias": 0.0,
            },
        }
        return profiles.get(profile)

    def _detect_asset_profile(self, kind: str, asset: dict) -> str:
       """Detect which profile an asset matches, or return 'custom' if none match."""
       asset_volatility = round(float(asset.get("volatility", 0.0)), 4)
       asset_risk = round(float(asset.get("risk", 1.0)), 2)
       asset_momentum = round(float(asset.get("momentum", 0.6)), 4)
       asset_reversal_accel = round(float(asset.get("reversal_accel", 0.08)), 4)
       asset_drift = round(float(asset.get("drift", 0.0)), 4)
       asset_bull_bias = round(float(asset.get("bull_bias", 0.05)), 4)

       for profile_name in ["stable", "wild", "uptrend", "downtrend", "swing"]:
           profile_data = self._behavior_profile(kind, profile_name)
           if profile_data is None:
               continue

           prof_volatility = round(float(profile_data.get("volatility", 0.0)), 4)
           prof_risk = round(float(profile_data.get("risk", 1.0)), 2)
           prof_momentum = round(float(profile_data.get("momentum", 0.6)), 4)
           prof_reversal_accel = round(float(profile_data.get("reversal_accel", 0.08)), 4)
           prof_drift = round(float(profile_data.get("drift", 0.0)), 4)
           prof_bull_bias = round(float(profile_data.get("bull_bias", 0.05)), 4)

           if (
               asset_volatility == prof_volatility
               and asset_risk == prof_risk
               and asset_momentum == prof_momentum
               and asset_reversal_accel == prof_reversal_accel
               and asset_drift == prof_drift
               and asset_bull_bias == prof_bull_bias
           ):
               return profile_name

       return "custom"

    async def _process_auto_orders(self, guild_id: int):
        """Process all auto-buy and auto-sell orders for all members in the guild."""
        guild_conf = self.config.guild_from_id(guild_id)
        assets = await guild_conf.assets()
        if not assets:
           return

        all_members = await self.config.all_members(guild_id)
        guild = self.bot.get_guild(guild_id)
        if guild is None:
           return

        for member_id, member_data in all_members.items():
           auto_orders = member_data.get("auto_orders", {})
           if not auto_orders:
               continue

           try:
               member_id_int = int(member_id)
               member = await self.bot.fetch_user(member_id_int)
           except (discord.NotFound, ValueError):
               continue

           member_conf = self.config.member_from_ids(guild_id, int(member_id))
           holdings = await member_conf.holdings()

           for order_id, order in list(auto_orders.items()):
               order_type = order.get("type")
               symbol = order.get("symbol", "").upper()
               target_price = float(order.get("target_price", 0))
               quantity = int(order.get("quantity", 0))

               if symbol not in assets or target_price <= 0 or (quantity <= 0 and quantity != -1):
                   continue

               asset = assets[symbol]
               current_price = float(asset.get("price", 0))

               if order_type == "buy":
                   if current_price <= target_price:
                       total_cost = int(round(current_price * quantity))
                       if total_cost <= 0:
                           total_cost = 1
                       if not await bank.can_spend(member, total_cost):
                           continue
                       await bank.withdraw_credits(member, total_cost)
                       async with member_conf.holdings() as hld, member_conf.cost_basis() as cb:
                           current_amount = int(hld.get(symbol, 0))
                           current_avg_price = float(cb.get(symbol, current_price))
                           new_amount = current_amount + quantity
                           hld[symbol] = new_amount
                           if new_amount > 0:
                               total_cost_basis = (current_amount * current_avg_price) + (quantity * current_price)
                               cb[symbol] = round(total_cost_basis / new_amount, 4)
                       del auto_orders[order_id]

               elif order_type == "sell":
                   if current_price >= target_price:
                       owned_amount = int(holdings.get(symbol, 0))
                       quantity_to_sell = owned_amount if quantity == -1 else quantity
                       if owned_amount >= quantity_to_sell and quantity_to_sell > 0:
                           async with member_conf.holdings() as hld, member_conf.cost_basis() as cb, member_conf.realized_profit() as rp:
                               current_amount = int(hld.get(symbol, 0))
                               avg_buy_price = float(cb.get(symbol, current_price))
                               realized_change = int(round((current_price - avg_buy_price) * quantity_to_sell))
                               previous_realized = int(rp.get(symbol, 0))
                               rp[symbol] = previous_realized + realized_change
                               hld[symbol] = current_amount - quantity_to_sell
                               if hld[symbol] == 0:
                                   del hld[symbol]
                                   if symbol in cb:
                                       del cb[symbol]
                               total_gain = int(round(current_price * quantity_to_sell))
                               if total_gain <= 0:
                                   total_gain = 1
                               await bank.deposit_credits(member, total_gain)
                           del auto_orders[order_id]

           await member_conf.auto_orders.set(auto_orders)

    async def _update_guild_prices(self, guild_id: int):
        await self._ensure_guild_initialized(guild_id)
        guild_conf = self.config.guild_from_id(guild_id)
        assets = await guild_conf.assets()
        if not assets:
            await guild_conf.last_update_ts.set(time.time())
            return

        active_events = await guild_conf.active_events()
        active_events = {
            symbol: event_data
            for symbol, event_data in active_events.items()
            if symbol in assets and isinstance(event_data, dict)
        }
        random_events_enabled = bool(await guild_conf.random_events_enabled())
        random_event_chance_percent = float(await guild_conf.random_event_chance_percent())
        random_event_started = None
        if random_events_enabled:
            active_events, random_event_started = self._roll_random_event(
                active_events, assets, random_event_chance_percent
            )
        if random_event_started is not None:
            started_symbol, started_event_data = random_event_started
            await self._announce_event_message(
                guild_id,
                f"Random event started for `{started_symbol}`: {self._format_event_line(started_event_data)}.",
            )

        updated_assets = {}
        ended_events = []
        for symbol, asset in assets.items():
            event_data = active_events.get(symbol)
            event_change = 0.0
            event_remaining_ticks = 0
            if isinstance(event_data, dict):
                event_change = float(event_data.get("change_per_tick", 0.0))
                event_remaining_ticks = max(0, int(event_data.get("remaining_ticks", 0)))

            current_price = float(asset["price"])
            volatility = float(asset.get("volatility", 0.08))
            risk = max(0.2, float(asset.get("risk", 1.0)))
            momentum = min(0.95, max(0.05, float(asset.get("momentum", 0.6))))
            reversal_accel = min(0.5, max(0.01, float(asset.get("reversal_accel", 0.08))))
            drift = min(0.2, max(-0.2, float(asset.get("drift", 0.0))))
            bull_bias = min(0.4, max(-0.4, float(asset.get("bull_bias", 0.05))))
            min_price = max(1.0, float(asset.get("min_price", 1.0)))
            max_price = max(min_price, float(asset.get("max_price", min_price)))
            trend = int(asset.get("trend", 0))
            trend_streak = max(0, int(asset.get("trend_streak", 0)))

            if trend == 0:
                trend = 1 if random.random() < (0.5 + (bull_bias / 2.0)) else -1
                trend_streak = 0
            else:
                trend_persistence_bias = bull_bias if trend > 0 else -bull_bias
                continue_chance = max(
                    0.05,
                    min(0.98, momentum + trend_persistence_bias - (trend_streak * reversal_accel)),
                )
                if random.random() > continue_chance:
                    trend *= -1
                    trend_streak = 0

            directional_move = random.uniform(volatility * 0.25, volatility) * risk
            if trend_streak >= 2:
                directional_move *= 1.0 + min(0.45, trend_streak * 0.08)

            noise = random.uniform(-volatility * 0.15, volatility * 0.15)
            change = (directional_move * trend) + noise + drift
            if change * trend < 0:
                change = trend * abs(change) * 0.35
            change += event_change
            change = max(-0.95, min(0.95, change))

            new_price = current_price * (1.0 + change)
            clamped_price = max(min_price, min(max_price, new_price))

            updated_asset = dict(asset)
            updated_asset["price"] = round(clamped_price, 2)
            if clamped_price in (min_price, max_price):
                updated_asset["trend"] = trend * -1
                updated_asset["trend_streak"] = 0
            else:
                updated_asset["trend"] = trend
                updated_asset["trend_streak"] = trend_streak + 1
            updated_assets[symbol] = updated_asset

            if symbol in active_events:
                if event_remaining_ticks <= 1:
                    ended_events.append(symbol)
                    del active_events[symbol]
                else:
                    next_event_data = dict(active_events[symbol])
                    next_event_data["remaining_ticks"] = event_remaining_ticks - 1
                    active_events[symbol] = next_event_data

        await guild_conf.assets.set(updated_assets)
        await guild_conf.active_events.set(active_events)
        await self._record_prices_snapshot(guild_conf, updated_assets)
        await guild_conf.last_update_ts.set(time.time())
        if ended_events:
            await self._announce_event_message(
                guild_id,
                "Event ended for: " + ", ".join(f"`{symbol}`" for symbol in sorted(ended_events)) + ".",
            )

    @tasks.loop(minutes=1)
    async def price_updater(self):
        all_guilds = await self.config.all_guilds()
        now = time.time()

        for guild_id, data in all_guilds.items():
           try:
               interval_minutes = int(data.get("update_interval_minutes", 10))
               last_update_ts = float(data.get("last_update_ts", 0.0))
               time_since_update = now - last_update_ts
                
               if time_since_update >= interval_minutes * 60:
                   parsed_guild_id = int(guild_id)
                   await self._process_auto_orders(parsed_guild_id)
                   await self._update_guild_prices(parsed_guild_id)
                   await self._update_live_prices_message(parsed_guild_id)
           except Exception as e:
               print(f"Error in price_updater for guild {guild_id}: {e}")
               import traceback
               traceback.print_exc()

    @price_updater.before_loop
    async def before_price_updater(self):
        await self.bot.wait_until_red_ready()

    @commands.group(case_insensitive=True)
    @commands.guild_only()
    async def market(self, ctx):
        """Fake market game commands."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @market.command(name="help")
    async def market_help(self, ctx):
       """Show all market trading commands."""
       embed = discord.Embed(
           title="Market Trading Commands",
           description="Complete list of all market trading commands",
           color=discord.Color.green()
       )

       # Trading Commands
       embed.add_field(
           name="**Trading**",
           value="`buy <symbol> <qty>` - Buy an asset with credits\n"
                 "`sell <symbol> <qty|all>` - Sell asset or everything\n"
                 "`portfolio [member]` - View holdings and value\n"
                 "`prices` - Show current asset prices\n"
                 "`graph <symbol> [points]` - Show price history graph",
           inline=False
       )

       # Auto-Orders
       embed.add_field(
           name="**Auto-Buy Orders**",
           value="`autobuy set <symbol> <price> <qty>` - Buy when price drops\n"
                 "`autobuy list` - List your auto-buy orders\n"
                 "`autobuy remove <symbol>` - Remove auto-buy orders",
           inline=False
       )

       embed.add_field(
           name="**Auto-Sell Orders**",
           value="`autosell set <symbol> <price> <qty|all>` - Sell when price rises\n"
                 "`autosell list` - List your auto-sell orders\n"
                 "`autosell remove <symbol>` - Remove auto-sell orders",
           inline=False
       )

       # Asset Management (Admin)
       embed.add_field(
           name="**Asset Management** (Admin)",
           value="`asset add <symbol> <kind> <price> <name>` - Add tradable asset\n"
                 "`asset list` - List all assets\n"
                 "`asset info <symbol>` - Show asset details & profile\n"
                 "`asset setprice <symbol> <price>` - Set asset price\n"
                 "`asset setminprice <symbol> <price>` - Set minimum price\n"
                 "`asset setmaxprice <symbol> <price>` - Set maximum price",
           inline=False
       )

       # Behavior Profiles (Admin)
       embed.add_field(
           name="**Behavior Profiles** (Admin)",
           value="`asset setprofile <symbol> <profile>` - Set behavior profile\n"
                 "`asset profiles` - List available profiles\n"
                 "Profiles: `stable`, `wild`, `uptrend`, `downtrend`, `swing`",
           inline=False
       )

       # Price Control (Admin)
       embed.add_field(
           name="**Price Control** (Admin)",
           value="`setdrift <value>` - Set baseline price change (-0.2 to 0.2)\n"
                 "`setbullbias <value>` - Set uptrend preference (-0.4 to 0.4)\n"
                 "`interval <minutes>` - Set price update interval (1-1440)\n"
                 "`update` - Manually trigger price update",
           inline=False
       )

       # Market Events (Admin)
       embed.add_field(
           name="**Market Events** (Admin)",
           value="`event list` - List active events\n"
                 "`event start <symbol> <percent> <ticks>` - Start event\n"
                 "`event clear [symbol]` - Clear event(s)\n"
                 "`event random <enabled>` - Enable/disable random events\n"
                 "`event chance <percent>` - Set random event chance\n"
                 "`event channel` - Set announcement channel",
           inline=False
       )

       embed.set_footer(text="Use !!market <command> help for more info on any command")
       await ctx.send(embed=embed)

    @market.command(name="prices")
    async def market_prices(self, ctx):
        """Show current asset prices."""
        assets = await self._get_assets(ctx.guild)
        if not assets:
            await ctx.send("No assets configured yet.")
            return

        active_events = await self.config.guild(ctx.guild).active_events()
        prices_text = self._build_prices_text(assets, active_events)
        now = time.time()
        channel_key = str(ctx.channel.id)
        cache = await self.config.guild(ctx.guild).prices_cache()
        cache_entry = cache.get(channel_key, {})
        cached_message_id = int(cache_entry.get("message_id", 0))
        cached_ts = float(cache_entry.get("ts", 0.0))

        reused_message = False
        if cached_message_id and (now - cached_ts) <= 300:
            try:
                cached_message = await ctx.channel.fetch_message(cached_message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                reused_message = False
            else:
                if cached_message.author.id == ctx.me.id:
                    await cached_message.edit(content=prices_text)
                    reused_message = True

        if reused_message:
            cache[channel_key] = {"message_id": cached_message_id, "ts": now}
            await self.config.guild(ctx.guild).prices_cache.set(cache)
            return

        sent_message = await ctx.send(prices_text)
        cache[channel_key] = {"message_id": sent_message.id, "ts": now}
        await self.config.guild(ctx.guild).prices_cache.set(cache)

    @market.command(name="buy")
    async def market_buy(self, ctx, symbol: str, quantity: int):
        """Buy an asset with bank credits."""
        if quantity <= 0:
            await ctx.send("Quantity must be at least 1.")
            return

        normalized_symbol = self._normalize_symbol(symbol)
        assets = await self._get_assets(ctx.guild)
        asset = assets.get(normalized_symbol)
        if asset is None:
            await ctx.send(f"Asset `{normalized_symbol}` does not exist.")
            return

        total_cost = int(round(float(asset["price"]) * quantity))
        if total_cost <= 0:
            total_cost = 1

        if not await bank.can_spend(ctx.author, total_cost):
            balance = await bank.get_balance(ctx.author)
            await ctx.send(
                f"You need {humanize_number(total_cost)} credits but only have "
                f"{humanize_number(balance)} credits."
            )
            return

        await bank.withdraw_credits(ctx.author, total_cost)

        fill_price = float(asset["price"])
        member_conf = self.config.member(ctx.author)
        async with member_conf.holdings() as holdings, member_conf.cost_basis() as cost_basis:
            current_amount = int(holdings.get(normalized_symbol, 0))
            current_avg_price = float(cost_basis.get(normalized_symbol, fill_price))
            new_amount = current_amount + quantity
            holdings[normalized_symbol] = new_amount

            if new_amount > 0:
                total_cost_basis = (current_amount * current_avg_price) + (quantity * fill_price)
                cost_basis[normalized_symbol] = round(total_cost_basis / new_amount, 4)

        await ctx.send(
            f"Bought {quantity} `{normalized_symbol}` for {humanize_number(total_cost)} credits."
        )

    @market.command(name="sell")
    async def market_sell(self, ctx, symbol: str, quantity: str = None):
        """Sell an owned asset for bank credits. Use `all` to sell everything."""
        normalized_symbol = self._normalize_symbol(symbol)
        member_conf = self.config.member(ctx.author)
        assets = await self._get_assets(ctx.guild)

        if normalized_symbol == "ALL":
            total_gain = 0
            total_realized_change = 0
            sold_assets = 0
            sold_units = 0

            async with member_conf.holdings() as holdings, member_conf.cost_basis() as cost_basis, member_conf.realized_profit() as realized_profit:
                for held_symbol, held_amount in list(holdings.items()):
                    asset = assets.get(held_symbol)
                    if asset is None:
                        continue

                    quantity_int = int(held_amount)
                    if quantity_int <= 0:
                        continue

                    sell_price = float(asset["price"])
                    gain_for_symbol = int(round(sell_price * quantity_int))
                    if gain_for_symbol <= 0:
                        gain_for_symbol = 1

                    avg_buy_price = float(cost_basis.get(held_symbol, sell_price))
                    realized_for_symbol = int(round((sell_price - avg_buy_price) * quantity_int))
                    previous_realized = int(realized_profit.get(held_symbol, 0))
                    realized_profit[held_symbol] = previous_realized + realized_for_symbol

                    total_gain += gain_for_symbol
                    total_realized_change += realized_for_symbol
                    sold_assets += 1
                    sold_units += quantity_int

                    del holdings[held_symbol]
                    if held_symbol in cost_basis:
                        del cost_basis[held_symbol]

            if sold_assets == 0:
                await ctx.send("You have no tradable holdings to sell.")
                return

            await bank.deposit_credits(ctx.author, total_gain)
            await ctx.send(
                f"Sold all tradable holdings ({sold_units} units across {sold_assets} assets) "
                f"for {humanize_number(total_gain)} credits. "
                f"Realized P/L: {humanize_number(total_realized_change)} credits."
            )
            return

        asset = assets.get(normalized_symbol)
        if asset is None:
            await ctx.send(f"Asset `{normalized_symbol}` does not exist.")
            return

        if quantity is None:
            await ctx.send("Please provide a quantity, or use `all`.")
            return

        quantity_value = quantity.strip().lower()
        quantity_int = 0
        if quantity_value == "all":
            quantity_int = -1
        else:
            try:
                quantity_int = int(quantity_value)
            except ValueError:
                await ctx.send("Quantity must be a number or `all`.")
                return
            if quantity_int <= 0:
                await ctx.send("Quantity must be at least 1.")
                return

        avg_buy_price = float(asset["price"])
        async with member_conf.holdings() as holdings, member_conf.cost_basis() as cost_basis, member_conf.realized_profit() as realized_profit:
            owned_amount = int(holdings.get(normalized_symbol, 0))
            if quantity_int == -1:
                quantity_int = owned_amount

            if owned_amount < quantity_int:
                await ctx.send(f"You only own {owned_amount} `{normalized_symbol}`.")
                return
            if quantity_int <= 0:
                await ctx.send(f"You only own {owned_amount} `{normalized_symbol}`.")
                return

            avg_buy_price = float(cost_basis.get(normalized_symbol, float(asset["price"])))

            holdings[normalized_symbol] = owned_amount - quantity_int
            if holdings[normalized_symbol] == 0:
                del holdings[normalized_symbol]
                if normalized_symbol in cost_basis:
                    del cost_basis[normalized_symbol]

            sell_price = float(asset["price"])
            realized_change = int(round((sell_price - avg_buy_price) * quantity_int))
            previous_realized = int(realized_profit.get(normalized_symbol, 0))
            realized_profit[normalized_symbol] = previous_realized + realized_change

        total_gain = int(round(float(asset["price"]) * quantity_int))
        if total_gain <= 0:
            total_gain = 1

        await bank.deposit_credits(ctx.author, total_gain)
        await ctx.send(
            f"Sold {quantity_int} `{normalized_symbol}` for {humanize_number(total_gain)} credits. "
            f"Realized P/L: {humanize_number(realized_change)} credits."
        )

    @market.command(name="portfolio")
    async def market_portfolio(self, ctx, member: discord.Member = None):
        """Show a member's holdings and estimated value."""
        target = member or ctx.author
        member_conf = self.config.member(target)
        holdings = await member_conf.holdings()
        if not holdings:
            await ctx.send(f"{target.display_name} has no holdings.")
            return

        cost_basis = await member_conf.cost_basis()
        realized_profit = await member_conf.realized_profit()
        assets = await self._get_assets(ctx.guild)
        total_value = 0
        total_cost_basis_value = 0
        total_unrealized = 0
        total_realized = 0
        lines = []

        for symbol, amount in sorted(holdings.items()):
            asset = assets.get(symbol)
            if asset is None:
                lines.append(f"- `{symbol}`: {amount} (delisted)")
                continue

            amount_int = int(amount)
            current_price = float(asset["price"])
            value = int(round(current_price * amount_int))
            total_value += value

            avg_buy_price = cost_basis.get(symbol)
            realized_for_symbol = int(realized_profit.get(symbol, 0))
            total_realized += realized_for_symbol

            if avg_buy_price is None:
                lines.append(
                    f"- `{symbol}`: {amount_int} @ {humanize_number(current_price)} = {humanize_number(value)} | "
                    f"avg buy: n/a | unrealized P/L: n/a | realized P/L: {humanize_number(realized_for_symbol)}"
                )
                continue

            avg_buy_price = float(avg_buy_price)
            basis_value = int(round(avg_buy_price * amount_int))
            unrealized = value - basis_value
            unrealized_percent = 0.0 if basis_value == 0 else (unrealized / basis_value) * 100

            total_cost_basis_value += basis_value
            total_unrealized += unrealized

            lines.append(
                f"- `{symbol}`: {amount_int} @ {humanize_number(current_price)} = {humanize_number(value)} | "
                f"avg buy: {humanize_number(round(avg_buy_price, 2))} | "
                f"unrealized P/L: {humanize_number(unrealized)} ({round(unrealized_percent, 2)}%) | "
                f"realized P/L: {humanize_number(realized_for_symbol)}"
            )

        await ctx.send(
            f"{target.display_name}'s portfolio:\n"
            + "\n".join(lines)
            + f"\nEstimated total value: {humanize_number(total_value)} credits"
            + f"\nTotal cost basis: {humanize_number(total_cost_basis_value)} credits"
            + f"\nTotal unrealized P/L: {humanize_number(total_unrealized)} credits"
            + f"\nTotal realized P/L: {humanize_number(total_realized)} credits"
        )

    @market.group(name="autobuy", case_insensitive=True)
    async def market_autobuy(self, ctx):
       """Manage auto-buy orders that execute when price drops to target."""
       if ctx.invoked_subcommand is None:
           await ctx.send_help()

    @market_autobuy.command(name="set")
    async def market_autobuy_set(self, ctx, symbol: str, target_price: float, quantity: int):
       """Set an auto-buy order. Buys when price drops to or below target."""
       if quantity <= 0:
           await ctx.send("Quantity must be at least 1.")
           return
       if target_price <= 0:
           await ctx.send("Target price must be greater than 0.")
           return

       normalized_symbol = self._normalize_symbol(symbol)
       assets = await self._get_assets(ctx.guild)
       asset = assets.get(normalized_symbol)
       if asset is None:
           await ctx.send(f"Asset `{normalized_symbol}` does not exist.")
           return

       order_id = f"{normalized_symbol}_{ctx.author.id}_{int(time.time() * 1000) % 10000}"
       member_conf = self.config.member(ctx.author)
       async with member_conf.auto_orders() as orders:
           orders[order_id] = {
               "type": "buy",
               "symbol": normalized_symbol,
               "target_price": round(target_price, 2),
               "quantity": quantity,
           }

       await ctx.send(
           f"Auto-buy order set: Buy {quantity} `{normalized_symbol}` when price drops to {humanize_number(round(target_price, 2))} credits."
       )

    @market_autobuy.command(name="list")
    async def market_autobuy_list(self, ctx):
       """List all your active auto-buy orders."""
       member_conf = self.config.member(ctx.author)
       auto_orders = await member_conf.auto_orders()

       buy_orders = [order for order in auto_orders.values() if order.get("type") == "buy"]
       if not buy_orders:
           await ctx.send("You have no active auto-buy orders.")
           return

       lines = []
       for order in buy_orders:
           symbol = order.get("symbol", "?")
           target_price = float(order.get("target_price", 0))
           quantity = int(order.get("quantity", 0))
           lines.append(f"- `{symbol}`: {quantity} units @ {humanize_number(round(target_price, 2))} credits")

       await ctx.send("Your auto-buy orders:\n" + "\n".join(lines))

    @market_autobuy.command(name="remove")
    async def market_autobuy_remove(self, ctx, symbol: str):
       """Remove all auto-buy orders for a symbol."""
       normalized_symbol = self._normalize_symbol(symbol)
       member_conf = self.config.member(ctx.author)

       async with member_conf.auto_orders() as orders:
           removed = False
           for order_id in list(orders.keys()):
               if orders[order_id].get("symbol") == normalized_symbol and orders[order_id].get("type") == "buy":
                   del orders[order_id]
                   removed = True

           if not removed:
               await ctx.send(f"You have no auto-buy orders for `{normalized_symbol}`.")
               return

       await ctx.send(f"Removed all auto-buy orders for `{normalized_symbol}`.")

    @market.group(name="autosell", case_insensitive=True)
    async def market_autosell(self, ctx):
       """Manage auto-sell orders that execute when price rises to target."""
       if ctx.invoked_subcommand is None:
           await ctx.send_help()

    @market_autosell.command(name="set")
    async def market_autosell_set(self, ctx, symbol: str, target_price: float, quantity: str = None):
       """Set an auto-sell order. Sells when price rises to or above target. Use 'all' to sell everything."""
       if target_price <= 0:
           await ctx.send("Target price must be greater than 0.")
           return

       if quantity is None:
           await ctx.send("Please provide a quantity or use `all`.")
           return

       normalized_symbol = self._normalize_symbol(symbol)
       assets = await self._get_assets(ctx.guild)
       asset = assets.get(normalized_symbol)
       if asset is None:
           await ctx.send(f"Asset `{normalized_symbol}` does not exist.")
           return

       member_conf = self.config.member(ctx.author)
       holdings = await member_conf.holdings()
       owned_amount = int(holdings.get(normalized_symbol, 0))

       quantity_value = quantity.strip().lower()
       quantity_int = 0
       if quantity_value == "all":
           quantity_int = -1
       else:
           try:
               quantity_int = int(quantity_value)
           except ValueError:
               await ctx.send("Quantity must be a number or `all`.")
               return
           if quantity_int <= 0:
               await ctx.send("Quantity must be at least 1.")
               return
           if owned_amount < quantity_int:
               await ctx.send(f"You only own {owned_amount} `{normalized_symbol}` but trying to sell {quantity_int}.")
               return

       order_id = f"{normalized_symbol}_{ctx.author.id}_{int(time.time() * 1000) % 10000}"
       async with member_conf.auto_orders() as orders:
           orders[order_id] = {
               "type": "sell",
               "symbol": normalized_symbol,
               "target_price": round(target_price, 2),
               "quantity": quantity_int,
           }

       if quantity_int == -1:
           await ctx.send(
               f"Auto-sell order set: Sell all `{normalized_symbol}` when price rises to {humanize_number(round(target_price, 2))} credits."
           )
       else:
           await ctx.send(
               f"Auto-sell order set: Sell {quantity_int} `{normalized_symbol}` when price rises to {humanize_number(round(target_price, 2))} credits."
           )

    @market_autosell.command(name="list")
    async def market_autosell_list(self, ctx):
       """List all your active auto-sell orders."""
       member_conf = self.config.member(ctx.author)
       auto_orders = await member_conf.auto_orders()

       sell_orders = [order for order in auto_orders.values() if order.get("type") == "sell"]
       if not sell_orders:
           await ctx.send("You have no active auto-sell orders.")
           return

       lines = []
       for order in sell_orders:
           symbol = order.get("symbol", "?")
           target_price = float(order.get("target_price", 0))
           quantity = int(order.get("quantity", 0))
           if quantity == -1:
               lines.append(f"- `{symbol}`: all units @ {humanize_number(round(target_price, 2))} credits")
           else:
               lines.append(f"- `{symbol}`: {quantity} units @ {humanize_number(round(target_price, 2))} credits")

       await ctx.send("Your auto-sell orders:\n" + "\n".join(lines))

    @market_autosell.command(name="remove")
    async def market_autosell_remove(self, ctx, symbol: str):
       """Remove all auto-sell orders for a symbol."""
       normalized_symbol = self._normalize_symbol(symbol)
       member_conf = self.config.member(ctx.author)

       async with member_conf.auto_orders() as orders:
           removed = False
           for order_id in list(orders.keys()):
               if orders[order_id].get("symbol") == normalized_symbol and orders[order_id].get("type") == "sell":
                   del orders[order_id]
                   removed = True

           if not removed:
               await ctx.send(f"You have no auto-sell orders for `{normalized_symbol}`.")
               return

       await ctx.send(f"Removed all auto-sell orders for `{normalized_symbol}`.")

    @market.command(name="graph")
    async def market_graph(self, ctx, symbol: str, points: int = 20):
        """Show price history graph for an asset."""
        if points < 5 or points > 60:
            await ctx.send("Points must be between 5 and 60.")
            return

        normalized_symbol = self._normalize_symbol(symbol)
        assets = await self._get_assets(ctx.guild)
        asset = assets.get(normalized_symbol)
        if asset is None:
            await ctx.send(f"Asset `{normalized_symbol}` does not exist.")
            return

        history = await self.config.guild(ctx.guild).price_history()
        values = list(history.get(normalized_symbol, []))
        if not values:
            values = [float(asset["price"])]
        values = values[-points:]

        sparkline = self._build_sparkline(values)
        first = values[0]
        last = values[-1]
        change = last - first
        change_percent = 0.0 if first == 0 else (change / first) * 100
        direction = "up" if change > 0 else "down" if change < 0 else "flat"

        await ctx.send(
            f"`{normalized_symbol}` ({asset['name']}) last {len(values)} points:\n"
            f"`{sparkline}`\n"
            f"Low: {humanize_number(min(values))} | High: {humanize_number(max(values))}\n"
            f"Start: {humanize_number(round(first, 2))} | Now: {humanize_number(round(last, 2))}\n"
            f"Change: {humanize_number(round(change, 2))} ({round(change_percent, 2)}%) [{direction}]"
        )

    @market.command(name="interval")
    @commands.admin_or_permissions(manage_guild=True)
    async def market_interval(self, ctx, minutes: int):
        """Set automatic price update interval in minutes."""
        if minutes < 1 or minutes > 1440:
            await ctx.send("Interval must be between 1 and 1440 minutes.")
            return

        await self.config.guild(ctx.guild).update_interval_minutes.set(minutes)
        await ctx.send(f"Price update interval set to {minutes} minute(s).")

    @market.command(name="tick")
    @commands.admin_or_permissions(manage_guild=True)
    async def market_tick(self, ctx):
        """Force an immediate price update for this server."""
        try:
            await self._process_auto_orders(ctx.guild.id)
            await self._update_guild_prices(ctx.guild.id)
            await self._update_live_prices_message(ctx.guild.id)
            await ctx.send("✅ Prices updated.")
        except Exception as e:
            await ctx.send(f"❌ Error during price update: {e}")
            import traceback
            traceback.print_exc()

    @market.command(name="debug")
    @commands.admin_or_permissions(manage_guild=True)
    async def market_debug(self, ctx):
        """Show debug info about price update timing."""
        import time
        guild_conf = self.config.guild(ctx.guild)
        interval_minutes = await guild_conf.update_interval_minutes()
        last_update_ts = await guild_conf.last_update_ts()
        now = time.time()
        time_since = now - last_update_ts
        
        await ctx.send(
            f"**Update Debug Info:**\n"
            f"Interval: {interval_minutes} minutes ({interval_minutes * 60} seconds)\n"
            f"Last update: {last_update_ts}\n"
            f"Now: {now}\n"
            f"Time since last update: {time_since:.1f} seconds\n"
            f"Ready for update: {time_since >= interval_minutes * 60}"
        )

    @market.command(name="liveprices")
    @commands.admin_or_permissions(manage_guild=True)
    async def market_liveprices(self, ctx):
        """Post a live prices message that auto-updates every market interval."""
        assets = await self._get_assets(ctx.guild)
        if not assets:
            await ctx.send("No assets configured yet.")
            return

        active_events = await self.config.guild(ctx.guild).active_events()
        prices_text = self._build_prices_text(assets, active_events)
        live_message = await ctx.send(prices_text)
        await self.config.guild(ctx.guild).live_prices_message.set(
            {"channel_id": ctx.channel.id, "message_id": live_message.id}
        )
        await ctx.send("Live prices message created. I will update it every market interval.")

    @market.group(name="event", case_insensitive=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def market_event(self, ctx):
        """Manage temporary market events."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @market_event.command(name="list")
    async def market_event_list(self, ctx):
        """List active market events."""
        active_events = await self.config.guild(ctx.guild).active_events()
        announce_channel_id = int(await self.config.guild(ctx.guild).event_announce_channel_id())
        if not active_events:
            if announce_channel_id:
                await ctx.send(
                    f"There are no active events.\nAnnouncement channel: <#{announce_channel_id}>"
                )
            else:
                await ctx.send("There are no active events.\nAnnouncement channel: not set.")
            return

        lines = []
        for symbol, event_data in sorted(active_events.items()):
            if not isinstance(event_data, dict):
                continue
            lines.append(f"- `{symbol}`: {self._format_event_line(event_data)}")
        if not lines:
            if announce_channel_id:
                await ctx.send(
                    f"There are no active events.\nAnnouncement channel: <#{announce_channel_id}>"
                )
            else:
                await ctx.send("There are no active events.\nAnnouncement channel: not set.")
            return
        announcement_line = (
            f"Announcement channel: <#{announce_channel_id}>"
            if announce_channel_id
            else "Announcement channel: not set."
        )
        await ctx.send("Active events:\n" + "\n".join(lines) + f"\n{announcement_line}")

    @market_event.command(name="channel")
    async def market_event_channel(self, ctx, channel: discord.TextChannel = None):
        """Show or set the channel used for event announcements."""
        guild_conf = self.config.guild(ctx.guild)
        if channel is None:
            channel_id = int(await guild_conf.event_announce_channel_id())
            if not channel_id:
                await ctx.send("No event announcement channel is set.")
                return
            await ctx.send(f"Event announcement channel: <#{channel_id}>")
            return

        await guild_conf.event_announce_channel_id.set(channel.id)
        await ctx.send(f"Event announcements will be posted in {channel.mention}.")

    @market_event.command(name="clearchannel")
    async def market_event_clearchannel(self, ctx):
        """Disable event announcements by clearing the announce channel."""
        await self.config.guild(ctx.guild).event_announce_channel_id.set(0)
        await ctx.send("Event announcement channel cleared.")

    @market_event.command(name="start")
    async def market_event_start(self, ctx, symbol: str, percent_per_tick: float, ticks: int):
        """Start a timed event for an asset (max 10 ticks)."""
        await self._ensure_guild_initialized(ctx.guild.id)
        normalized_symbol = self._normalize_symbol(symbol)
        if percent_per_tick == 0:
            await ctx.send("Percent per tick must not be 0.")
            return
        if percent_per_tick < -50 or percent_per_tick > 50:
            await ctx.send("Percent per tick must be between -50 and 50.")
            return
        if ticks < 1 or ticks > 10:
            await ctx.send("Ticks must be between 1 and 10.")
            return

        async with self.config.guild(ctx.guild).assets() as assets, self.config.guild(
            ctx.guild
        ).active_events() as active_events:
            if normalized_symbol not in assets:
                await ctx.send(f"`{normalized_symbol}` does not exist.")
                return
            active_events[normalized_symbol] = {
                "change_per_tick": round(percent_per_tick / 100.0, 4),
                "remaining_ticks": ticks,
                "source": "manual",
            }

        await ctx.send(
            f"Event started for `{normalized_symbol}`: {round(percent_per_tick, 2)}% per tick for {ticks} tick(s)."
        )
        await self._announce_event_message(
            ctx.guild.id,
            f"Manual event started for `{normalized_symbol}`: {round(percent_per_tick, 2)}% per tick for {ticks} tick(s).",
        )

    @market_event.command(name="clear")
    async def market_event_clear(self, ctx, symbol: str = None):
        """Clear one active event or all active events."""
        async with self.config.guild(ctx.guild).active_events() as active_events:
            if symbol is None:
                active_events.clear()
                await ctx.send("Cleared all active events.")
                return

            normalized_symbol = self._normalize_symbol(symbol)
            if normalized_symbol not in active_events:
                await ctx.send(f"No active event found for `{normalized_symbol}`.")
                return

            del active_events[normalized_symbol]
        await ctx.send(f"Cleared active event for `{normalized_symbol}`.")

    @market_event.command(name="random")
    async def market_event_random(self, ctx, enabled: bool):
        """Enable or disable automatic random events."""
        await self.config.guild(ctx.guild).random_events_enabled.set(enabled)
        state = "enabled" if enabled else "disabled"
        await ctx.send(f"Automatic random events are now {state}.")

    @market_event.command(name="chance")
    async def market_event_chance(self, ctx, percent: float):
        """Set random event chance percent per update tick."""
        if percent < 0 or percent > 100:
            await ctx.send("Chance percent must be between 0 and 100.")
            return
        await self.config.guild(ctx.guild).random_event_chance_percent.set(round(percent, 2))
        await ctx.send(f"Random event chance set to {round(percent, 2)}% per update tick.")

    @market.group(name="asset", case_insensitive=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def market_asset(self, ctx):
        """Manage assets in this server."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @market_asset.command(name="add")
    async def market_asset_add(self, ctx, symbol: str, kind: str, starting_price: float, *, name: str):
        """Add a new tradable asset."""
        await self._ensure_guild_initialized(ctx.guild.id)
        normalized_symbol = self._normalize_symbol(symbol)
        normalized_kind = kind.strip().lower()
        if normalized_kind not in {"crypto", "stock"}:
            await ctx.send("Kind must be either `crypto` or `stock`.")
            return
        if starting_price <= 0:
            await ctx.send("Starting price must be greater than 0.")
            return

        min_price = max(1.0, round(starting_price * 0.05, 2))
        max_price = round(starting_price * 20, 2)
        defaults = self._default_asset_behavior(normalized_kind)

        async with self.config.guild(ctx.guild).assets() as assets:
            if normalized_symbol in assets:
                await ctx.send(f"`{normalized_symbol}` already exists.")
                return

            assets[normalized_symbol] = {
                "name": name.strip(),
                "kind": normalized_kind,
                "price": round(starting_price, 2),
                "min_price": min_price,
                "max_price": max_price,
                "volatility": defaults["volatility"],
                "risk": defaults["risk"],
                "momentum": defaults["momentum"],
                "reversal_accel": defaults["reversal_accel"],
                "drift": defaults["drift"],
                "bull_bias": defaults["bull_bias"],
                "trend": 0,
                "trend_streak": 0,
            }

        await ctx.send(
            f"Added `{normalized_symbol}` ({normalized_kind}) at {humanize_number(round(starting_price, 2))} credits."
        )
        await self._append_price_history(
            self.config.guild(ctx.guild), normalized_symbol, round(starting_price, 2)
        )

    @market_asset.command(name="remove")
    async def market_asset_remove(self, ctx, symbol: str):
        """Remove a tradable asset."""
        await self._ensure_guild_initialized(ctx.guild.id)
        normalized_symbol = self._normalize_symbol(symbol)

        async with self.config.guild(ctx.guild).assets() as assets:
            if normalized_symbol not in assets:
                await ctx.send(f"`{normalized_symbol}` does not exist.")
                return
            del assets[normalized_symbol]

        await ctx.send(f"Removed `{normalized_symbol}`.")

    @market_asset.command(name="list")
    async def market_asset_list(self, ctx):
        """List all tradable assets."""
        assets = await self._get_assets(ctx.guild)
        if not assets:
            await ctx.send("No assets configured.")
            return

        lines = []
        for symbol, asset in sorted(assets.items()):
            volatility_percent = round(float(asset.get("volatility", 0.0)) * 100, 2)
            risk = round(float(asset.get("risk", 1.0)), 2)
            momentum_percent = round(float(asset.get("momentum", 0.6)) * 100, 1)
            drift_percent = round(float(asset.get("drift", 0.0)) * 100, 2)
            bull_bias_percent = round(float(asset.get("bull_bias", 0.05)) * 100, 2)
            lines.append(
                f"- `{symbol}` ({asset['kind']}) {asset['name']}: "
                f"price={humanize_number(asset['price'])}, "
                f"volatility={volatility_percent}%, "
                f"risk={risk}x, "
                f"momentum={momentum_percent}%, "
                f"drift={drift_percent}%, "
                f"bull_bias={bull_bias_percent}%"
            )
        await ctx.send("Assets:\n" + "\n".join(lines))

    @market_asset.command(name="profiles")
    async def market_asset_profiles(self, ctx):
        """Show available behavior profiles for setprofile."""
        await ctx.send(
            "Available profiles: `stable`, `wild`, `uptrend`, `downtrend`, `swing`.\n"
            "Use: `market asset setprofile <symbol> <profile>`"
        )

    @market_asset.command(name="info")
    async def market_asset_info(self, ctx, symbol: str):
        """Show detailed configuration for one asset."""
        assets = await self._get_assets(ctx.guild)
        normalized_symbol = self._normalize_symbol(symbol)
        asset = assets.get(normalized_symbol)
        if asset is None:
            await ctx.send(f"`{normalized_symbol}` does not exist.")
            return

        price = round(float(asset.get("price", 0.0)), 2)
        min_price = round(float(asset.get("min_price", 1.0)), 2)
        max_price = round(float(asset.get("max_price", min_price)), 2)
        volatility_percent = round(float(asset.get("volatility", 0.0)) * 100, 2)
        risk = round(float(asset.get("risk", 1.0)), 2)
        momentum_percent = round(float(asset.get("momentum", 0.6)) * 100, 2)
        reversal_accel_percent = round(float(asset.get("reversal_accel", 0.08)) * 100, 2)
        drift_percent = round(float(asset.get("drift", 0.0)) * 100, 2)
        bull_bias_percent = round(float(asset.get("bull_bias", 0.05)) * 100, 2)
        trend = int(asset.get("trend", 0))
        trend_streak = max(0, int(asset.get("trend_streak", 0)))
        trend_text = "up" if trend > 0 else "down" if trend < 0 else "flat"
        kind = str(asset.get("kind", "stock")).strip().lower()
        profile = self._detect_asset_profile(kind, asset)

        await ctx.send(
            f"`{normalized_symbol}` ({asset.get('kind', 'unknown')}) {asset.get('name', 'Unknown')}:\n"
            f"price={humanize_number(price)}\n"
            f"min_price={humanize_number(min_price)} | max_price={humanize_number(max_price)}\n"
            f"volatility={volatility_percent}% | risk={risk}x | momentum={momentum_percent}%\n"
            f"reversal_accel={reversal_accel_percent}% | drift={drift_percent}% | bull_bias={bull_bias_percent}%\n"
            f"trend={trend_text} | trend_streak={trend_streak} | profile={profile}"
        )

    @market_asset.command(name="setprofile")
    async def market_asset_setprofile(self, ctx, symbol: str, profile: str):
        """Apply a predefined behavior profile to an asset."""
        await self._ensure_guild_initialized(ctx.guild.id)
        normalized_symbol = self._normalize_symbol(symbol)
        normalized_profile = profile.strip().lower()
        profile_aliases = {
            "wilder": "wild",
            "volatile": "wild",
            "up": "uptrend",
            "ups": "uptrend",
            "dip": "downtrend",
            "dips": "downtrend",
            "down": "downtrend",
        }
        normalized_profile = profile_aliases.get(normalized_profile, normalized_profile)

        async with self.config.guild(ctx.guild).assets() as assets:
            asset = assets.get(normalized_symbol)
            if asset is None:
                await ctx.send(f"`{normalized_symbol}` does not exist.")
                return

            kind = str(asset.get("kind", "stock")).strip().lower()
            selected = self._behavior_profile(kind, normalized_profile)
            if selected is None:
                await ctx.send(
                    "Unknown profile. Use one of: `stable`, `wild`, `uptrend`, `downtrend`, `swing`."
                )
                return

            asset["volatility"] = round(float(selected["volatility"]), 4)
            asset["risk"] = round(float(selected["risk"]), 2)
            asset["momentum"] = round(float(selected["momentum"]), 4)
            asset["reversal_accel"] = round(float(selected["reversal_accel"]), 4)
            asset["drift"] = round(float(selected["drift"]), 4)
            asset["bull_bias"] = round(float(selected["bull_bias"]), 4)
            assets[normalized_symbol] = asset

        await ctx.send(
            f"`{normalized_symbol}` profile set to `{normalized_profile}` "
            f"(volatility={round(selected['volatility'] * 100, 2)}%, "
            f"risk={round(selected['risk'], 2)}x, "
            f"momentum={round(selected['momentum'] * 100, 1)}%, "
            f"drift={round(selected['drift'] * 100, 2)}%, "
            f"bull_bias={round(selected['bull_bias'] * 100, 2)}%)."
        )

    @market_asset.command(name="setprice")
    async def market_asset_setprice(self, ctx, symbol: str, new_price: float):
        """Set an asset's current price."""
        await self._ensure_guild_initialized(ctx.guild.id)
        normalized_symbol = self._normalize_symbol(symbol)
        if new_price <= 0:
            await ctx.send("Price must be greater than 0.")
            return

        async with self.config.guild(ctx.guild).assets() as assets:
            asset = assets.get(normalized_symbol)
            if asset is None:
                await ctx.send(f"`{normalized_symbol}` does not exist.")
                return
            asset["price"] = round(new_price, 2)
            asset["min_price"] = min(float(asset.get("min_price", new_price)), round(new_price, 2))
            asset["max_price"] = max(float(asset.get("max_price", new_price)), round(new_price, 2))
            assets[normalized_symbol] = asset

        await ctx.send(f"`{normalized_symbol}` price set to {humanize_number(round(new_price, 2))}.")
        await self._append_price_history(
            self.config.guild(ctx.guild), normalized_symbol, round(new_price, 2)
        )

    @market_asset.command(name="setminprice")
    async def market_asset_setminprice(self, ctx, symbol: str, min_price: float):
        """Set the minimum allowed price for an asset."""
        await self._ensure_guild_initialized(ctx.guild.id)
        normalized_symbol = self._normalize_symbol(symbol)
        if min_price <= 0:
            await ctx.send("Minimum price must be greater than 0.")
            return

        rounded_min_price = round(min_price, 2)
        async with self.config.guild(ctx.guild).assets() as assets:
            asset = assets.get(normalized_symbol)
            if asset is None:
                await ctx.send(f"`{normalized_symbol}` does not exist.")
                return

            current_max_price = round(float(asset.get("max_price", rounded_min_price)), 2)
            if rounded_min_price > current_max_price:
                await ctx.send(
                    f"Minimum price cannot be greater than current max price ({humanize_number(current_max_price)})."
                )
                return

            asset["min_price"] = rounded_min_price
            current_price = round(float(asset.get("price", rounded_min_price)), 2)
            if current_price < rounded_min_price:
                asset["price"] = rounded_min_price
            assets[normalized_symbol] = asset

        await ctx.send(
            f"`{normalized_symbol}` minimum price set to {humanize_number(rounded_min_price)}."
        )

    @market_asset.command(name="setmaxprice")
    async def market_asset_setmaxprice(self, ctx, symbol: str, max_price: float):
        """Set the maximum allowed price for an asset."""
        await self._ensure_guild_initialized(ctx.guild.id)
        normalized_symbol = self._normalize_symbol(symbol)
        if max_price <= 0:
            await ctx.send("Maximum price must be greater than 0.")
            return

        rounded_max_price = round(max_price, 2)
        async with self.config.guild(ctx.guild).assets() as assets:
            asset = assets.get(normalized_symbol)
            if asset is None:
                await ctx.send(f"`{normalized_symbol}` does not exist.")
                return

            current_min_price = round(float(asset.get("min_price", 1.0)), 2)
            if rounded_max_price < current_min_price:
                await ctx.send(
                    f"Maximum price cannot be lower than current min price ({humanize_number(current_min_price)})."
                )
                return

            asset["max_price"] = rounded_max_price
            current_price = round(float(asset.get("price", rounded_max_price)), 2)
            if current_price > rounded_max_price:
                asset["price"] = rounded_max_price
            assets[normalized_symbol] = asset

        await ctx.send(
            f"`{normalized_symbol}` maximum price set to {humanize_number(rounded_max_price)}."
        )

    @market_asset.command(name="setvolatility")
    async def market_asset_setvolatility(self, ctx, symbol: str, percent: float):
        """Set max up/down change per update, in percent."""
        await self._ensure_guild_initialized(ctx.guild.id)
        normalized_symbol = self._normalize_symbol(symbol)
        if percent <= 0 or percent > 100:
            await ctx.send("Volatility percent must be between 0 and 100.")
            return

        async with self.config.guild(ctx.guild).assets() as assets:
            asset = assets.get(normalized_symbol)
            if asset is None:
                await ctx.send(f"`{normalized_symbol}` does not exist.")
                return
            asset["volatility"] = round(percent / 100, 4)
            assets[normalized_symbol] = asset

        await ctx.send(f"`{normalized_symbol}` volatility set to {round(percent, 2)}%.")

    @market_asset.command(name="setrisk")
    async def market_asset_setrisk(self, ctx, symbol: str, risk: float):
        """Set directional movement multiplier (higher = riskier trends)."""
        await self._ensure_guild_initialized(ctx.guild.id)
        normalized_symbol = self._normalize_symbol(symbol)
        if risk < 0.2 or risk > 5:
            await ctx.send("Risk must be between 0.2 and 5.")
            return

        async with self.config.guild(ctx.guild).assets() as assets:
            asset = assets.get(normalized_symbol)
            if asset is None:
                await ctx.send(f"`{normalized_symbol}` does not exist.")
                return
            asset["risk"] = round(risk, 2)
            assets[normalized_symbol] = asset

        await ctx.send(f"`{normalized_symbol}` risk set to {round(risk, 2)}x.")

    @market_asset.command(name="setmomentum")
    async def market_asset_setmomentum(self, ctx, symbol: str, percent: float):
        """Set trend continuation chance. Higher means dips/pumps last longer."""
        await self._ensure_guild_initialized(ctx.guild.id)
        normalized_symbol = self._normalize_symbol(symbol)
        if percent <= 0 or percent >= 100:
            await ctx.send("Momentum percent must be greater than 0 and less than 100.")
            return

        async with self.config.guild(ctx.guild).assets() as assets:
            asset = assets.get(normalized_symbol)
            if asset is None:
                await ctx.send(f"`{normalized_symbol}` does not exist.")
                return
            asset["momentum"] = round(percent / 100, 4)
            assets[normalized_symbol] = asset

        await ctx.send(f"`{normalized_symbol}` momentum set to {round(percent, 2)}%.")

    @market_asset.command(name="setdrift")
    async def market_asset_setdrift(self, ctx, symbol: str, percent: float):
        """Set baseline per-tick drift in percent (positive favors upward movement)."""
        await self._ensure_guild_initialized(ctx.guild.id)
        normalized_symbol = self._normalize_symbol(symbol)
        if percent < -10 or percent > 10:
            await ctx.send("Drift percent must be between -10 and 10.")
            return

        async with self.config.guild(ctx.guild).assets() as assets:
            asset = assets.get(normalized_symbol)
            if asset is None:
                await ctx.send(f"`{normalized_symbol}` does not exist.")
                return
            asset["drift"] = round(percent / 100, 4)
            assets[normalized_symbol] = asset

        await ctx.send(f"`{normalized_symbol}` drift set to {round(percent, 2)}% per update.")

    @market_asset.command(name="setbullbias")
    async def market_asset_setbullbias(self, ctx, symbol: str, percent: float):
        """Set trend bias percent (positive favors uptrends, negative favors downtrends)."""
        await self._ensure_guild_initialized(ctx.guild.id)
        normalized_symbol = self._normalize_symbol(symbol)
        if percent < -40 or percent > 40:
            await ctx.send("Bull bias percent must be between -40 and 40.")
            return

        async with self.config.guild(ctx.guild).assets() as assets:
            asset = assets.get(normalized_symbol)
            if asset is None:
                await ctx.send(f"`{normalized_symbol}` does not exist.")
                return
            asset["bull_bias"] = round(percent / 100, 4)
            assets[normalized_symbol] = asset

        await ctx.send(f"`{normalized_symbol}` bull bias set to {round(percent, 2)}%.")


async def setup(bot):
    await bot.add_cog(MarketTrade(bot))
