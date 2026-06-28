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
        )
        self.config.register_member(holdings={})
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

        await guild_conf.seeded.set(True)

    async def _get_assets(self, guild):
        await self._ensure_guild_initialized(guild.id)
        return await self.config.guild(guild).assets()

    @staticmethod
    def _default_asset_behavior(kind: str):
        if kind == "crypto":
            return {
                "volatility": 0.08,
                "risk": 1.2,
                "momentum": 0.68,
                "reversal_accel": 0.08,
            }
        return {
            "volatility": 0.05,
            "risk": 1.0,
            "momentum": 0.58,
            "reversal_accel": 0.09,
        }

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
            min_price = max(1.0, float(asset.get("min_price", 1.0)))
            max_price = max(min_price, float(asset.get("max_price", min_price)))
            trend = int(asset.get("trend", 0))
            trend_streak = max(0, int(asset.get("trend_streak", 0)))

            if trend == 0:
                trend = random.choice((-1, 1))
                trend_streak = 0
            else:
                continue_chance = max(0.05, momentum - (trend_streak * reversal_accel))
                if random.random() > continue_chance:
                    trend *= -1
                    trend_streak = 0

            directional_move = random.uniform(volatility * 0.25, volatility) * risk
            if trend_streak >= 2:
                directional_move *= 1.0 + min(0.45, trend_streak * 0.08)

            noise = random.uniform(-volatility * 0.15, volatility * 0.15)
            change = (directional_move * trend) + noise
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
        await guild_conf.last_update_ts.set(time.time())

    @tasks.loop(minutes=1)
    async def price_updater(self):
        all_guilds = await self.config.all_guilds()
        now = time.time()

        for guild_id, data in all_guilds.items():
            interval_minutes = int(data.get("update_interval_minutes", 10))
            last_update_ts = float(data.get("last_update_ts", 0.0))
            if now - last_update_ts >= interval_minutes * 60:
                await self._update_guild_prices(int(guild_id))

    @price_updater.before_loop
    async def before_price_updater(self):
        await self.bot.wait_until_red_ready()

    @commands.group()
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

        lines = []
        for symbol, asset in sorted(assets.items()):
            trend = int(asset.get("trend", 0))
            trend_icon = "↗️" if trend > 0 else "↘️" if trend < 0 else "➡️"
            lines.append(
                f"- `{symbol}` ({asset['kind']}) {asset['name']}: "
                f"{humanize_number(asset['price'])} credits {trend_icon}"
            )
        await ctx.send("Current prices:\n" + "\n".join(lines))

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

        async with self.config.member(ctx.author).holdings() as holdings:
            current_amount = int(holdings.get(normalized_symbol, 0))
            holdings[normalized_symbol] = current_amount + quantity

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

        async with self.config.member(ctx.author).holdings() as holdings:
            owned_amount = int(holdings.get(normalized_symbol, 0))
            if owned_amount < quantity:
                await ctx.send(f"You only own {owned_amount} `{normalized_symbol}`.")
                return

            holdings[normalized_symbol] = owned_amount - quantity
            if holdings[normalized_symbol] == 0:
                del holdings[normalized_symbol]

        total_gain = int(round(float(asset["price"]) * quantity))
        if total_gain <= 0:
            total_gain = 1

        await bank.deposit_credits(ctx.author, total_gain)
        await ctx.send(
            f"Sold {quantity} `{normalized_symbol}` for {humanize_number(total_gain)} credits."
        )

    @market.command(name="portfolio")
    async def market_portfolio(self, ctx, member: discord.Member = None):
        """Show a member's holdings and estimated value."""
        target = member or ctx.author
        holdings = await self.config.member(target).holdings()
        if not holdings:
            await ctx.send(f"{target.display_name} has no holdings.")
            return

        assets = await self._get_assets(ctx.guild)
        total_value = 0
        lines = []

        for symbol, amount in sorted(holdings.items()):
            asset = assets.get(symbol)
            if asset is None:
                lines.append(f"- `{symbol}`: {amount} (delisted)")
                continue

            value = int(round(float(asset["price"]) * int(amount)))
            total_value += value
            lines.append(
                f"- `{symbol}`: {amount} @ {humanize_number(asset['price'])} = {humanize_number(value)}"
            )

        await ctx.send(
            f"{target.display_name}'s portfolio:\n"
            + "\n".join(lines)
            + f"\nEstimated total value: {humanize_number(total_value)} credits"
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
        await ctx.send("Prices updated.")

    @market.group(name="asset")
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
                "trend": 0,
                "trend_streak": 0,
            }

        await ctx.send(
            f"Added `{normalized_symbol}` ({normalized_kind}) at {humanize_number(round(starting_price, 2))} credits."
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
            lines.append(
                f"- `{symbol}` ({asset['kind']}) {asset['name']}: "
                f"price={humanize_number(asset['price'])}, "
                f"volatility={volatility_percent}%, "
                f"risk={risk}x, "
                f"momentum={momentum_percent}%"
            )
        await ctx.send("Assets:\n" + "\n".join(lines))

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


async def setup(bot):
    await bot.add_cog(MarketTrade(bot))
