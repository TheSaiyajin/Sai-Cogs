import asyncio
from io import BytesIO
import random
import time
import traceback

import discord
from redbot.core import Config, bank, commands
from redbot.core.utils.chat_formatting import humanize_number
from discord.ext import tasks
from .events import roll_random_event
from .profiles import (
    behavior_profile,
    detect_asset_profile,
    next_profile,
    profile_transition_window_seconds,
)


class MarketTrade(commands.Cog):
    """A simple in-server fake market for coins and stocks."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=219084771503, force_registration=True)
        self.config.register_guild(
            assets={},
            last_update_ts=0.0,
            seeded=False,
            prices_cache={},
            live_prices_message={},
            price_history={},
            active_events={},
            random_events_enabled=True,
            random_event_chance_percent=0.3,
            event_announce_channel_id=0,
            announce_profile_changes=True,
            buy_fee_rate=0.0,
            sell_fee_rate=0.0,
            trade_limit_value_per_day=0,
            trade_limit_trades_per_day=0,
        )
        self.config.register_member(
            holdings={},
            cost_basis={},
            realized_profit={},
            auto_orders={},
            trade_limit_window_start_ts=0.0,
            trade_limit_reset_day="",
            trade_limit_used_value=0,
            trade_limit_used_trades=0,
        )
        self.price_updater.start()

    def cog_unload(self):
        self.price_updater.cancel()

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return symbol.strip().upper()

    @staticmethod
    def _profile_display_name(profile: str) -> str:
        mapping = {
            "stable": "Stable",
            "uptrend": "Uptrend",
            "downtrend": "Downtrend",
            "swing": "Swing",
            "wild": "Wild",
            "bullrun": "Bull Run",
            "crash": "Crash",
            "recovery": "Recovery",
            "flat": "Flat",
            "custom": "Custom",
        }
        return mapping.get(str(profile).strip().lower(), str(profile).strip().title())

    def _build_default_assets(self):
        now = time.time()
        return {
            "BTC": {
                "name": "Bitcoin",
                "kind": "crypto",
                "price": 1200.0,
                "min_price": 100.0,
                "max_price": 100000.0,
                "volatility": 0.008,
                "risk": 1.0,
                "momentum": 0.53,
                "reversal_accel": 0.12,
                "drift": 0.00008,
                "bull_bias": 0.008,
                "trend": 0,
                "trend_streak": 0,
                "profile": "uptrend",
                "next_profile_change_ts": now + random.randint(2 * 3600, 8 * 3600),
            },
            "ETH": {
                "name": "Ethereum",
                "kind": "crypto",
                "price": 700.0,
                "min_price": 50.0,
                "max_price": 50000.0,
                "volatility": 0.006,
                "risk": 0.9,
                "momentum": 0.50,
                "reversal_accel": 0.14,
                "drift": 0.00003,
                "bull_bias": 0.002,
                "trend": 0,
                "trend_streak": 0,
                "profile": "stable",
                "next_profile_change_ts": now + random.randint(2 * 3600, 8 * 3600),
            },
            "AAPL": {
                "name": "Apple",
                "kind": "stock",
                "price": 350.0,
                "min_price": 10.0,
                "max_price": 10000.0,
                "volatility": 0.006,
                "risk": 0.9,
                "momentum": 0.50,
                "reversal_accel": 0.14,
                "drift": 0.00003,
                "bull_bias": 0.002,
                "trend": 0,
                "trend_streak": 0,
                "profile": "stable",
                "next_profile_change_ts": now + random.randint(2 * 3600, 8 * 3600),
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
    def _parse_graph_window_minutes(window: str):
        token = str(window).strip().lower()
        if not token:
            return None

        multiplier = 1
        if token.endswith("h"):
            multiplier = 60
            token = token[:-1]
        elif token.endswith("m"):
            token = token[:-1]

        try:
            amount = int(token)
        except ValueError:
            return None
        return amount * multiplier

    @staticmethod
    def _compress_graph_values(values, max_points: int = 240):
        if len(values) <= max_points:
            return values
        step = len(values) / float(max_points)
        compressed = []
        idx = 0.0
        while int(idx) < len(values) and len(compressed) < max_points:
            compressed.append(values[int(idx)])
            idx += step
        if compressed[-1] != values[-1]:
            compressed[-1] = values[-1]
        return compressed

    @staticmethod
    def _moving_average(values, window: int = 5):
        if not values:
            return []
        smoothed = []
        for idx in range(len(values)):
            start = max(0, idx - window + 1)
            window_values = values[start : idx + 1]
            smoothed.append(sum(window_values) / len(window_values))
        return smoothed

    @staticmethod
    def _format_time_axis_label(offset_minutes: int):
        if offset_minutes <= 0:
            return "Now"
        if offset_minutes >= 180 and offset_minutes % 60 == 0:
            return f"-{offset_minutes // 60}h"
        return f"-{offset_minutes}m"

    @staticmethod
    def _clamp_fee_rate(rate: float) -> float:
        return max(0.0, min(1.0, float(rate)))

    @staticmethod
    def _calculate_fee(amount: int, fee_rate: float) -> int:
        normalized_amount = max(0, int(amount))
        normalized_rate = MarketTrade._clamp_fee_rate(fee_rate)
        return max(0, int(round(normalized_amount * normalized_rate)))

    async def _get_member_trade_limit_usage(self, member_conf):
        now = time.time()
        current_day = time.strftime("%Y-%m-%d", time.gmtime(now))
        reset_day = str(await member_conf.trade_limit_reset_day())
        window_start = float(await member_conf.trade_limit_window_start_ts())
        used_value = int(await member_conf.trade_limit_used_value())
        used_trades = int(await member_conf.trade_limit_used_trades())
        if reset_day != current_day:
            window_start = now
            used_value = 0
            used_trades = 0
            await member_conf.trade_limit_reset_day.set(current_day)
            await member_conf.trade_limit_window_start_ts.set(window_start)
            await member_conf.trade_limit_used_value.set(used_value)
            await member_conf.trade_limit_used_trades.set(used_trades)
        return window_start, used_value, used_trades

    async def _check_trade_limits(self, guild_conf, member_conf, gross_value: int, trades: int = 1):
        max_value_per_day = int(await guild_conf.trade_limit_value_per_day())
        max_trades_per_day = int(await guild_conf.trade_limit_trades_per_day())
        _window_start, used_value, used_trades = await self._get_member_trade_limit_usage(member_conf)
        next_used_value = used_value + max(0, int(gross_value))
        next_used_trades = used_trades + max(0, int(trades))
        if max_value_per_day > 0 and next_used_value > max_value_per_day:
            remaining_value = max(0, max_value_per_day - used_value)
            return (
                False,
                "Daily trading value limit reached. "
                f"Remaining value today: {humanize_number(remaining_value)} credits.",
            )
        if max_trades_per_day > 0 and next_used_trades > max_trades_per_day:
            remaining_trades = max(0, max_trades_per_day - used_trades)
            return (
                False,
                "Daily trade count limit reached. "
                f"Remaining trades today: {remaining_trades}.",
            )
        return True, None

    async def _consume_trade_limits(self, member_conf, gross_value: int, trades: int = 1):
        _window_start, used_value, used_trades = await self._get_member_trade_limit_usage(member_conf)
        await member_conf.trade_limit_used_value.set(used_value + max(0, int(gross_value)))
        await member_conf.trade_limit_used_trades.set(used_trades + max(0, int(trades)))

    async def _remaining_trades_after_trade(self, guild_conf, member_conf, trades_to_consume: int = 1):
        max_trades_per_day = int(await guild_conf.trade_limit_trades_per_day())
        if max_trades_per_day <= 0:
            return "unlimited"
        _window_start, _used_value, used_trades = await self._get_member_trade_limit_usage(member_conf)
        return str(max(0, max_trades_per_day - (used_trades + max(0, int(trades_to_consume)))))

    async def _confirm_trade_action(
        self,
        ctx,
        *,
        asset_name: str,
        side: str,
        quantity_text: str,
        price_per_unit_text: str,
        total_value: int,
        fee: int,
        final_value: int,
        remaining_trades_after: str,
        timeout_seconds: int = 30,
    ) -> bool:
        embed = discord.Embed(
            title="Confirm Trade",
            color=discord.Color.gold(),
            description=(
                "React with ✅ to confirm or ❌ to cancel.\n"
                f"This request auto-cancels in {timeout_seconds} seconds."
            ),
        )
        embed.add_field(name="Asset", value=asset_name, inline=True)
        embed.add_field(name="Side", value=side.upper(), inline=True)
        embed.add_field(name="Quantity", value=quantity_text, inline=True)
        embed.add_field(name="Price per unit", value=price_per_unit_text, inline=True)
        embed.add_field(name="Total value", value=f"{humanize_number(total_value)} credits", inline=True)
        embed.add_field(name="Fee", value=f"{humanize_number(fee)} credits", inline=True)
        embed.add_field(
            name="Final cost/profit",
            value=f"{humanize_number(final_value)} credits",
            inline=True,
        )
        embed.add_field(
            name="Trades remaining after confirm",
            value=remaining_trades_after,
            inline=True,
        )
        confirmation_message = await ctx.send(embed=embed)
        try:
            await confirmation_message.add_reaction("✅")
            await confirmation_message.add_reaction("❌")
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send("I couldn't add confirmation reactions. Please check my permissions.")
            return False

        def reaction_check(reaction, user):
            return (
                reaction.message.id == confirmation_message.id
                and user.id == ctx.author.id
                and str(reaction.emoji) in {"✅", "❌"}
            )

        try:
            reaction, _user = await self.bot.wait_for(
                "reaction_add",
                check=reaction_check,
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            await ctx.send("Trade confirmation timed out. No trade was executed.")
            return False

        if str(reaction.emoji) != "✅":
            await ctx.send("Trade cancelled.")
            return False
        return True

    async def _confirm_order_setup(self, ctx, *, title: str, lines, timeout_seconds: int = 30) -> bool:
        embed = discord.Embed(
            title=title,
            color=discord.Color.gold(),
            description=(
                "\n".join(lines)
                + "\n\nReact with ✅ to confirm or ❌ to cancel.\n"
                f"This request auto-cancels in {timeout_seconds} seconds."
            ),
        )
        confirmation_message = await ctx.send(embed=embed)
        try:
            await confirmation_message.add_reaction("✅")
            await confirmation_message.add_reaction("❌")
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send("I couldn't add confirmation reactions. Please check my permissions.")
            return False

        def reaction_check(reaction, user):
            return (
                reaction.message.id == confirmation_message.id
                and user.id == ctx.author.id
                and str(reaction.emoji) in {"✅", "❌"}
            )

        try:
            reaction, _user = await self.bot.wait_for(
                "reaction_add",
                check=reaction_check,
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            await ctx.send("Order setup confirmation timed out. No order was created.")
            return False

        if str(reaction.emoji) != "✅":
            await ctx.send("Order setup cancelled.")
            return False
        return True

    @staticmethod
    def _build_graph_image(values, symbol: str, asset_name: str, window_minutes: int):
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib import pyplot as plt
        from matplotlib.ticker import FuncFormatter

        background = "#101318"
        chart_bg = "#151A21"
        panel_bg = "#17202A"
        grid_y = "#445264"
        grid_x = "#344050"
        axis_color = "#A0B2C8"
        text = "#F3F8FF"
        dim_text = "#D5E0F0"
        up_color = "#4CAF50"
        down_color = "#E74C3C"
        neutral_dot = "#DCE6F4"
        first = values[0]
        last = values[-1]
        change = last - first
        change_percent = 0.0 if first == 0 else (change / first) * 100
        direction = "UP" if change >= 0 else "DOWN"
        trend_color = up_color if change >= 0 else down_color
        direction_color = trend_color

        fig = plt.figure(figsize=(12, 7), dpi=120, facecolor=background)
        gs = fig.add_gridspec(
            1,
            2,
            width_ratios=[4.5, 2.0],
            left=0.08,
            right=0.96,
            top=0.82,
            bottom=0.14,
            wspace=0.06,
        )
        ax = fig.add_subplot(gs[0, 0])
        panel = fig.add_subplot(gs[0, 1])
        ax.set_facecolor(chart_bg)
        panel.set_facecolor(panel_bg)

        min_value = min(values)
        max_value = max(values)
        value_range = max_value - min_value
        value_padding = value_range * 0.10 if value_range > 0 else 1
        y_min = min_value - value_padding
        y_max = max_value + value_padding
        ax.set_ylim(y_min, y_max)

        x_values = list(range(len(values)))
        last_index = len(values) - 1
        if len(values) > 1:
            smooth_values = MarketTrade._moving_average(values, window=5)
            ax.fill_between(x_values, values, y2=y_min, color=trend_color, alpha=0.20, zorder=1)
            ax.plot(x_values, values, color=trend_color, linewidth=2.0, alpha=0.45, zorder=2)
            ax.plot(x_values, smooth_values, color=trend_color, linewidth=3.2, alpha=0.95, zorder=3)
        else:
            ax.plot(x_values, values, color=trend_color, linewidth=3.2, alpha=0.95, zorder=3)

        ax.axhline(first, color="#C5CDD8", linestyle="--", linewidth=1.2, alpha=0.55, zorder=1, dashes=(4, 4))

        ax.scatter(0, values[0], s=36, color=neutral_dot, edgecolors="#0D1117", linewidths=0.8, zorder=4)
        ax.scatter(last_index, values[-1], s=96, color=trend_color, edgecolors="#F3F8FF", linewidths=1.3, zorder=5)

        high_idx = max(range(len(values)), key=lambda idx: values[idx])
        low_idx = min(range(len(values)), key=lambda idx: values[idx])
        ax.scatter(high_idx, values[high_idx], s=32, color=neutral_dot, edgecolors="#0D1117", linewidths=0.8, zorder=4)
        ax.scatter(low_idx, values[low_idx], s=32, color=neutral_dot, edgecolors="#0D1117", linewidths=0.8, zorder=4)

        x_offset = max(1, len(values) // 35)
        y_offset = (y_max - y_min) * 0.04
        if high_idx >= len(values) - x_offset:
            high_x, high_ha = high_idx - x_offset, "right"
        else:
            high_x, high_ha = high_idx + x_offset, "left"
        if low_idx >= len(values) - x_offset:
            low_x, low_ha = low_idx - x_offset, "right"
        else:
            low_x, low_ha = low_idx + x_offset, "left"
        high_y = min(values[high_idx] + y_offset, y_max - y_offset * 0.7)
        low_y = max(values[low_idx] - y_offset, y_min + y_offset * 0.7)
        ax.text(high_x, high_y, "High", color=dim_text, fontsize=11, ha=high_ha, va="bottom", clip_on=True, zorder=6)
        ax.text(low_x, low_y, "Low", color=dim_text, fontsize=11, ha=low_ha, va="top", clip_on=True, zorder=6)

        ax.grid(True, axis="y", color=grid_y, alpha=0.65, linewidth=0.9)
        ax.grid(True, axis="x", color=grid_x, alpha=0.45, linewidth=0.8)
        for spine in ax.spines.values():
            spine.set_color(axis_color)
            spine.set_linewidth(1.1)
        ax.tick_params(axis="both", colors=dim_text, labelsize=11, pad=8)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda val, _: humanize_number(round(val, 2))))
        ax.set_xlim(0, max(1, last_index))
        if last_index > 0:
            x_ticks = [0, last_index // 2, last_index]
            x_tick_labels = [
                MarketTrade._format_time_axis_label(window_minutes),
                MarketTrade._format_time_axis_label(window_minutes // 2),
                "Now",
            ]
        else:
            x_ticks = [0]
            x_tick_labels = ["Now"]
        ax.set_xticks(x_ticks)
        ax.set_xticklabels(x_tick_labels)

        fig.text(0.06, 0.94, f"{symbol} ({asset_name})", fontsize=26, color=text, ha="left", va="top")
        fig.text(
            0.06,
            0.89,
            f"Last {window_minutes} minute(s) - {len(values)} points",
            fontsize=15,
            color=dim_text,
            ha="left",
            va="top",
        )

        panel.set_xticks([])
        panel.set_yticks([])
        for spine in panel.spines.values():
            spine.set_visible(False)

        stats = [
            ("START", humanize_number(round(first, 2)), dim_text),
            ("NOW", humanize_number(round(last, 2)), dim_text),
            ("CHANGE", f"{humanize_number(round(change, 2))} ({round(change_percent, 2)}%)", direction_color),
            ("LOW", humanize_number(round(min(values), 2)), dim_text),
            ("HIGH", humanize_number(round(max(values), 2)), dim_text),
            ("TREND", direction, direction_color),
        ]
        stat_y = 0.92
        stat_step = 0.155
        for index, (label, value, color) in enumerate(stats):
            panel.text(0.08, stat_y, label, fontsize=13, color=dim_text, transform=panel.transAxes, va="top")
            panel.text(0.08, stat_y - 0.065, value, fontsize=20, color=color, transform=panel.transAxes, va="top")
            if index in (1, 3):
                divider_y = stat_y - 0.12
                panel.plot([0.08, 0.92], [divider_y, divider_y], transform=panel.transAxes, color=grid_y, linewidth=1)
            stat_y -= stat_step

        output = BytesIO()
        fig.savefig(output, format="png", dpi=120, facecolor=fig.get_facecolor())
        plt.close(fig)
        output.seek(0)
        return output

    async def _append_price_history(self, guild_conf, symbol: str, price: float):
        async with guild_conf.price_history() as history:
            symbol_history = list(history.get(symbol, []))
            symbol_history.append(round(float(price), 2))
            history[symbol] = symbol_history[-1440:]

    async def _record_prices_snapshot(self, guild_conf, assets):
        for symbol, asset in assets.items():
            await self._append_price_history(guild_conf, symbol, float(asset["price"]))

    @staticmethod
    def _format_event_line(event_data):
        return "⚡"

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
        return roll_random_event(active_events, assets, chance_percent)

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

    def _behavior_profile(self, profile: str):
        return behavior_profile(profile)

    @staticmethod
    def _profile_transition_window_seconds():
        return profile_transition_window_seconds()

    def _next_profile(self, current_profile: str):
        return next_profile(current_profile)

    def _apply_profile_to_asset(self, asset: dict, profile_name: str, now_ts: float):
        profile_data = self._behavior_profile(profile_name)
        if profile_data is None:
            return dict(asset)

        updated_asset = dict(asset)
        updated_asset["volatility"] = round(float(profile_data["volatility"]), 4)
        updated_asset["risk"] = round(float(profile_data["risk"]), 2)
        updated_asset["momentum"] = round(float(profile_data["momentum"]), 4)
        updated_asset["reversal_accel"] = round(float(profile_data["reversal_accel"]), 4)
        updated_asset["drift"] = round(float(profile_data["drift"]), 5)
        updated_asset["bull_bias"] = round(float(profile_data["bull_bias"]), 4)
        updated_asset["profile"] = profile_name
        updated_asset["next_profile_change_ts"] = now_ts + self._profile_transition_window_seconds()
        # Reset trend state on profile switch so prior long streaks do not leak into the new regime.
        # The next tick will derive direction naturally from the new profile's bull_bias/momentum.
        updated_asset["trend"] = 0
        updated_asset["trend_streak"] = 0
        return updated_asset

    def _detect_asset_profile(self, asset: dict) -> str:
        return detect_asset_profile(asset)

    async def _process_auto_orders_debug(self, guild_id: int):
        """Debug version of _process_auto_orders that returns detailed info."""
        debug_lines = []
        try:
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return ["Guild not found"]
            
            guild_conf = self.config.guild_from_id(guild_id)
            assets = await guild_conf.assets()
            
            if not assets:
                return ["No assets configured"]
            
            all_members = await self.config.all_members(guild)
            if not all_members:
                return ["No members found"]
            
            debug_lines.append(f"**Found {len(all_members)} members, {len(assets)} assets**")
            debug_lines.append(f"Assets: {list(assets.keys())}\n")
            
            member_with_orders = 0
            total_orders = 0
            
            for member_id, member_data in all_members.items():
                auto_orders = member_data.get("auto_orders", {})
                if not auto_orders:
                    continue
                
                member_with_orders += 1
                total_orders += len(auto_orders)
                
                debug_lines.append(f"**Member {member_id}:** {len(auto_orders)} orders")
                
                try:
                    member_id_int = int(member_id)
                    member = await self.bot.fetch_user(member_id_int)
                    debug_lines.append(f"  ✓ User fetched: {member}")
                except Exception as e:
                    debug_lines.append(f"  ✗ Failed to fetch user: {e}")
                    continue
                
                member_conf = self.config.member_from_ids(guild_id, member_id_int)
                holdings = await member_conf.holdings()
                
                for order_id, order in list(auto_orders.items()):
                    order_type = order.get("type")
                    symbol = order.get("symbol", "").upper()
                    target_price = float(order.get("target_price", 0))
                    quantity = int(order.get("quantity", 0))
                    
                    debug_lines.append(f"  Order: {order_id}")
                    debug_lines.append(f"    Type: {order_type}, Symbol: {symbol}, Target: {target_price}, Qty: {quantity}")
                    
                    if symbol not in assets:
                        debug_lines.append(f"    ✗ Symbol not in assets")
                        continue
                    
                    asset = assets[symbol]
                    current_price = float(asset.get("price", 0))
                    owned = int(holdings.get(symbol, 0))
                    
                    debug_lines.append(f"    Price: {current_price}, Owned: {owned}")
                    
                    if order_type == "sell":
                        meets_price = current_price >= target_price
                        qty_to_sell = owned if quantity == -1 else quantity
                        has_holdings = owned >= qty_to_sell and qty_to_sell > 0
                        debug_lines.append(f"    Sell check: price_ok={meets_price}, has_holdings={has_holdings}")
                        if meets_price and has_holdings:
                            debug_lines.append(f"    ✓ SHOULD EXECUTE THIS ORDER!")
                
            debug_lines.append(f"\n**Summary:** {member_with_orders} members with orders, {total_orders} total orders")
            
            return debug_lines
            
        except Exception as e:
            return [f"Error: {e}\n```{traceback.format_exc()}```"]

    async def _process_auto_orders(self, guild_id: int):
        """Process all auto-buy and auto-sell orders for all members in the guild."""
        execution_log = []
        try:
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return
            
            guild_conf = self.config.guild_from_id(guild_id)
            assets = await guild_conf.assets()
            if not assets:
                return
            buy_fee_rate = float(await guild_conf.buy_fee_rate())
            sell_fee_rate = float(await guild_conf.sell_fee_rate())

            all_members = await self.config.all_members(guild)
            if not all_members:
                return

            for member_id, member_data in all_members.items():
                auto_orders = member_data.get("auto_orders", {})
                if not auto_orders:
                    continue

                try:
                    member_id_int = int(member_id)
                    # Get the Member object from the guild for bank operations
                    member = guild.get_member(member_id_int)
                    if member is None:
                        continue
                except (ValueError, TypeError):
                    continue

                member_conf = self.config.member_from_ids(guild_id, member_id_int)
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
                            base_cost = int(round(current_price * quantity))
                            if base_cost <= 0:
                                base_cost = 1
                            buy_fee = self._calculate_fee(base_cost, buy_fee_rate)
                            total_cost = base_cost + buy_fee
                            limits_ok, _limits_message = await self._check_trade_limits(
                                guild_conf, member_conf, base_cost, trades=1
                            )
                            if not limits_ok:
                                execution_log.append(
                                    f"SKIP BUY {symbol}: trade limit reached for {member.display_name}"
                                )
                                continue
                            if not await bank.can_spend(member, total_cost):
                                continue
                            await bank.withdraw_credits(member, total_cost)
                            async with member_conf.holdings() as hld, member_conf.cost_basis() as cb:
                                current_amount = int(hld.get(symbol, 0))
                                effective_fill_price = total_cost / quantity
                                current_avg_price = float(cb.get(symbol, effective_fill_price))
                                new_amount = current_amount + quantity
                                hld[symbol] = new_amount
                                if new_amount > 0:
                                    total_cost_basis = (current_amount * current_avg_price) + (quantity * effective_fill_price)
                                    cb[symbol] = round(total_cost_basis / new_amount, 4)
                            await self._consume_trade_limits(member_conf, base_cost, trades=1)
                            del auto_orders[order_id]
                            execution_log.append(
                                f"BUY: {quantity} {symbol} @ {current_price} | total={total_cost} fee={buy_fee}"
                            )
                            try:
                                if buy_fee > 0:
                                    await member.send(
                                        f"✅ **Auto-Buy Order Executed!**\n"
                                        f"Bought {quantity} `{symbol}` at {humanize_number(round(current_price, 2))} credits each\n"
                                        f"Base cost: {humanize_number(base_cost)} credits\n"
                                        f"Fee: {humanize_number(buy_fee)} credits ({round(buy_fee_rate * 100, 2)}%)\n"
                                        f"Total cost: {humanize_number(total_cost)} credits"
                                    )
                                else:
                                    await member.send(
                                        f"✅ **Auto-Buy Order Executed!**\n"
                                        f"Bought {quantity} `{symbol}` at {humanize_number(round(current_price, 2))} credits each\n"
                                        f"Total cost: {humanize_number(total_cost)} credits"
                                    )
                            except (discord.Forbidden, discord.HTTPException):
                                pass

                    elif order_type == "sell":
                        if current_price >= target_price:
                            current_holdings = await member_conf.holdings()
                            owned_amount = int(current_holdings.get(symbol, 0))
                            quantity_to_sell = owned_amount if quantity == -1 else quantity
                            if owned_amount >= quantity_to_sell and quantity_to_sell > 0:
                                gross_gain = int(round(current_price * quantity_to_sell))
                                if gross_gain <= 0:
                                    gross_gain = 1
                                sell_fee = self._calculate_fee(gross_gain, sell_fee_rate)
                                total_gain = max(0, gross_gain - sell_fee)
                                limits_ok, _limits_message = await self._check_trade_limits(
                                    guild_conf, member_conf, gross_gain, trades=1
                                )
                                if not limits_ok:
                                    execution_log.append(
                                        f"SKIP SELL {symbol}: trade limit reached for {member.display_name}"
                                    )
                                    continue
                                
                                async with member_conf.holdings() as hld, member_conf.cost_basis() as cb, member_conf.realized_profit() as rp:
                                    current_amount = int(hld.get(symbol, 0))
                                    avg_buy_price = float(cb.get(symbol, current_price))
                                    realized_change = int(round(total_gain - (avg_buy_price * quantity_to_sell)))
                                    previous_realized = int(rp.get(symbol, 0))
                                    rp[symbol] = previous_realized + realized_change
                                    hld[symbol] = current_amount - quantity_to_sell
                                    if hld[symbol] == 0:
                                        del hld[symbol]
                                        if symbol in cb:
                                            del cb[symbol]
                                await self._consume_trade_limits(member_conf, gross_gain, trades=1)
                                
                                # Deposit credits OUTSIDE the context manager to ensure it persists
                                try:
                                    if total_gain > 0:
                                        await bank.deposit_credits(member, total_gain)
                                except Exception as e:
                                    execution_log.append(f"ERROR SELL {symbol}: Failed to deposit {total_gain} credits - {e}")
                                
                                del auto_orders[order_id]
                                execution_log.append(
                                    f"SELL: {quantity_to_sell} {symbol} @ {current_price} | net={total_gain} fee={sell_fee}"
                                )
                                try:
                                    profit_loss_text = (
                                        f"+{humanize_number(realized_change)}"
                                        if realized_change > 0
                                        else humanize_number(realized_change)
                                    )
                                    if sell_fee > 0:
                                        await member.send(
                                            f"✅ **Auto-Sell Order Executed!**\n"
                                            f"Sold {quantity_to_sell} `{symbol}` at {humanize_number(round(current_price, 2))} credits each\n"
                                            f"Gross: {humanize_number(gross_gain)} credits\n"
                                            f"Fee: {humanize_number(sell_fee)} credits ({round(sell_fee_rate * 100, 2)}%)\n"
                                            f"Net gain: {humanize_number(total_gain)} credits\n"
                                            f"Profit/Loss: {profit_loss_text} credits"
                                        )
                                    else:
                                        await member.send(
                                            f"✅ **Auto-Sell Order Executed!**\n"
                                            f"Sold {quantity_to_sell} `{symbol}` at {humanize_number(round(current_price, 2))} credits each\n"
                                            f"Total gain: {humanize_number(total_gain)} credits\n"
                                            f"Profit/Loss: {profit_loss_text} credits"
                                        )
                                except (discord.Forbidden, discord.HTTPException):
                                    pass

                await member_conf.auto_orders.set(auto_orders)
        except Exception as e:
            print(f"Error in _process_auto_orders: {e}")
            traceback.print_exc()
        
        return execution_log

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
            started_symbol, _started_event_data = random_event_started
            await self._announce_event_message(
                guild_id,
                f"An event is now happening for `{started_symbol}`.",
            )

        updated_assets = {}
        ended_events = []
        profile_transitions = []
        now_ts = time.time()
        for symbol, asset in assets.items():
            working_asset = dict(asset)
            current_profile = self._detect_asset_profile(working_asset)
            if current_profile != "custom":
                if not float(working_asset.get("next_profile_change_ts", 0.0)):
                    working_asset["next_profile_change_ts"] = now_ts + self._profile_transition_window_seconds()
                    if "profile" not in working_asset:
                        working_asset["profile"] = current_profile
                elif now_ts >= float(working_asset.get("next_profile_change_ts", 0.0)):
                    next_profile = self._next_profile(current_profile)
                    if next_profile != current_profile:
                        working_asset = self._apply_profile_to_asset(working_asset, next_profile, now_ts)
                        profile_transitions.append((symbol, current_profile, next_profile))
                    else:
                        working_asset["profile"] = current_profile
                        working_asset["next_profile_change_ts"] = now_ts + self._profile_transition_window_seconds()

            event_data = active_events.get(symbol)
            event_change = 0.0
            event_remaining_ticks = 0
            if isinstance(event_data, dict):
                event_change = float(event_data.get("change_per_tick", 0.0))
                event_remaining_ticks = max(0, int(event_data.get("remaining_ticks", 0)))

            current_price = float(working_asset["price"])
            volatility = float(working_asset.get("volatility", 0.08))
            risk = max(0.2, float(working_asset.get("risk", 1.0)))
            momentum = min(0.95, max(0.05, float(working_asset.get("momentum", 0.6))))
            reversal_accel = min(0.5, max(0.01, float(working_asset.get("reversal_accel", 0.08))))
            drift = min(0.2, max(-0.2, float(working_asset.get("drift", 0.0))))
            bull_bias = min(0.4, max(-0.4, float(working_asset.get("bull_bias", 0.05))))
            min_price = max(1.0, float(working_asset.get("min_price", 1.0)))
            max_price = max(min_price, float(working_asset.get("max_price", min_price)))
            trend = int(working_asset.get("trend", 0))
            trend_streak = max(0, int(working_asset.get("trend_streak", 0)))

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

            updated_asset = dict(working_asset)
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
        if profile_transitions:
            lines = [
                f"{symbol}: {self._profile_display_name(old_profile)} → {self._profile_display_name(new_profile)}"
                for symbol, old_profile, new_profile in profile_transitions[:20]
            ]
            announce_profile_changes = bool(await guild_conf.announce_profile_changes())
            if announce_profile_changes:
                await self._announce_event_message(
                    guild_id,
                    "📈 Market Profile Changes\n\n" + "\n".join(lines),
                )

    @tasks.loop(minutes=1)
    async def price_updater(self):
        all_guilds = await self.config.all_guilds()

        for guild_id in all_guilds:
            try:
                parsed_guild_id = int(guild_id)
                await self._process_auto_orders(parsed_guild_id)
                await self._update_guild_prices(parsed_guild_id)
                await self._update_live_prices_message(parsed_guild_id)
            except Exception as e:
                print(f"Error in price_updater for guild {guild_id}: {e}")
                traceback.print_exc()

    @price_updater.before_loop
    async def before_price_updater(self):
        await self.bot.wait_until_red_ready()

    @commands.group(case_insensitive=True, aliases=["mt"])
    @commands.guild_only()
    async def market(self, ctx):
        """Fake market game commands."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @market.command(name="help", aliases=["commands", "cmds"])
    async def market_help(self, ctx):
       """Show all market trading commands."""
       embed = discord.Embed(
           title="Market Trading Commands",
           description="Complete list of all market trading commands",
           color=discord.Color.green()
       )
       embed.add_field(
           name="**Aliases**",
           value="`market|mt`, `buy|b`, `sell|s`, `prices|price|pr`, `portfolio|pf|port`, `graph|chart|g`,\n"
                 "`autobuy|ab`, `autosell|as`, `set|create|add`, `list|ls`, `remove|rm|del`",
           inline=False
       )

       # Trading Commands
       embed.add_field(
           name="**Trading**",
           value="`buy <symbol> <qty>` - Buy an asset with credits\n"
                 "`sell <symbol> <qty|all>` - Sell asset or everything\n"
                 "`portfolio [member]` - View holdings and value\n"
                 "`prices` - Show current asset prices\n"
                 "`graph <symbol> [window]` - Show price graph (`30m`, `6h`, max `24h`)",
           inline=False
       )

       # Auto-Orders
       embed.add_field(
           name="**Auto-Buy Orders**",
           value="`autobuy set <symbol> <price> <qty>` - Buy when price drops\n"
                 "`autobuy list` - List your auto-buy orders\n"
                 "(order setup requires ✅/❌ confirmation)\n"
                 "`autobuy remove <symbol>` - Remove auto-buy orders",
           inline=False
       )

       embed.add_field(
           name="**Auto-Sell Orders**",
           value="`autosell set <symbol> <price> <qty|all>` - Sell when price rises\n"
                 "`autosell list` - List your auto-sell orders\n"
                 "(order setup requires ✅/❌ confirmation)\n"
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
                 "`cycle info <symbol>` - Show current cycle profile and next shift\n"
                "`cycle announce <true|false>` - Toggle profile change announcements\n"
                "Profiles: `stable`, `uptrend`, `downtrend`, `swing`, `wild`, `bullrun`, `crash`, `recovery`, `flat`",
           inline=False
       )

       # Price Control (Admin)
       embed.add_field(
           name="**Price Control** (Admin)",
           value="`setdrift <value>` - Set baseline price change (-0.2 to 0.2)\n"
                 "`setbullbias <value>` - Set uptrend preference (-0.4 to 0.4)\n"
                 "`fees show` - Show configured trading fees\n"
                 "`fees buy <percent>` - Set buy fee percent\n"
                 "`fees sell <percent>` - Set sell fee percent\n"
                 "`limits show` - Show configured trade limits\n"
                 "`limits value <credits>` - Set daily traded value limit (0 = unlimited)\n"
                 "`limits usage [member]` - Show daily usage counters\n"
                 "`limits trades <count>` - Set daily trade count limit (0 = unlimited)\n"
                 "`limits reset <member>` - Reset a member usage counters\n"
                 "`tick` - Manually trigger 1 price update\n"
                 "`ticks <count>` - Run many price updates at once (testing)",
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
                 "`event channel [#channel]` - Show/set announcement channel\n"
                 "`event clearchannel` - Clear announcement channel",
           inline=False
       )

       embed.set_footer(text="Use [p]market <command> help for more info on any command")
       await ctx.send(embed=embed)

    @market.group(name="fees", case_insensitive=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def market_fees(self, ctx):
        """Configure trading fees for buys and sells."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @market_fees.command(name="show")
    async def market_fees_show(self, ctx):
        """Show configured buy and sell fee percentages."""
        guild_conf = self.config.guild(ctx.guild)
        buy_fee_rate = float(await guild_conf.buy_fee_rate())
        sell_fee_rate = float(await guild_conf.sell_fee_rate())
        await ctx.send(
            f"Trading fees:\n"
            f"- Buy fee: {round(buy_fee_rate * 100, 2)}%\n"
            f"- Sell fee: {round(sell_fee_rate * 100, 2)}%"
        )

    @market_fees.command(name="buy")
    async def market_fees_buy(self, ctx, percent: float):
        """Set buy fee percent (0-100)."""
        if percent < 0 or percent > 100:
            await ctx.send("Buy fee percent must be between 0 and 100.")
            return
        buy_fee_rate = round(percent / 100.0, 6)
        await self.config.guild(ctx.guild).buy_fee_rate.set(buy_fee_rate)
        await ctx.send(f"Buy fee set to {round(percent, 2)}%.")

    @market_fees.command(name="sell")
    async def market_fees_sell(self, ctx, percent: float):
        """Set sell fee percent (0-100)."""
        if percent < 0 or percent > 100:
            await ctx.send("Sell fee percent must be between 0 and 100.")
            return
        sell_fee_rate = round(percent / 100.0, 6)
        await self.config.guild(ctx.guild).sell_fee_rate.set(sell_fee_rate)
        await ctx.send(f"Sell fee set to {round(percent, 2)}%.")

    @market.group(name="limits", case_insensitive=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def market_limits(self, ctx):
        """Configure daily trading limits."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @market_limits.command(name="show")
    async def market_limits_show(self, ctx):
        """Show configured daily trading limits."""
        guild_conf = self.config.guild(ctx.guild)
        limit_value = int(await guild_conf.trade_limit_value_per_day())
        limit_trades = int(await guild_conf.trade_limit_trades_per_day())
        limit_value_text = "unlimited" if limit_value <= 0 else f"{humanize_number(limit_value)} credits/day"
        limit_trades_text = "unlimited" if limit_trades <= 0 else f"{limit_trades} trades/day"
        await ctx.send(
            "Trading limits:\n"
            f"- Daily traded value limit: {limit_value_text}\n"
            f"- Daily trade count limit: {limit_trades_text}"
        )

    @market_limits.command(name="value")
    async def market_limits_value(self, ctx, credits: int):
        """Set daily traded value limit in credits (0 = unlimited)."""
        if credits < 0:
            await ctx.send("Daily traded value limit must be 0 or greater.")
            return
        await self.config.guild(ctx.guild).trade_limit_value_per_day.set(int(credits))
        if credits == 0:
            await ctx.send("Daily traded value limit set to unlimited.")
            return
        await ctx.send(f"Daily traded value limit set to {humanize_number(credits)} credits.")

    @market_limits.command(name="trades")
    async def market_limits_trades(self, ctx, count: int):
        """Set daily trade count limit (0 = unlimited)."""
        if count < 0:
            await ctx.send("Daily trade count limit must be 0 or greater.")
            return
        await self.config.guild(ctx.guild).trade_limit_trades_per_day.set(int(count))
        if count == 0:
            await ctx.send("Daily trade count limit set to unlimited.")
            return
        await ctx.send(f"Daily trade count limit set to {count} trades per day.")

    @market_limits.command(name="usage")
    async def market_limits_usage(self, ctx, member: discord.Member = None):
        """Show a member's current daily trading limit usage."""
        target = member or ctx.author
        member_conf = self.config.member(target)
        _window_start, used_value, used_trades = await self._get_member_trade_limit_usage(member_conf)
        now_gmt = time.gmtime()
        seconds_since_midnight = (now_gmt.tm_hour * 3600) + (now_gmt.tm_min * 60) + now_gmt.tm_sec
        reset_in_seconds = max(0, 86400 - seconds_since_midnight)
        hours = reset_in_seconds // 3600
        minutes = (reset_in_seconds % 3600) // 60
        await ctx.send(
            f"Trading limit usage for {target.display_name}:\n"
            f"- Used traded value: {humanize_number(used_value)} credits\n"
            f"- Used trades: {used_trades}\n"
            f"- Resets in: {hours}h {minutes}m"
        )

    @market_limits.command(name="reset")
    async def market_limits_reset(self, ctx, member: discord.Member):
        """Reset a member's daily trading limit usage counters."""
        member_conf = self.config.member(member)
        await member_conf.trade_limit_reset_day.set(time.strftime("%Y-%m-%d", time.gmtime()))
        await member_conf.trade_limit_window_start_ts.set(time.time())
        await member_conf.trade_limit_used_value.set(0)
        await member_conf.trade_limit_used_trades.set(0)
        await ctx.send(f"Reset trading limit usage for {member.display_name}.")

    @market.command(name="prices", aliases=["price", "pr"])
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

    @market.command(name="buy", aliases=["b"])
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

        guild_conf = self.config.guild(ctx.guild)
        buy_fee_rate = float(await guild_conf.buy_fee_rate())
        base_cost = int(round(float(asset["price"]) * quantity))
        if base_cost <= 0:
            base_cost = 1
        buy_fee = self._calculate_fee(base_cost, buy_fee_rate)
        total_cost = base_cost + buy_fee
        member_conf = self.config.member(ctx.author)
        limits_ok, limits_message = await self._check_trade_limits(guild_conf, member_conf, base_cost, trades=1)
        if not limits_ok:
            await ctx.send(limits_message)
            return

        if not await bank.can_spend(ctx.author, total_cost):
            balance = await bank.get_balance(ctx.author)
            await ctx.send(
                f"You need {humanize_number(total_cost)} credits but only have "
                f"{humanize_number(balance)} credits."
            )
            return
        remaining_trades_after = await self._remaining_trades_after_trade(guild_conf, member_conf, trades_to_consume=1)
        confirmed = await self._confirm_trade_action(
            ctx,
            asset_name=f"{asset['name']} ({normalized_symbol})",
            side="buy",
            quantity_text=str(quantity),
            price_per_unit_text=f"{humanize_number(round(float(asset['price']), 2))} credits",
            total_value=base_cost,
            fee=buy_fee,
            final_value=total_cost,
            remaining_trades_after=remaining_trades_after,
        )
        if not confirmed:
            return
        await bank.withdraw_credits(ctx.author, total_cost)
        fill_price = float(asset["price"])
        effective_fill_price = total_cost / quantity
        async with member_conf.holdings() as holdings, member_conf.cost_basis() as cost_basis:
            current_amount = int(holdings.get(normalized_symbol, 0))
            current_avg_price = float(cost_basis.get(normalized_symbol, effective_fill_price))
            new_amount = current_amount + quantity
            holdings[normalized_symbol] = new_amount

            if new_amount > 0:
                total_cost_basis = (current_amount * current_avg_price) + (quantity * effective_fill_price)
                cost_basis[normalized_symbol] = round(total_cost_basis / new_amount, 4)
        await self._consume_trade_limits(member_conf, base_cost, trades=1)

        if buy_fee > 0:
            await ctx.send(
                f"Bought {quantity} `{normalized_symbol}` at {humanize_number(round(fill_price, 2))} each.\n"
                f"Base cost: {humanize_number(base_cost)} | Fee: {humanize_number(buy_fee)} ({round(buy_fee_rate * 100, 2)}%)\n"
                f"Total paid: {humanize_number(total_cost)} credits."
            )
            return
        await ctx.send(f"Bought {quantity} `{normalized_symbol}` for {humanize_number(total_cost)} credits.")

    @market.command(name="sell", aliases=["s"])
    async def market_sell(self, ctx, symbol: str, quantity: str = None):
        """Sell an owned asset for bank credits. Use `all` to sell everything."""
        normalized_symbol = self._normalize_symbol(symbol)
        member_conf = self.config.member(ctx.author)
        assets = await self._get_assets(ctx.guild)
        guild_conf = self.config.guild(ctx.guild)
        sell_fee_rate = float(await guild_conf.sell_fee_rate())

        if normalized_symbol == "ALL":
            holdings_snapshot = await member_conf.holdings()
            gross_total_estimate = 0
            total_sell_fees_estimate = 0
            net_total_estimate = 0
            sold_assets_estimate = 0
            sold_units_estimate = 0
            for held_symbol, held_amount in holdings_snapshot.items():
                asset = assets.get(held_symbol)
                if asset is None:
                    continue
                quantity_estimate = int(held_amount)
                if quantity_estimate <= 0:
                    continue
                gross_for_symbol = int(round(float(asset["price"]) * quantity_estimate))
                if gross_for_symbol <= 0:
                    gross_for_symbol = 1
                fee_for_symbol = self._calculate_fee(gross_for_symbol, sell_fee_rate)
                gross_total_estimate += gross_for_symbol
                total_sell_fees_estimate += fee_for_symbol
                net_total_estimate += max(0, gross_for_symbol - fee_for_symbol)
                sold_assets_estimate += 1
                sold_units_estimate += quantity_estimate

            if sold_assets_estimate == 0:
                await ctx.send("You have no tradable holdings to sell.")
                return

            limits_ok, limits_message = await self._check_trade_limits(
                guild_conf, member_conf, gross_total_estimate, trades=1
            )
            if not limits_ok:
                await ctx.send(limits_message)
                return
            remaining_trades_after = await self._remaining_trades_after_trade(guild_conf, member_conf, trades_to_consume=1)
            confirmed = await self._confirm_trade_action(
                ctx,
                asset_name="All Holdings",
                side="sell",
                quantity_text=str(sold_units_estimate),
                price_per_unit_text="mixed",
                total_value=gross_total_estimate,
                fee=total_sell_fees_estimate,
                final_value=net_total_estimate,
                remaining_trades_after=remaining_trades_after,
            )
            if not confirmed:
                return

            total_gain = 0
            total_realized_change = 0
            sold_assets = 0
            sold_units = 0
            total_sell_fees = 0

            async with member_conf.holdings() as holdings, member_conf.cost_basis() as cost_basis, member_conf.realized_profit() as realized_profit:
                for held_symbol, held_amount in list(holdings.items()):
                    asset = assets.get(held_symbol)
                    if asset is None:
                        continue

                    quantity_int = int(held_amount)
                    if quantity_int <= 0:
                        continue

                    sell_price = float(asset["price"])
                    gross_gain_for_symbol = int(round(sell_price * quantity_int))
                    if gross_gain_for_symbol <= 0:
                        gross_gain_for_symbol = 1
                    sell_fee_for_symbol = self._calculate_fee(gross_gain_for_symbol, sell_fee_rate)
                    net_gain_for_symbol = max(0, gross_gain_for_symbol - sell_fee_for_symbol)

                    avg_buy_price = float(cost_basis.get(held_symbol, sell_price))
                    realized_for_symbol = int(round(net_gain_for_symbol - (avg_buy_price * quantity_int)))
                    previous_realized = int(realized_profit.get(held_symbol, 0))
                    realized_profit[held_symbol] = previous_realized + realized_for_symbol

                    total_gain += net_gain_for_symbol
                    total_sell_fees += sell_fee_for_symbol
                    total_realized_change += realized_for_symbol
                    sold_assets += 1
                    sold_units += quantity_int

                    del holdings[held_symbol]
                    if held_symbol in cost_basis:
                        del cost_basis[held_symbol]

            if sold_assets == 0:
                await ctx.send("You have no tradable holdings to sell.")
                return

            if total_gain > 0:
                await bank.deposit_credits(ctx.author, total_gain)
            await self._consume_trade_limits(member_conf, gross_total_estimate, trades=1)
            if total_sell_fees > 0:
                await ctx.send(
                    f"Sold all tradable holdings ({sold_units} units across {sold_assets} assets).\n"
                    f"Net gain: {humanize_number(total_gain)} credits | "
                    f"Sell fees: {humanize_number(total_sell_fees)} ({round(sell_fee_rate * 100, 2)}%)\n"
                    f"Realized P/L: {humanize_number(total_realized_change)} credits."
                )
            else:
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
        gross_gain = 0
        sell_fee = 0
        total_gain = 0
        realized_change = 0
        quantity_to_sell = quantity_int
        holdings_snapshot = await member_conf.holdings()
        owned_amount_snapshot = int(holdings_snapshot.get(normalized_symbol, 0))
        if quantity_to_sell == -1:
            quantity_to_sell = owned_amount_snapshot
        if owned_amount_snapshot < quantity_to_sell:
            await ctx.send(f"You only own {owned_amount_snapshot} `{normalized_symbol}`.")
            return
        if quantity_to_sell <= 0:
            await ctx.send(f"You only own {owned_amount_snapshot} `{normalized_symbol}`.")
            return
        sell_price_snapshot = float(asset["price"])
        gross_gain_snapshot = int(round(sell_price_snapshot * quantity_to_sell))
        if gross_gain_snapshot <= 0:
            gross_gain_snapshot = 1
        sell_fee_snapshot = self._calculate_fee(gross_gain_snapshot, sell_fee_rate)
        total_gain_snapshot = max(0, gross_gain_snapshot - sell_fee_snapshot)
        limits_ok, limits_message = await self._check_trade_limits(
            guild_conf, member_conf, gross_gain_snapshot, trades=1
        )
        if not limits_ok:
            await ctx.send(limits_message)
            return
        remaining_trades_after = await self._remaining_trades_after_trade(guild_conf, member_conf, trades_to_consume=1)
        confirmed = await self._confirm_trade_action(
            ctx,
            asset_name=f"{asset['name']} ({normalized_symbol})",
            side="sell",
            quantity_text=str(quantity_to_sell),
            price_per_unit_text=f"{humanize_number(round(sell_price_snapshot, 2))} credits",
            total_value=gross_gain_snapshot,
            fee=sell_fee_snapshot,
            final_value=total_gain_snapshot,
            remaining_trades_after=remaining_trades_after,
        )
        if not confirmed:
            return
        async with member_conf.holdings() as holdings, member_conf.cost_basis() as cost_basis, member_conf.realized_profit() as realized_profit:
            owned_amount = int(holdings.get(normalized_symbol, 0))
            quantity_int = quantity_to_sell

            if owned_amount < quantity_int:
                await ctx.send(f"You only own {owned_amount} `{normalized_symbol}`.")
                return
            if quantity_int <= 0:
                await ctx.send(f"You only own {owned_amount} `{normalized_symbol}`.")
                return

            avg_buy_price = float(cost_basis.get(normalized_symbol, float(asset["price"])))
            sell_price = float(asset["price"])
            gross_gain = int(round(sell_price * quantity_int))
            if gross_gain <= 0:
                gross_gain = 1
            sell_fee = self._calculate_fee(gross_gain, sell_fee_rate)
            total_gain = max(0, gross_gain - sell_fee)

            holdings[normalized_symbol] = owned_amount - quantity_int
            if holdings[normalized_symbol] == 0:
                del holdings[normalized_symbol]
                if normalized_symbol in cost_basis:
                    del cost_basis[normalized_symbol]

            realized_change = int(round(total_gain - (avg_buy_price * quantity_int)))
            previous_realized = int(realized_profit.get(normalized_symbol, 0))
            realized_profit[normalized_symbol] = previous_realized + realized_change

        if total_gain > 0:
            await bank.deposit_credits(ctx.author, total_gain)
        await self._consume_trade_limits(member_conf, gross_gain, trades=1)
        if sell_fee > 0:
            await ctx.send(
                f"Sold {quantity_int} `{normalized_symbol}` at {humanize_number(round(float(asset['price']), 2))} each.\n"
                f"Gross: {humanize_number(gross_gain)} | Fee: {humanize_number(sell_fee)} ({round(sell_fee_rate * 100, 2)}%)\n"
                f"Net gain: {humanize_number(total_gain)} credits | Realized P/L: {humanize_number(realized_change)} credits."
            )
            return
        await ctx.send(
            f"Sold {quantity_int} `{normalized_symbol}` for {humanize_number(total_gain)} credits. "
            f"Realized P/L: {humanize_number(realized_change)} credits."
        )

    @market.command(name="portfolio", aliases=["pf", "port"])
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

    @market.group(name="autobuy", case_insensitive=True, aliases=["ab"])
    async def market_autobuy(self, ctx):
       """Manage auto-buy orders that execute when price drops to target."""
       if ctx.invoked_subcommand is None:
           await ctx.send_help()

    @market_autobuy.command(name="set", aliases=["create", "add"])
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

       buy_fee_rate = float(await self.config.guild(ctx.guild).buy_fee_rate())
       estimated_value = int(round(target_price * quantity))
       if estimated_value <= 0:
           estimated_value = 1
       estimated_fee = self._calculate_fee(estimated_value, buy_fee_rate)
       estimated_total = estimated_value + estimated_fee
       confirmed = await self._confirm_order_setup(
           ctx,
           title="Confirm Auto-Buy Order",
           lines=[
               f"Asset: {asset['name']} (`{normalized_symbol}`)",
               "Side: AUTO-BUY",
               f"Quantity: {quantity}",
               f"Trigger price: {humanize_number(round(target_price, 2))} credits",
               f"Estimated value: {humanize_number(estimated_value)} credits",
               f"Estimated fee: {humanize_number(estimated_fee)} credits ({round(buy_fee_rate * 100, 2)}%)",
               f"Estimated final cost: {humanize_number(estimated_total)} credits",
           ],
       )
       if not confirmed:
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

    @market_autobuy.command(name="list", aliases=["ls"])
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

    @market_autobuy.command(name="remove", aliases=["rm", "del"])
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

    @market.group(name="autosell", case_insensitive=True, aliases=["as"])
    async def market_autosell(self, ctx):
       """Manage auto-sell orders that execute when price rises to target."""
       if ctx.invoked_subcommand is None:
           await ctx.send_help()

    @market_autosell.command(name="set", aliases=["create", "add"])
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

       sell_fee_rate = float(await self.config.guild(ctx.guild).sell_fee_rate())
       if quantity_int == -1:
           confirmed = await self._confirm_order_setup(
               ctx,
               title="Confirm Auto-Sell Order",
               lines=[
                   f"Asset: {asset['name']} (`{normalized_symbol}`)",
                   "Side: AUTO-SELL",
                   "Quantity: all holdings at trigger time",
                   f"Trigger price: {humanize_number(round(target_price, 2))} credits",
                   "Estimated value: dynamic",
                   f"Estimated fee: {round(sell_fee_rate * 100, 2)}% (dynamic amount)",
                   "Estimated final profit: dynamic",
               ],
           )
       else:
           estimated_value = int(round(target_price * quantity_int))
           if estimated_value <= 0:
               estimated_value = 1
           estimated_fee = self._calculate_fee(estimated_value, sell_fee_rate)
           estimated_final = max(0, estimated_value - estimated_fee)
           confirmed = await self._confirm_order_setup(
               ctx,
               title="Confirm Auto-Sell Order",
               lines=[
                   f"Asset: {asset['name']} (`{normalized_symbol}`)",
                   "Side: AUTO-SELL",
                   f"Quantity: {quantity_int}",
                   f"Trigger price: {humanize_number(round(target_price, 2))} credits",
                   f"Estimated value: {humanize_number(estimated_value)} credits",
                   f"Estimated fee: {humanize_number(estimated_fee)} credits ({round(sell_fee_rate * 100, 2)}%)",
                   f"Estimated final profit: {humanize_number(estimated_final)} credits",
               ],
           )
       if not confirmed:
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

    @market_autosell.command(name="list", aliases=["ls"])
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

    @market_autosell.command(name="remove", aliases=["rm", "del"])
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

    @market.command(name="graph", aliases=["chart", "g"])
    async def market_graph(self, ctx, symbol: str, window: str = "60m"):
        """Show price history graph for an asset (window: Xm or Xh, max 24h)."""
        window_minutes = self._parse_graph_window_minutes(window)
        if window_minutes is None:
            await ctx.send("Window must be like `30m`, `90m`, `2h`, up to `24h`.")
            return
        if window_minutes < 1 or window_minutes > 1440:
            await ctx.send("Window must be between 1 minute and 24 hours.")
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
        values = values[-window_minutes:]
        graph_values = self._compress_graph_values(values)
        image_buffer = self._build_graph_image(graph_values, normalized_symbol, asset["name"], window_minutes)
        graph_file = discord.File(image_buffer, filename=f"{normalized_symbol.lower()}_graph.png")
        await ctx.send(
            f"Price graph for `{normalized_symbol}` ({asset['name']}) "
            f"using {len(graph_values)} point(s) from {len(values)} minute sample(s).",
            file=graph_file,
        )

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
            traceback.print_exc()

    @market.command(name="ticks")
    @commands.admin_or_permissions(manage_guild=True)
    async def market_ticks(self, ctx, count: int):
        """Run multiple market ticks immediately (testing/stress)."""
        if count < 1 or count > 500:
            await ctx.send("Count must be between 1 and 500.")
            return

        await self._ensure_guild_initialized(ctx.guild.id)
        start_assets = await self._get_assets(ctx.guild)
        if not start_assets:
            await ctx.send("No assets configured yet.")
            return

        await ctx.send(f"🔄 Running {count} ticks...")
        try:
            for _ in range(count):
                await self._process_auto_orders(ctx.guild.id)
                await self._update_guild_prices(ctx.guild.id)
            await self._update_live_prices_message(ctx.guild.id)
        except Exception as e:
            await ctx.send(f"❌ Error during multi-tick run: {e}")
            return

        end_assets = await self._get_assets(ctx.guild)
        movers = []
        for symbol, after in end_assets.items():
            before_asset = start_assets.get(symbol)
            if before_asset is None:
                continue
            before = float(before_asset.get("price", 0))
            after_price = float(after.get("price", 0))
            if before <= 0:
                continue
            pct = ((after_price - before) / before) * 100
            movers.append((abs(pct), symbol, before, after_price, pct))

        movers.sort(reverse=True)
        lines = [f"✅ Ran **{count}** ticks."]
        if movers:
            lines.append("Top movers:")
            for _, symbol, before, after_price, pct in movers[:5]:
                arrow = "📈" if pct >= 0 else "📉"
                lines.append(
                    f"{arrow} `{symbol}`: {humanize_number(round(before, 2))} -> "
                    f"{humanize_number(round(after_price, 2))} ({round(pct, 2)}%)"
                )
        await ctx.send("\n".join(lines))

    @market.command(name="debug")
    @commands.admin_or_permissions(manage_guild=True)
    async def market_debug(self, ctx):
        """Show debug info about price update timing."""
        guild_conf = self.config.guild(ctx.guild)
        last_update_ts = await guild_conf.last_update_ts()
        now = time.time()
        time_since = now - last_update_ts
        
        await ctx.send(
            f"**Update Debug Info:**\n"
            f"Interval: 1 minute (60 seconds)\n"
            f"Last update: {last_update_ts}\n"
            f"Now: {now}\n"
            f"Time since last update: {time_since:.1f} seconds\n"
            f"Ready for update: {time_since >= 60}"
        )

    @market.group(name="cycle", case_insensitive=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def market_cycle(self, ctx):
        """Show market profile cycle details."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @market_cycle.command(name="info")
    async def market_cycle_info(self, ctx, symbol: str):
        """Show current profile and next profile-shift timing for an asset."""
        assets = await self._get_assets(ctx.guild)
        normalized_symbol = self._normalize_symbol(symbol)
        asset = assets.get(normalized_symbol)
        if asset is None:
            await ctx.send(f"`{normalized_symbol}` does not exist.")
            return

        profile = self._detect_asset_profile(asset)
        next_change_ts = float(asset.get("next_profile_change_ts", 0.0))
        now = time.time()

        if profile == "custom" or next_change_ts <= 0:
            cycle_state = "disabled (custom/manual tuning)"
            next_shift_text = "N/A"
        else:
            cycle_state = "enabled"
            remaining = max(0, int(next_change_ts - now))
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            seconds = remaining % 60
            if remaining == 0:
                next_shift_text = "due now (on next tick)"
            else:
                next_shift_text = f"in {hours}h {minutes}m {seconds}s"

        await ctx.send(
            f"`{normalized_symbol}` cycle info:\n"
            f"Current profile: `{profile}`\n"
            f"Cycle state: {cycle_state}\n"
            f"Next profile shift: {next_shift_text}"
        )

    @market_cycle.command(name="announce")
    async def market_cycle_announce(self, ctx, enabled: bool):
        """Toggle profile change announcements in the configured event channel."""
        guild_conf = self.config.guild(ctx.guild)
        await guild_conf.announce_profile_changes.set(enabled)
        channel_id = int(await guild_conf.event_announce_channel_id())
        state = "enabled" if enabled else "disabled"
        if enabled and channel_id:
            await ctx.send(
                f"Profile change announcements are now {state} in <#{channel_id}>."
            )
            return
        if enabled:
            await ctx.send(
                "Profile change announcements are now enabled, but no announcement channel is set. "
                "Set one with `market event channel #channel`."
            )
            return
        await ctx.send("Profile change announcements are now disabled.")

    @market.command(name="ordersdebug")
    async def market_ordersdebug(self, ctx):
        """Show your auto-orders for debugging."""
        member_conf = self.config.member(ctx.author)
        auto_orders = await member_conf.auto_orders()
        holdings = await member_conf.holdings()
        
        if not auto_orders:
            await ctx.send("You have no auto-orders.")
            return
        
        lines = [f"**Your Auto-Orders ({len(auto_orders)} total):**"]
        for order_id, order in auto_orders.items():
            order_type = order.get("type", "?")
            symbol = order.get("symbol", "?")
            target = order.get("target_price", 0)
            qty = order.get("quantity", 0)
            held = holdings.get(symbol, 0)
            lines.append(f"\n**{order_id}**\n"
                        f"Type: {order_type}\n"
                        f"Symbol: {symbol} (holding: {held})\n"
                        f"Target price: {target}\n"
                        f"Quantity: {qty} (note: -1 means 'all')\n"
                        f"Full order data: {order}")
        
        await ctx.send("\n".join(lines))

    @market.command(name="testorder")
    async def market_testorder(self, ctx, symbol: str):
        """Test if an auto-order would execute (simulates the check)."""
        guild = ctx.guild
        assets = await self._get_assets(guild)
        
        if symbol.upper() not in assets:
            await ctx.send(f"Asset `{symbol}` not found.")
            return
        
        asset = assets[symbol.upper()]
        current_price = float(asset.get("price", 0))
        
        member_conf = self.config.member(ctx.author)
        auto_orders = await member_conf.auto_orders()
        holdings = await member_conf.holdings()
        
        orders_for_symbol = [o for o in auto_orders.values() if o.get("symbol") == symbol.upper()]
        
        if not orders_for_symbol:
            await ctx.send(f"No auto-orders found for `{symbol}`.")
            return
        
        lines = [f"**Testing Auto-Orders for `{symbol}` (current price: {current_price})**\n"]
        
        for order in orders_for_symbol:
            order_type = order.get("type")
            target_price = float(order.get("target_price", 0))
            quantity = int(order.get("quantity", 0))
            owned = int(holdings.get(symbol.upper(), 0))
            
            lines.append(f"\n**Type:** {order_type}")
            lines.append(f"**Target:** {target_price}")
            lines.append(f"**Current:** {current_price}")
            
            if order_type == "buy":
                condition = current_price <= target_price
                lines.append(f"**Condition (price <= target):** {condition}")
                if condition:
                    lines.append("✅ **WOULD EXECUTE** (if you have credits)")
                else:
                    lines.append(f"❌ **Would NOT execute** ({current_price} > {target_price})")
                    
            elif order_type == "sell":
                condition = current_price >= target_price
                qty_to_sell = owned if quantity == -1 else quantity
                can_sell = owned >= qty_to_sell and qty_to_sell > 0
                
                lines.append(f"**Condition (price >= target):** {condition}")
                lines.append(f"**Owned:** {owned} | **Need to sell:** {qty_to_sell}")
                lines.append(f"**Can sell:** {can_sell}")
                
                if condition and can_sell:
                    lines.append("✅ **WOULD EXECUTE**")
                else:
                    if not condition:
                        lines.append(f"❌ **Would NOT execute** ({current_price} < {target_price})")
                    if not can_sell:
                        lines.append(f"❌ **Would NOT execute** (not enough holdings: {owned} < {qty_to_sell})")
        
        await ctx.send("\n".join(lines))

    @market.group(name="admin", case_insensitive=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def market_admin(self, ctx):
        """Admin commands for market management."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @market_admin.command(name="deposit")
    async def market_admin_deposit(self, ctx, member: discord.Member, symbol: str, quantity: int):
        """Deposit crypto/stocks to a member's holdings. Usage: !!market admin deposit @user VIL 100"""
        if quantity <= 0:
            await ctx.send("❌ Quantity must be positive")
            return
        
        symbol = symbol.upper()
        assets = await self._get_assets(ctx.guild)
        
        if symbol not in assets:
            await ctx.send(f"❌ Asset `{symbol}` not found. Available: {', '.join(assets.keys())}")
            return
        
        member_conf = self.config.member(member)
        async with member_conf.holdings() as holdings:
            current = int(holdings.get(symbol, 0))
            holdings[symbol] = current + quantity
        
        await ctx.send(f"✅ Deposited **{quantity}** `{symbol}` to {member.mention}\n"
                      f"New balance: {current + quantity}")

    @market_admin.command(name="remove")
    async def market_admin_remove(self, ctx, member: discord.Member, symbol: str, quantity: int):
        """Remove crypto/stocks from a member's holdings. Usage: !!market admin remove @user VIL 50"""
        if quantity <= 0:
            await ctx.send("❌ Quantity must be positive")
            return
        
        symbol = symbol.upper()
        assets = await self._get_assets(ctx.guild)
        
        if symbol not in assets:
            await ctx.send(f"❌ Asset `{symbol}` not found. Available: {', '.join(assets.keys())}")
            return
        
        member_conf = self.config.member(member)
        async with member_conf.holdings() as holdings:
            current = int(holdings.get(symbol, 0))
            if current < quantity:
                await ctx.send(f"❌ Cannot remove {quantity} `{symbol}` (only has {current})")
                return
            holdings[symbol] = current - quantity
            if holdings[symbol] == 0:
                del holdings[symbol]
        
        await ctx.send(f"✅ Removed **{quantity}** `{symbol}` from {member.mention}\n"
                      f"Remaining balance: {current - quantity}")

    @market_admin.command(name="set")
    async def market_admin_set(self, ctx, member: discord.Member, symbol: str, quantity: int):
        """Set exact crypto/stocks balance for a member. Usage: !!market admin set @user VIL 500"""
        if quantity < 0:
            await ctx.send("❌ Quantity cannot be negative")
            return
        
        symbol = symbol.upper()
        assets = await self._get_assets(ctx.guild)
        
        if symbol not in assets:
            await ctx.send(f"❌ Asset `{symbol}` not found. Available: {', '.join(assets.keys())}")
            return
        
        member_conf = self.config.member(member)
        async with member_conf.holdings() as holdings:
            old_qty = int(holdings.get(symbol, 0))
            if quantity == 0:
                if symbol in holdings:
                    del holdings[symbol]
            else:
                holdings[symbol] = quantity
        
        await ctx.send(f"✅ Set {member.mention}'s `{symbol}` balance to **{quantity}**\n"
                      f"Previous balance: {old_qty}")

    @market.command(name="triggerorders")
    @commands.admin_or_permissions(manage_guild=True)
    async def market_triggerorders(self, ctx):
        """Manually trigger auto-order processing for testing."""
        await ctx.send("🔄 Processing auto-orders now...")
        try:
            # First show debug info
            debug_output = await self._process_auto_orders_debug(ctx.guild.id)
            debug_text = "\n".join(debug_output)
            
            # Split into chunks if too long
            if len(debug_text) > 1900:
                chunks = [debug_text[i:i+1900] for i in range(0, len(debug_text), 1900)]
                for chunk in chunks:
                    await ctx.send(f"```\n{chunk}\n```")
            else:
                await ctx.send(f"```\n{debug_text}\n```")
            
            # Now actually process
            execution_log = await self._process_auto_orders(ctx.guild.id)
            
            # Show what was executed
            if execution_log:
                log_text = "\n".join(execution_log)
                await ctx.send(f"✅ **Orders Executed:**\n```\n{log_text}\n```")
            else:
                await ctx.send("⚠️ No orders were executed (all conditions not met)")
        except Exception as e:
            await ctx.send(f"❌ Error: {e}\n```{traceback.format_exc()}```")

    @market.command(name="liveprices")
    @commands.admin_or_permissions(manage_guild=True)
    async def market_liveprices(self, ctx):
        """Post a live prices message that auto-updates every minute."""
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
        await ctx.send("Live prices message created. I will update it every minute.")

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
        announce_profile_changes = bool(await self.config.guild(ctx.guild).announce_profile_changes())
        profile_changes_line = f"Profile change announcements: {'enabled' if announce_profile_changes else 'disabled'}"
        if not active_events:
            if announce_channel_id:
                await ctx.send(
                    f"There are no active events.\nAnnouncement channel: <#{announce_channel_id}>\n{profile_changes_line}"
                )
            else:
                await ctx.send(f"There are no active events.\nAnnouncement channel: not set.\n{profile_changes_line}")
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
        await ctx.send("Active events:\n" + "\n".join(lines) + f"\n{announcement_line}\n{profile_changes_line}")

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
            f"An event is now happening for `{normalized_symbol}`.",
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
        defaults = self._behavior_profile("stable")

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
                "profile": "stable",
                "next_profile_change_ts": time.time() + self._profile_transition_window_seconds(),
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
            "Available profiles: `stable`, `uptrend`, `downtrend`, `swing`, `wild`, `bullrun`, `crash`, `recovery`, `flat`.\n"
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
        profile = self._detect_asset_profile(asset)

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
            "bull": "bullrun",
            "run": "bullrun",
            "bull-run": "bullrun",
            "dump": "crash",
            "panic": "crash",
            "recover": "recovery",
            "sideways": "flat",
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

            selected = self._behavior_profile(normalized_profile)
            if selected is None:
                await ctx.send(
                    "Unknown profile. Use one of: `stable`, `uptrend`, `downtrend`, `swing`, `wild`, `bullrun`, `crash`, `recovery`, `flat`."
                )
                return

            asset["volatility"] = round(float(selected["volatility"]), 4)
            asset["risk"] = round(float(selected["risk"]), 2)
            asset["momentum"] = round(float(selected["momentum"]), 4)
            asset["reversal_accel"] = round(float(selected["reversal_accel"]), 4)
            asset["drift"] = round(float(selected["drift"]), 5)
            asset["bull_bias"] = round(float(selected["bull_bias"]), 4)
            asset["profile"] = normalized_profile
            asset["next_profile_change_ts"] = time.time() + self._profile_transition_window_seconds()
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
            asset["profile"] = "custom"
            asset["next_profile_change_ts"] = 0
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
            asset["profile"] = "custom"
            asset["next_profile_change_ts"] = 0
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
            asset["profile"] = "custom"
            asset["next_profile_change_ts"] = 0
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
            asset["profile"] = "custom"
            asset["next_profile_change_ts"] = 0
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
            asset["profile"] = "custom"
            asset["next_profile_change_ts"] = 0
            assets[normalized_symbol] = asset

        await ctx.send(f"`{normalized_symbol}` bull bias set to {round(percent, 2)}%.")


async def setup(bot):
    await bot.add_cog(MarketTrade(bot))
