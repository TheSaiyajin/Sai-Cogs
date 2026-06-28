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
        )
        self.config.register_member(holdings={}, cost_basis={}, realized_profit={})
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

    def _build_prices_text(self, assets):
        lines = []
        for symbol, asset in sorted(assets.items()):
            trend = int(asset.get("trend", 0))
            trend_icon = "↗️" if trend > 0 else "↘️" if trend < 0 else "➡️"
            lines.append(
                f"- `{symbol}` ({asset['kind']}) {asset['name']}: "
                f"{humanize_number(asset['price'])} credits {trend_icon}"
            )
        return "Current prices:\n" + "\n".join(lines)

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

        prices_text = self._build_prices_text(assets)
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

    async def _update_guild_prices(self, guild_id: int):
        await self._ensure_guild_initialized(guild_id)
        guild_conf = self.config.guild_from_id(guild_id)
        assets = await guild_conf.assets()
        if not assets:
            await guild_conf.last_update_ts.set(time.time())
            return

        updated_assets = {}
        for symbol, asset in assets.items():
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

        await guild_conf.assets.set(updated_assets)
        await self._record_prices_snapshot(guild_conf, updated_assets)
        await guild_conf.last_update_ts.set(time.time())

    @tasks.loop(minutes=1)
    async def price_updater(self):
        all_guilds = await self.config.all_guilds()
        now = time.time()

        for guild_id, data in all_guilds.items():
            interval_minutes = int(data.get("update_interval_minutes", 10))
            last_update_ts = float(data.get("last_update_ts", 0.0))
            if now - last_update_ts >= interval_minutes * 60:
                parsed_guild_id = int(guild_id)
                await self._update_guild_prices(parsed_guild_id)
                await self._update_live_prices_message(parsed_guild_id)

    @price_updater.before_loop
    async def before_price_updater(self):
        await self.bot.wait_until_red_ready()

    @commands.group(case_insensitive=True)
    @commands.guild_only()
    async def market(self, ctx):
        """Fake market game commands."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @market.command(name="prices")
    async def market_prices(self, ctx):
        """Show current asset prices."""
        assets = await self._get_assets(ctx.guild)
        if not assets:
            await ctx.send("No assets configured yet.")
            return

        prices_text = self._build_prices_text(assets)
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
    async def market_sell(self, ctx, symbol: str, quantity: int):
        """Sell an owned asset for bank credits."""
        if quantity <= 0:
            await ctx.send("Quantity must be at least 1.")
            return

        normalized_symbol = self._normalize_symbol(symbol)
        assets = await self._get_assets(ctx.guild)
        asset = assets.get(normalized_symbol)
        if asset is None:
            await ctx.send(f"Asset `{normalized_symbol}` does not exist.")
            return

        member_conf = self.config.member(ctx.author)
        avg_buy_price = float(asset["price"])
        async with member_conf.holdings() as holdings, member_conf.cost_basis() as cost_basis:
            owned_amount = int(holdings.get(normalized_symbol, 0))
            if owned_amount < quantity:
                await ctx.send(f"You only own {owned_amount} `{normalized_symbol}`.")
                return

            avg_buy_price = float(cost_basis.get(normalized_symbol, float(asset["price"])))

            holdings[normalized_symbol] = owned_amount - quantity
            if holdings[normalized_symbol] == 0:
                del holdings[normalized_symbol]
                if normalized_symbol in cost_basis:
                    del cost_basis[normalized_symbol]

        sell_price = float(asset["price"])
        total_gain = int(round(sell_price * quantity))
        if total_gain <= 0:
            total_gain = 1

        realized_change = int(round((sell_price - avg_buy_price) * quantity))
        async with member_conf.realized_profit() as realized_profit:
            previous_realized = int(realized_profit.get(normalized_symbol, 0))
            realized_profit[normalized_symbol] = previous_realized + realized_change

        await bank.deposit_credits(ctx.author, total_gain)
        await ctx.send(
            f"Sold {quantity} `{normalized_symbol}` for {humanize_number(total_gain)} credits. "
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
        await self._update_guild_prices(ctx.guild.id)
        await self._update_live_prices_message(ctx.guild.id)
        await ctx.send("Prices updated.")

    @market.command(name="liveprices")
    @commands.admin_or_permissions(manage_guild=True)
    async def market_liveprices(self, ctx):
        """Post a live prices message that auto-updates every market interval."""
        assets = await self._get_assets(ctx.guild)
        if not assets:
            await ctx.send("No assets configured yet.")
            return

        prices_text = self._build_prices_text(assets)
        live_message = await ctx.send(prices_text)
        await self.config.guild(ctx.guild).live_prices_message.set(
            {"channel_id": ctx.channel.id, "message_id": live_message.id}
        )
        await ctx.send("Live prices message created. I will update it every market interval.")

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

        await ctx.send(
            f"`{normalized_symbol}` ({asset.get('kind', 'unknown')}) {asset.get('name', 'Unknown')}:\n"
            f"price={humanize_number(price)}\n"
            f"min_price={humanize_number(min_price)} | max_price={humanize_number(max_price)}\n"
            f"volatility={volatility_percent}% | risk={risk}x | momentum={momentum_percent}%\n"
            f"reversal_accel={reversal_accel_percent}% | drift={drift_percent}% | bull_bias={bull_bias_percent}%\n"
            f"trend={trend_text} | trend_streak={trend_streak}"
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
