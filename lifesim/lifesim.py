import copy
import time
from typing import Dict, List, Optional, Tuple

import discord
from redbot.core import Config, bank, commands
from redbot.core.utils.chat_formatting import humanize_number, pagify


class LifeSim(commands.Cog):
    """A small life simulation game using Red bank credits."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=557912408431, force_registration=True)
        self.config.register_guild(
            jobs={},
            foods={},
            houses={},
            settings={
                "starting_hunger": 85.0,
                "starting_energy": 85.0,
                "starting_happiness": 75.0,
                "need_decay_per_hour": {
                    "hunger": 3.0,
                    "energy": 4.0,
                    "happiness": 1.5,
                },
                "rest_restore": {
                    "energy": 22.0,
                    "happiness": 9.0,
                },
                "work_cooldown_hours": 4.0,
                "rest_cooldown_hours": 2.0,
                "sleep_cooldown_hours": 24.0,
                "upkeep_interval_hours": 24.0,
            },
            seeded=False,
        )
        self.config.register_member(
            job_key="",
            house_key="",
            hunger=85.0,
            energy=85.0,
            happiness=75.0,
            career_xp=0,
            last_update_ts=0.0,
            last_work_ts=0.0,
            last_rest_ts=0.0,
            last_sleep_ts=0.0,
            sleep_lock_until_ts=0.0,
            last_upkeep_ts=0.0,
            inventory={},
        )

    @staticmethod
    def _normalize_key(value: str) -> str:
        return "_".join(value.strip().lower().split())

    @staticmethod
    def _currency(amount: float) -> str:
        return humanize_number(int(round(max(0.0, float(amount)))))

    @staticmethod
    def _clamp_need(value: float) -> float:
        return max(0.0, min(100.0, float(value)))

    @staticmethod
    def _default_jobs() -> Dict[str, Dict[str, float]]:
        return {
            "freelance": {
                "name": "Freelance Worker",
                "pay": 120.0,
                "energy_cost": 8.0,
                "hunger_cost": 6.0,
                "happiness_cost": 1.0,
                "cooldown_minutes": 30.0,
                "xp_gain": 12.0,
                "description": "Simple odd jobs with steady pay and low requirements.",
            },
            "cashier": {
                "name": "Cashier",
                "pay": 200.0,
                "energy_cost": 10.0,
                "hunger_cost": 8.0,
                "happiness_cost": 2.0,
                "cooldown_minutes": 45.0,
                "xp_gain": 18.0,
                "description": "A customer-facing job with decent pay.",
            },
            "delivery": {
                "name": "Delivery Driver",
                "pay": 280.0,
                "energy_cost": 14.0,
                "hunger_cost": 10.0,
                "happiness_cost": 3.0,
                "cooldown_minutes": 60.0,
                "xp_gain": 24.0,
                "description": "Run packages around town for good pay and a bigger stamina hit.",
            },
            "chef": {
                "name": "Chef",
                "pay": 340.0,
                "energy_cost": 16.0,
                "hunger_cost": 12.0,
                "happiness_cost": 2.0,
                "cooldown_minutes": 75.0,
                "xp_gain": 30.0,
                "description": "Work in a kitchen for strong pay and steady progression.",
            },
            "miner": {
                "name": "Miner",
                "pay": 420.0,
                "energy_cost": 22.0,
                "hunger_cost": 14.0,
                "happiness_cost": 4.0,
                "cooldown_minutes": 120.0,
                "xp_gain": 40.0,
                "description": "Hard physical work with high rewards.",
            },
        }

    @staticmethod
    def _default_foods() -> Dict[str, Dict[str, float]]:
        return {
            "ramen": {
                "name": "Instant Ramen",
                "price": 35.0,
                "hunger_restore": 22.0,
                "energy_restore": 3.0,
                "happiness_restore": 1.0,
                "description": "Cheap and filling.",
            },
            "sandwich": {
                "name": "Sandwich",
                "price": 60.0,
                "hunger_restore": 32.0,
                "energy_restore": 4.0,
                "happiness_restore": 2.0,
                "description": "A balanced lunch.",
            },
            "coffee": {
                "name": "Coffee",
                "price": 50.0,
                "hunger_restore": 0.0,
                "energy_restore": 18.0,
                "happiness_restore": 0.0,
                "description": "A quick boost for long shifts.",
            },
            "salad": {
                "name": "Salad",
                "price": 70.0,
                "hunger_restore": 26.0,
                "energy_restore": 4.0,
                "happiness_restore": 3.0,
                "description": "Healthy and a bit more expensive.",
            },
        }

    @staticmethod
    def _default_houses() -> Dict[str, Dict[str, float]]:
        return {
            "room": {
                "name": "Shared Room",
                "price": 800.0,
                "upkeep": 20.0,
                "work_bonus": 0.0,
                "rest_energy": 18.0,
                "rest_happiness": 8.0,
                "description": "A bare-bones room with cheap upkeep.",
            },
            "apartment": {
                "name": "Apartment",
                "price": 2500.0,
                "upkeep": 60.0,
                "work_bonus": 0.05,
                "rest_energy": 30.0,
                "rest_happiness": 12.0,
                "description": "A comfortable apartment with a small productivity bonus.",
            },
            "house": {
                "name": "Family House",
                "price": 7000.0,
                "upkeep": 120.0,
                "work_bonus": 0.1,
                "rest_energy": 42.0,
                "rest_happiness": 18.0,
                "description": "A proper home with stronger bonuses and higher upkeep.",
            },
        }

    @staticmethod
    def _default_settings() -> Dict[str, Dict[str, float]]:
        return {
            "starting_hunger": 85.0,
            "starting_energy": 85.0,
            "starting_happiness": 75.0,
            "need_decay_per_hour": {
                "hunger": 3.0,
                "energy": 4.0,
                "happiness": 1.5,
            },
            "rest_restore": {
                "energy": 22.0,
                "happiness": 9.0,
            },
            "work_cooldown_hours": 4.0,
            "rest_cooldown_hours": 2.0,
            "sleep_cooldown_hours": 24.0,
            "upkeep_interval_hours": 24.0,
        }

    async def _ensure_guild_defaults(self, guild: discord.Guild):
        guild_conf = self.config.guild(guild)
        defaults = self._default_settings()

        async with guild_conf.jobs() as jobs:
            if not jobs:
                jobs.update(copy.deepcopy(self._default_jobs()))

        async with guild_conf.foods() as foods:
            if not foods:
                foods.update(copy.deepcopy(self._default_foods()))

        async with guild_conf.houses() as houses:
            if not houses:
                houses.update(copy.deepcopy(self._default_houses()))

        async with guild_conf.settings() as settings:
            for key, value in defaults.items():
                if key not in settings:
                    settings[key] = copy.deepcopy(value)
                elif isinstance(value, dict):
                    for nested_key, nested_value in value.items():
                        settings[key].setdefault(nested_key, copy.deepcopy(nested_value))

        await guild_conf.seeded.set(True)

    async def _get_guild_data(self, guild: discord.Guild):
        await self._ensure_guild_defaults(guild)
        guild_conf = self.config.guild(guild)
        return {
            "jobs": await guild_conf.jobs(),
            "foods": await guild_conf.foods(),
            "houses": await guild_conf.houses(),
            "settings": await guild_conf.settings(),
        }

    async def _sync_member_state(self, member: discord.Member, guild_data: Dict[str, dict]) -> List[str]:
        member_conf = self.config.member(member)
        settings = guild_data["settings"]
        notes: List[str] = []
        now = time.time()

        hunger = float(await member_conf.hunger())
        energy = float(await member_conf.energy())
        happiness = float(await member_conf.happiness())
        last_update_ts = float(await member_conf.last_update_ts())

        if last_update_ts <= 0.0:
            await member_conf.last_update_ts.set(now)
            return notes

        elapsed_hours = max(0.0, (now - last_update_ts) / 3600.0)
        if elapsed_hours > 0.0:
            decay = settings["need_decay_per_hour"]
            hunger = self._clamp_need(hunger - (float(decay["hunger"]) * elapsed_hours))
            energy = self._clamp_need(energy - (float(decay["energy"]) * elapsed_hours))
            happiness = self._clamp_need(happiness - (float(decay["happiness"]) * elapsed_hours))
            await member_conf.hunger.set(hunger)
            await member_conf.energy.set(energy)
            await member_conf.happiness.set(happiness)
            await member_conf.last_update_ts.set(now)

        house_key = str(await member_conf.house_key())
        houses = guild_data["houses"]
        if house_key and house_key in houses:
            house = houses[house_key]
            upkeep_hours = max(1.0, float(settings["upkeep_interval_hours"]))
            last_upkeep_ts = float(await member_conf.last_upkeep_ts())
            if last_upkeep_ts <= 0.0:
                await member_conf.last_upkeep_ts.set(now)
            else:
                periods = int((now - last_upkeep_ts) // (upkeep_hours * 3600.0))
                if periods > 0:
                    upkeep_cost = int(round(float(house["upkeep"]) * periods))
                    if upkeep_cost > 0:
                        if await bank.can_spend(member, upkeep_cost):
                            await bank.withdraw_credits(member, upkeep_cost)
                            notes.append(
                                f"Paid {self._currency(upkeep_cost)} credits in housing upkeep."
                            )
                        else:
                            await member_conf.house_key.set("")
                            await member_conf.last_upkeep_ts.set(0.0)
                            notes.append(
                                "You could not afford house upkeep and lost your home."
                            )
                            return notes
                    await member_conf.last_upkeep_ts.set(last_upkeep_ts + periods * upkeep_hours * 3600.0)

        return notes

    async def _sleep_lock_active(self, member: discord.Member) -> Tuple[bool, float]:
        member_conf = self.config.member(member)
        until_ts = float(await member_conf.sleep_lock_until_ts())
        now = time.time()
        if until_ts > now:
            return True, until_ts - now
        return False, 0.0

    async def _require_active_state(
        self,
        ctx: commands.Context,
        member: Optional[discord.Member] = None,
        *,
        bypass_sleep_lock: bool = False,
    ) -> bool:
        subject = member or ctx.author
        if bypass_sleep_lock:
            return True

        locked, remaining = await self._sleep_lock_active(subject)
        if locked:
            hours = int(remaining // 3600)
            minutes = int((remaining % 3600) // 60)
            seconds = int(round(remaining % 60))
            await ctx.send(
                f"You are sleeping and cannot use LifeSim commands for "
                f"{hours}h {minutes}m {seconds}s."
            )
            return False
        return True

    async def _set_member_needs(
        self,
        member: discord.Member,
        *,
        hunger: Optional[float] = None,
        energy: Optional[float] = None,
        happiness: Optional[float] = None,
    ):
        member_conf = self.config.member(member)
        changed = False
        if hunger is not None:
            await member_conf.hunger.set(self._clamp_need(hunger))
            changed = True
        if energy is not None:
            await member_conf.energy.set(self._clamp_need(energy))
            changed = True
        if happiness is not None:
            await member_conf.happiness.set(self._clamp_need(happiness))
            changed = True
        if changed:
            await member_conf.last_update_ts.set(time.time())

    async def _reset_member_cooldowns(self, member: discord.Member):
        member_conf = self.config.member(member)
        await member_conf.last_work_ts.set(0.0)
        await member_conf.last_rest_ts.set(0.0)
        await member_conf.last_sleep_ts.set(0.0)
        await member_conf.sleep_lock_until_ts.set(0.0)

    async def _member_house(self, member: discord.Member, guild_data: Dict[str, dict]) -> Optional[dict]:
        house_key = str(await self.config.member(member).house_key())
        return guild_data["houses"].get(house_key)

    async def _member_job(self, member: discord.Member, guild_data: Dict[str, dict]) -> Optional[dict]:
        job_key = str(await self.config.member(member).job_key())
        return guild_data["jobs"].get(job_key)

    def _need_summary(self, hunger: float, energy: float, happiness: float) -> str:
        return (
            f"Hunger {int(round(hunger))}/100 | "
            f"Energy {int(round(energy))}/100 | "
            f"Happiness {int(round(happiness))}/100"
        )

    def _need_bar(self, value: float) -> str:
        filled = int(round(self._clamp_need(value) / 10.0))
        return "█" * filled + "░" * (10 - filled)

    async def _build_profile_embed(self, member: discord.Member, guild_data: Dict[str, dict]) -> discord.Embed:
        member_conf = self.config.member(member)
        await self._sync_member_state(member, guild_data)

        hunger = float(await member_conf.hunger())
        energy = float(await member_conf.energy())
        happiness = float(await member_conf.happiness())
        career_xp = int(await member_conf.career_xp())
        job = await self._member_job(member, guild_data)
        house = await self._member_house(member, guild_data)
        balance = await bank.get_balance(member)
        inventory = await member_conf.inventory()

        embed = discord.Embed(
            title=f"LifeSim Profile: {member.display_name}",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Bank Balance",
            value=f"{self._currency(balance)} credits",
            inline=True,
        )
        embed.add_field(
            name="Current Job",
            value=job["name"] if job else "Unemployed",
            inline=True,
        )
        embed.add_field(
            name="Current House",
            value=house["name"] if house else "No home",
            inline=True,
        )
        embed.add_field(
            name="Needs",
            value=(
                f"Hunger: `{self._need_bar(hunger)}` {int(round(hunger))}/100\n"
                f"Energy: `{self._need_bar(energy)}` {int(round(energy))}/100\n"
                f"Happiness: `{self._need_bar(happiness)}` {int(round(happiness))}/100"
            ),
            inline=False,
        )
        embed.add_field(
            name="Career XP",
            value=str(career_xp),
            inline=True,
        )

        item_count = sum(int(qty) for qty in inventory.values()) if inventory else 0
        embed.add_field(
            name="Inventory",
            value=f"{len(inventory)} item types / {item_count} items",
            inline=True,
        )
        return embed

    def _format_job_line(self, key: str, job: Dict[str, float]) -> str:
        return (
            f"`{key}` — {job['name']} | pay {self._currency(job['pay'])} | "
            f"costs H{int(job['hunger_cost'])}/E{int(job['energy_cost'])}/M{int(job['happiness_cost'])} | "
            f"cooldown {int(job['cooldown_minutes'])}m"
        )

    def _format_food_line(self, key: str, food: Dict[str, float]) -> str:
        return (
            f"`{key}` — {food['name']} | price {self._currency(food['price'])} | "
            f"+{int(food['hunger_restore'])} hunger, +{int(food['energy_restore'])} energy, "
            f"+{int(food['happiness_restore'])} happiness"
        )

    def _format_house_line(self, key: str, house: Dict[str, float]) -> str:
        return (
            f"`{key}` — {house['name']} | price {self._currency(house['price'])} | "
            f"upkeep {self._currency(house['upkeep'])}/day | "
            f"work bonus +{int(round(float(house['work_bonus']) * 100))}%"
        )

    @commands.group(name="lifesim", aliases=["life", "lsim"], case_insensitive=True)
    @commands.guild_only()
    async def lifesim_group(self, ctx):
        """Manage and play the LifeSim game."""
        invoked = ctx.invoked_subcommand
        if invoked is not None and getattr(invoked, "name", "") == "commands":
            pass
        else:
            if not await self._require_active_state(ctx):
                return
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @lifesim_group.command(name="profile")
    async def lifesim_profile(self, ctx, member: Optional[discord.Member] = None):
        """Show a member's current life state."""
        member = member or ctx.author
        if not await self._require_active_state(ctx, member):
            return
        guild_data = await self._get_guild_data(ctx.guild)
        embed = await self._build_profile_embed(member, guild_data)
        await ctx.send(embed=embed)

    @lifesim_group.command(name="status")
    async def lifesim_status(self, ctx, member: Optional[discord.Member] = None):
        """Alias for profile."""
        member = member or ctx.author
        if not await self._require_active_state(ctx, member):
            return
        guild_data = await self._get_guild_data(ctx.guild)
        embed = await self._build_profile_embed(member, guild_data)
        await ctx.send(embed=embed)

    @lifesim_group.command(name="commands", aliases=["cmds", "listcommands"])
    async def lifesim_commands(self, ctx):
        """Show all LifeSim commands, separated by regular and admin use."""
        lines = [
            "**Regular commands**",
            "- `[p]lifesim profile [member]`",
            "- `[p]lifesim status [member]`",
            "- `[p]lifesim work`",
            "- `[p]lifesim rest`",
            "- `[p]lifesim sleep`",
            "- `[p]lifesim jobs list`",
            "- `[p]lifesim jobs info <job>`",
            "- `[p]lifesim jobs apply <job>`",
            "- `[p]lifesim jobs clear`",
            "- `[p]lifesim shop list`",
            "- `[p]lifesim shop info <item>`",
            "- `[p]lifesim shop buy <item> [quantity]`",
            "- `[p]lifesim inventory`",
            "- `[p]lifesim eat <item> [quantity]`",
            "- `[p]lifesim house list`",
            "- `[p]lifesim house info <house>`",
            "- `[p]lifesim house buy <house>`",
            "- `[p]lifesim house current`",
            "",
            "**Admin commands**",
            "- `[p]lifesim jobs admin add|edit|remove|resetdefaults`",
            "- `[p]lifesim shop admin add|edit|remove|resetdefaults`",
            "- `[p]lifesim house admin add|edit|remove|resetdefaults`",
            "- `[p]lifesim settings view|setstarting|setdecay|setrest|setrestcooldown|setworkcooldown|setsleepcooldown|setupkeep|resetdefaults`",
            "- `[p]lifesim member set <member> <hunger|energy|happiness|xp|job|house> <value>`",
            "- `[p]lifesim member cooldowns reset <member>`",
        ]
        for page in pagify("\n".join(lines), page_length=1900):
            await ctx.send(page)

    @lifesim_group.command(name="work")
    async def lifesim_work(self, ctx):
        """Work your active job to earn bank credits."""
        if not await self._require_active_state(ctx):
            return
        guild_data = await self._get_guild_data(ctx.guild)
        member_conf = self.config.member(ctx.author)
        job = await self._member_job(ctx.author, guild_data)
        if job is None:
            await ctx.send("You do not have a job yet. Use `[p]lifesim jobs apply <job>` first.")
            return

        notes = await self._sync_member_state(ctx.author, guild_data)
        now = time.time()
        last_work_ts = float(await member_conf.last_work_ts())
        cooldown_seconds = float(guild_data["settings"]["work_cooldown_hours"]) * 3600.0
        if last_work_ts > 0.0 and now - last_work_ts < cooldown_seconds:
            remaining = int(round(cooldown_seconds - (now - last_work_ts)))
            await ctx.send(
                "Work is on cooldown. "
                f"Try again in {remaining // 60}m {remaining % 60}s."
            )
            return

        hunger = float(await member_conf.hunger())
        energy = float(await member_conf.energy())
        happiness = float(await member_conf.happiness())
        required_energy = float(job["energy_cost"])
        if energy < required_energy:
            await ctx.send(
                f"You need at least {int(round(required_energy))} energy to work as {job['name']}."
            )
            return
        performance = max(0.45, min(1.30, (hunger + energy + happiness) / 300.0 * 1.1))
        house = await self._member_house(ctx.author, guild_data)
        house_bonus = float(house["work_bonus"]) if house else 0.0
        xp = int(await member_conf.career_xp())
        xp_bonus = min(0.30, xp / 1000.0)
        payout = int(round(float(job["pay"]) * performance * (1.0 + house_bonus + xp_bonus)))

        hunger = self._clamp_need(hunger - float(job["hunger_cost"]))
        energy = self._clamp_need(energy - float(job["energy_cost"]))
        happiness = self._clamp_need(happiness - float(job["happiness_cost"]))
        await member_conf.hunger.set(hunger)
        await member_conf.energy.set(energy)
        await member_conf.happiness.set(happiness)
        await member_conf.last_work_ts.set(now)
        await member_conf.career_xp.set(xp + int(round(float(job["xp_gain"]))))
        await bank.deposit_credits(ctx.author, payout)

        note_text = ""
        if notes:
            note_text = "\n" + "\n".join(f"- {note}" for note in notes)

        await ctx.send(
            f"You worked as **{job['name']}** and earned **{self._currency(payout)}** credits."
            f"{note_text}"
        )

    @lifesim_group.command(name="rest")
    async def lifesim_rest(self, ctx):
        """Rest to recover energy and happiness."""
        if not await self._require_active_state(ctx):
            return
        guild_data = await self._get_guild_data(ctx.guild)
        member_conf = self.config.member(ctx.author)
        await self._sync_member_state(ctx.author, guild_data)
        settings = guild_data["settings"]
        house = await self._member_house(ctx.author, guild_data)
        now = time.time()
        last_rest_ts = float(await member_conf.last_rest_ts())
        rest_cooldown_seconds = float(settings["rest_cooldown_hours"]) * 3600.0
        if last_rest_ts > 0.0 and now - last_rest_ts < rest_cooldown_seconds:
            remaining = int(round(rest_cooldown_seconds - (now - last_rest_ts)))
            await ctx.send(
                "Rest is on cooldown. "
                f"Try again in {remaining // 3600}h {(remaining % 3600) // 60}m {remaining % 60}s."
            )
            return

        energy = float(await member_conf.energy())
        happiness = float(await member_conf.happiness())
        energy_gain = float(settings["rest_restore"]["energy"])
        happiness_gain = float(settings["rest_restore"]["happiness"])

        if house:
            energy_gain += float(house["rest_energy"])
            happiness_gain += float(house["rest_happiness"])
        else:
            energy_gain *= 0.5
            happiness_gain *= 0.5

        await member_conf.energy.set(self._clamp_need(energy + energy_gain))
        await member_conf.happiness.set(self._clamp_need(happiness + happiness_gain))
        await member_conf.last_rest_ts.set(now)
        await ctx.send(
            f"You rested and recovered **{int(round(energy_gain))}** energy and "
            f"**{int(round(happiness_gain))}** happiness."
        )

    @lifesim_group.command(name="sleep")
    async def lifesim_sleep(self, ctx):
        """Sleep to fully recover, then lock LifeSim commands for 8 hours."""
        if not await self._require_active_state(ctx):
            return
        guild_data = await self._get_guild_data(ctx.guild)
        member_conf = self.config.member(ctx.author)
        await self._sync_member_state(ctx.author, guild_data)

        now = time.time()
        last_sleep_ts = float(await member_conf.last_sleep_ts())
        sleep_cooldown_seconds = float(guild_data["settings"]["sleep_cooldown_hours"]) * 3600.0
        if last_sleep_ts > 0.0 and now - last_sleep_ts < sleep_cooldown_seconds:
            remaining = int(round(sleep_cooldown_seconds - (now - last_sleep_ts)))
            await ctx.send(
                "Sleep is still on cooldown. "
                f"Try again in {remaining // 3600}h {(remaining % 3600) // 60}m {remaining % 60}s."
            )
            return

        hunger = float(await member_conf.hunger())
        await self._set_member_needs(
            ctx.author,
            hunger=self._clamp_need(hunger - 10.0),
            energy=100.0,
            happiness=100.0,
        )
        await member_conf.last_sleep_ts.set(now)
        await member_conf.sleep_lock_until_ts.set(now + (8.0 * 3600.0))
        await ctx.send(
            "You went to sleep, fully recovered your energy and happiness, "
            "and you cannot use LifeSim commands for 8 hours."
        )

    @lifesim_group.group(name="jobs")
    @commands.guild_only()
    async def lifesim_jobs_group(self, ctx):
        """Browse and manage available jobs."""
        if not await self._require_active_state(ctx):
            return
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @lifesim_jobs_group.command(name="list")
    async def lifesim_jobs_list(self, ctx):
        """List all jobs in the server."""
        if not await self._require_active_state(ctx):
            return
        guild_data = await self._get_guild_data(ctx.guild)
        jobs = guild_data["jobs"]
        lines = [self._format_job_line(key, job) for key, job in sorted(jobs.items())]
        if not lines:
            await ctx.send("No jobs are configured yet.")
            return
        for page in pagify("\n".join(lines), page_length=1900):
            await ctx.send(f"Available jobs:\n{page}")

    @lifesim_jobs_group.command(name="info")
    async def lifesim_jobs_info(self, ctx, job_key: str):
        """Show details for a job."""
        if not await self._require_active_state(ctx):
            return
        guild_data = await self._get_guild_data(ctx.guild)
        key = self._normalize_key(job_key)
        job = guild_data["jobs"].get(key)
        if job is None:
            await ctx.send("That job does not exist.")
            return

        await ctx.send(
            f"**{job['name']}** (`{key}`)\n"
            f"Pay: {self._currency(job['pay'])}\n"
            f"Cooldown: {int(job['cooldown_minutes'])} minutes\n"
            f"Costs: hunger {int(job['hunger_cost'])}, energy {int(job['energy_cost'])}, happiness {int(job['happiness_cost'])}\n"
            f"XP gain: {int(job['xp_gain'])}\n"
            f"{job['description']}"
        )

    @lifesim_jobs_group.command(name="apply")
    async def lifesim_jobs_apply(self, ctx, job_key: str):
        """Set your active job."""
        if not await self._require_active_state(ctx):
            return
        guild_data = await self._get_guild_data(ctx.guild)
        key = self._normalize_key(job_key)
        job = guild_data["jobs"].get(key)
        if job is None:
            await ctx.send("That job does not exist.")
            return

        await self.config.member(ctx.author).job_key.set(key)
        await ctx.send(f"You are now working as **{job['name']}**.")

    @lifesim_jobs_group.command(name="clear")
    async def lifesim_jobs_clear(self, ctx):
        """Leave your current job."""
        if not await self._require_active_state(ctx):
            return
        await self.config.member(ctx.author).job_key.set("")
        await ctx.send("You are now unemployed.")

    @lifesim_jobs_group.group(name="admin")
    @commands.admin_or_permissions(manage_guild=True)
    async def lifesim_jobs_admin_group(self, ctx):
        """Admin tools for jobs."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @lifesim_jobs_admin_group.command(name="add")
    async def lifesim_jobs_admin_add(
        self,
        ctx,
        key: str,
        pay: int,
        energy_cost: int,
        hunger_cost: int,
        happiness_cost: int,
        cooldown_minutes: int,
        *,
        name: str,
    ):
        """Add a new job."""
        guild_conf = self.config.guild(ctx.guild)
        norm_key = self._normalize_key(key)
        async with guild_conf.jobs() as jobs:
            jobs[norm_key] = {
                "name": name,
                "pay": float(pay),
                "energy_cost": float(energy_cost),
                "hunger_cost": float(hunger_cost),
                "happiness_cost": float(happiness_cost),
                "cooldown_minutes": float(cooldown_minutes),
                "xp_gain": max(1.0, float(pay) / 10.0),
                "description": "Custom job configured in Discord.",
            }
        await ctx.send(f"Added job `{norm_key}`.")

    @lifesim_jobs_admin_group.command(name="edit")
    async def lifesim_jobs_admin_edit(
        self,
        ctx,
        key: str,
        field: str,
        *,
        value: str,
    ):
        """Edit a field on an existing job."""
        guild_conf = self.config.guild(ctx.guild)
        norm_key = self._normalize_key(key)
        field_name = self._normalize_key(field)
        valid_fields = {
            "name",
            "description",
            "pay",
            "energy_cost",
            "hunger_cost",
            "happiness_cost",
            "cooldown_minutes",
            "xp_gain",
        }
        if field_name not in valid_fields:
            await ctx.send("That field is not editable.")
            return

        async with guild_conf.jobs() as jobs:
            job = jobs.get(norm_key)
            if job is None:
                await ctx.send("That job does not exist.")
                return

            if field_name in {"name", "description"}:
                job[field_name] = value
            else:
                job[field_name] = float(value)

        await ctx.send(f"Updated `{norm_key}` `{field_name}`.")

    @lifesim_jobs_admin_group.command(name="remove")
    async def lifesim_jobs_admin_remove(self, ctx, key: str):
        """Remove a job."""
        guild_conf = self.config.guild(ctx.guild)
        norm_key = self._normalize_key(key)
        async with guild_conf.jobs() as jobs:
            if norm_key not in jobs:
                await ctx.send("That job does not exist.")
                return
            del jobs[norm_key]
        await ctx.send(f"Removed job `{norm_key}`.")

    @lifesim_jobs_admin_group.command(name="resetdefaults")
    async def lifesim_jobs_admin_resetdefaults(self, ctx):
        """Restore the built-in default jobs."""
        await self.config.guild(ctx.guild).jobs.set(copy.deepcopy(self._default_jobs()))
        await ctx.send("Restored the default jobs.")

    @lifesim_group.group(name="shop")
    async def lifesim_shop_group(self, ctx):
        """Browse and manage food items."""
        if not await self._require_active_state(ctx):
            return
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @lifesim_shop_group.command(name="list")
    async def lifesim_shop_list(self, ctx):
        """List all food items."""
        if not await self._require_active_state(ctx):
            return
        guild_data = await self._get_guild_data(ctx.guild)
        foods = guild_data["foods"]
        lines = [self._format_food_line(key, food) for key, food in sorted(foods.items())]
        if not lines:
            await ctx.send("No food items are configured yet.")
            return
        for page in pagify("\n".join(lines), page_length=1900):
            await ctx.send(f"Food shop:\n{page}")

    @lifesim_shop_group.command(name="info")
    async def lifesim_shop_info(self, ctx, item_key: str):
        """Show details for a food item."""
        if not await self._require_active_state(ctx):
            return
        guild_data = await self._get_guild_data(ctx.guild)
        key = self._normalize_key(item_key)
        food = guild_data["foods"].get(key)
        if food is None:
            await ctx.send("That item does not exist.")
            return
        await ctx.send(
            f"**{food['name']}** (`{key}`)\n"
            f"Price: {self._currency(food['price'])}\n"
            f"Restores: hunger {int(food['hunger_restore'])}, energy {int(food['energy_restore'])}, "
            f"happiness {int(food['happiness_restore'])}\n"
            f"{food['description']}"
        )

    @lifesim_shop_group.command(name="buy")
    async def lifesim_shop_buy(self, ctx, item_key: str, quantity: int = 1):
        """Buy food into your inventory."""
        if not await self._require_active_state(ctx):
            return
        if quantity <= 0:
            await ctx.send("Quantity must be at least 1.")
            return

        guild_data = await self._get_guild_data(ctx.guild)
        key = self._normalize_key(item_key)
        food = guild_data["foods"].get(key)
        if food is None:
            await ctx.send("That item does not exist.")
            return

        total_cost = int(round(float(food["price"]) * quantity))
        if not await bank.can_spend(ctx.author, total_cost):
            await ctx.send(
                f"You need {self._currency(total_cost)} credits, but you cannot afford that."
            )
            return

        await bank.withdraw_credits(ctx.author, total_cost)
        member_conf = self.config.member(ctx.author)
        async with member_conf.inventory() as inventory:
            inventory[key] = int(inventory.get(key, 0)) + quantity
        await ctx.send(
            f"You bought {quantity}x **{food['name']}** for {self._currency(total_cost)} credits."
        )

    @lifesim_shop_group.group(name="admin")
    @commands.admin_or_permissions(manage_guild=True)
    async def lifesim_shop_admin_group(self, ctx):
        """Admin tools for the food shop."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @lifesim_shop_admin_group.command(name="add")
    async def lifesim_shop_admin_add(
        self,
        ctx,
        key: str,
        price: int,
        hunger_restore: int,
        energy_restore: int,
        happiness_restore: int,
        *,
        name: str,
    ):
        """Add a new food item."""
        norm_key = self._normalize_key(key)
        async with self.config.guild(ctx.guild).foods() as foods:
            foods[norm_key] = {
                "name": name,
                "price": float(price),
                "hunger_restore": float(hunger_restore),
                "energy_restore": float(energy_restore),
                "happiness_restore": float(happiness_restore),
                "description": "Custom food item configured in Discord.",
            }
        await ctx.send(f"Added food item `{norm_key}`.")

    @lifesim_shop_admin_group.command(name="edit")
    async def lifesim_shop_admin_edit(self, ctx, key: str, field: str, *, value: str):
        """Edit a field on an existing food item."""
        norm_key = self._normalize_key(key)
        field_name = self._normalize_key(field)
        valid_fields = {
            "name",
            "description",
            "price",
            "hunger_restore",
            "energy_restore",
            "happiness_restore",
        }
        if field_name not in valid_fields:
            await ctx.send("That field is not editable.")
            return

        async with self.config.guild(ctx.guild).foods() as foods:
            food = foods.get(norm_key)
            if food is None:
                await ctx.send("That item does not exist.")
                return
            if field_name in {"name", "description"}:
                food[field_name] = value
            else:
                food[field_name] = float(value)
        await ctx.send(f"Updated `{norm_key}` `{field_name}`.")

    @lifesim_shop_admin_group.command(name="remove")
    async def lifesim_shop_admin_remove(self, ctx, key: str):
        """Remove a food item."""
        norm_key = self._normalize_key(key)
        async with self.config.guild(ctx.guild).foods() as foods:
            if norm_key not in foods:
                await ctx.send("That item does not exist.")
                return
            del foods[norm_key]
        await ctx.send(f"Removed food item `{norm_key}`.")

    @lifesim_shop_admin_group.command(name="resetdefaults")
    async def lifesim_shop_admin_resetdefaults(self, ctx):
        """Restore the built-in default food shop."""
        await self.config.guild(ctx.guild).foods.set(copy.deepcopy(self._default_foods()))
        await ctx.send("Restored the default food shop.")

    @lifesim_group.command(name="inventory")
    async def lifesim_inventory(self, ctx):
        """Show your food inventory."""
        if not await self._require_active_state(ctx):
            return
        member_conf = self.config.member(ctx.author)
        inventory = await member_conf.inventory()
        if not inventory:
            await ctx.send("Your inventory is empty.")
            return

        guild_data = await self._get_guild_data(ctx.guild)
        foods = guild_data["foods"]
        lines = []
        for key, qty in sorted(inventory.items()):
            item = foods.get(key)
            name = item["name"] if item else key
            lines.append(f"`{key}` — {name} x{int(qty)}")
        await ctx.send("Inventory:\n" + "\n".join(lines))

    @lifesim_group.command(name="eat")
    async def lifesim_eat(self, ctx, item_key: str, quantity: int = 1):
        """Consume food from your inventory."""
        if not await self._require_active_state(ctx):
            return
        if quantity <= 0:
            await ctx.send("Quantity must be at least 1.")
            return

        guild_data = await self._get_guild_data(ctx.guild)
        key = self._normalize_key(item_key)
        food = guild_data["foods"].get(key)
        if food is None:
            await ctx.send("That item does not exist.")
            return

        member_conf = self.config.member(ctx.author)
        async with member_conf.inventory() as inventory:
            owned = int(inventory.get(key, 0))
            if owned < quantity:
                await ctx.send("You do not have enough of that item.")
                return
            inventory[key] = owned - quantity
            if inventory[key] <= 0:
                del inventory[key]

        hunger = float(await member_conf.hunger())
        energy = float(await member_conf.energy())
        happiness = float(await member_conf.happiness())
        hunger_gain = float(food["hunger_restore"]) * quantity
        energy_gain = float(food["energy_restore"]) * quantity
        happiness_gain = float(food["happiness_restore"]) * quantity
        await member_conf.hunger.set(self._clamp_need(hunger + hunger_gain))
        await member_conf.energy.set(self._clamp_need(energy + energy_gain))
        await member_conf.happiness.set(self._clamp_need(happiness + happiness_gain))

        await ctx.send(
            f"You ate {quantity}x **{food['name']}** and gained "
            f"{int(round(hunger_gain))} hunger, {int(round(energy_gain))} energy, "
            f"{int(round(happiness_gain))} happiness."
        )

    @lifesim_group.group(name="house")
    async def lifesim_house_group(self, ctx):
        """Browse and manage houses."""
        if not await self._require_active_state(ctx):
            return
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @lifesim_house_group.command(name="list")
    async def lifesim_house_list(self, ctx):
        """List all houses."""
        if not await self._require_active_state(ctx):
            return
        guild_data = await self._get_guild_data(ctx.guild)
        houses = guild_data["houses"]
        lines = [self._format_house_line(key, house) for key, house in sorted(houses.items())]
        if not lines:
            await ctx.send("No houses are configured yet.")
            return
        for page in pagify("\n".join(lines), page_length=1900):
            await ctx.send(f"Houses:\n{page}")

    @lifesim_house_group.command(name="info")
    async def lifesim_house_info(self, ctx, house_key: str):
        """Show details for a house."""
        if not await self._require_active_state(ctx):
            return
        guild_data = await self._get_guild_data(ctx.guild)
        key = self._normalize_key(house_key)
        house = guild_data["houses"].get(key)
        if house is None:
            await ctx.send("That house does not exist.")
            return
        await ctx.send(
            f"**{house['name']}** (`{key}`)\n"
            f"Price: {self._currency(house['price'])}\n"
            f"Upkeep: {self._currency(house['upkeep'])} per day\n"
            f"Work bonus: +{int(round(float(house['work_bonus']) * 100))}%\n"
            f"Rest bonus: +{int(house['rest_energy'])} energy, +{int(house['rest_happiness'])} happiness\n"
            f"{house['description']}"
        )

    @lifesim_house_group.command(name="buy")
    async def lifesim_house_buy(self, ctx, house_key: str):
        """Buy and move into a house."""
        if not await self._require_active_state(ctx):
            return
        guild_data = await self._get_guild_data(ctx.guild)
        key = self._normalize_key(house_key)
        house = guild_data["houses"].get(key)
        if house is None:
            await ctx.send("That house does not exist.")
            return

        price = int(round(float(house["price"])))
        if not await bank.can_spend(ctx.author, price):
            await ctx.send(f"You need {self._currency(price)} credits, but you cannot afford that.")
            return

        await bank.withdraw_credits(ctx.author, price)
        member_conf = self.config.member(ctx.author)
        await member_conf.house_key.set(key)
        await member_conf.last_upkeep_ts.set(time.time())
        await ctx.send(f"You bought and moved into **{house['name']}** for {self._currency(price)} credits.")

    @lifesim_house_group.command(name="current")
    async def lifesim_house_current(self, ctx):
        """Show your current house."""
        if not await self._require_active_state(ctx):
            return
        guild_data = await self._get_guild_data(ctx.guild)
        house = await self._member_house(ctx.author, guild_data)
        if house is None:
            await ctx.send("You do not own a house yet.")
            return
        await ctx.send(
            f"You currently live in **{house['name']}**.\n"
            f"Upkeep: {self._currency(house['upkeep'])} per day | "
            f"Work bonus: +{int(round(float(house['work_bonus']) * 100))}%"
        )

    @lifesim_house_group.group(name="admin")
    @commands.admin_or_permissions(manage_guild=True)
    async def lifesim_house_admin_group(self, ctx):
        """Admin tools for houses."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @lifesim_house_admin_group.command(name="add")
    async def lifesim_house_admin_add(
        self,
        ctx,
        key: str,
        price: int,
        upkeep: int,
        work_bonus: float,
        rest_energy: int,
        rest_happiness: int,
        *,
        name: str,
    ):
        """Add a new house."""
        norm_key = self._normalize_key(key)
        async with self.config.guild(ctx.guild).houses() as houses:
            houses[norm_key] = {
                "name": name,
                "price": float(price),
                "upkeep": float(upkeep),
                "work_bonus": float(work_bonus),
                "rest_energy": float(rest_energy),
                "rest_happiness": float(rest_happiness),
                "description": "Custom house configured in Discord.",
            }
        await ctx.send(f"Added house `{norm_key}`.")

    @lifesim_house_admin_group.command(name="edit")
    async def lifesim_house_admin_edit(self, ctx, key: str, field: str, *, value: str):
        """Edit a field on an existing house."""
        norm_key = self._normalize_key(key)
        field_name = self._normalize_key(field)
        valid_fields = {
            "name",
            "description",
            "price",
            "upkeep",
            "work_bonus",
            "rest_energy",
            "rest_happiness",
        }
        if field_name not in valid_fields:
            await ctx.send("That field is not editable.")
            return

        async with self.config.guild(ctx.guild).houses() as houses:
            house = houses.get(norm_key)
            if house is None:
                await ctx.send("That house does not exist.")
                return
            if field_name in {"name", "description"}:
                house[field_name] = value
            else:
                house[field_name] = float(value)
        await ctx.send(f"Updated `{norm_key}` `{field_name}`.")

    @lifesim_house_admin_group.command(name="remove")
    async def lifesim_house_admin_remove(self, ctx, key: str):
        """Remove a house."""
        norm_key = self._normalize_key(key)
        async with self.config.guild(ctx.guild).houses() as houses:
            if norm_key not in houses:
                await ctx.send("That house does not exist.")
                return
            del houses[norm_key]
        await ctx.send(f"Removed house `{norm_key}`.")

    @lifesim_house_admin_group.command(name="resetdefaults")
    async def lifesim_house_admin_resetdefaults(self, ctx):
        """Restore the built-in default houses."""
        await self.config.guild(ctx.guild).houses.set(copy.deepcopy(self._default_houses()))
        await ctx.send("Restored the default houses.")

    @lifesim_group.group(name="settings")
    @commands.admin_or_permissions(manage_guild=True)
    async def lifesim_settings_group(self, ctx):
        """Edit the simulation settings."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @lifesim_settings_group.command(name="view")
    async def lifesim_settings_view(self, ctx):
        """Show current life simulation settings."""
        guild_data = await self._get_guild_data(ctx.guild)
        settings = guild_data["settings"]
        decay = settings["need_decay_per_hour"]
        rest = settings["rest_restore"]
        await ctx.send(
            "LifeSim settings:\n"
            f"- Starting needs: hunger {int(settings['starting_hunger'])}, "
            f"energy {int(settings['starting_energy'])}, happiness {int(settings['starting_happiness'])}\n"
            f"- Need decay/hour: hunger {decay['hunger']}, energy {decay['energy']}, happiness {decay['happiness']}\n"
            f"- Rest restore: energy {rest['energy']}, happiness {rest['happiness']}\n"
            f"- Work cooldown: {float(settings['work_cooldown_hours'])} hours\n"
            f"- Rest cooldown: {float(settings['rest_cooldown_hours'])} hours\n"
            f"- Sleep cooldown: {float(settings['sleep_cooldown_hours'])} hours\n"
            f"- Upkeep interval: {float(settings['upkeep_interval_hours'])} hours"
        )

    @lifesim_settings_group.command(name="setstarting")
    async def lifesim_settings_setstarting(self, ctx, need: str, value: float):
        """Set starting needs for new members."""
        field = self._normalize_key(need)
        valid_fields = {"hunger", "energy", "happiness"}
        if field not in valid_fields:
            await ctx.send("Need must be hunger, energy, or happiness.")
            return

        async with self.config.guild(ctx.guild).settings() as settings:
            settings[f"starting_{field}"] = float(value)
        await ctx.send(f"Set starting {field} to {value}.")

    @lifesim_settings_group.command(name="setdecay")
    async def lifesim_settings_setdecay(self, ctx, need: str, value: float):
        """Set hourly decay for a need."""
        field = self._normalize_key(need)
        valid_fields = {"hunger", "energy", "happiness"}
        if field not in valid_fields:
            await ctx.send("Need must be hunger, energy, or happiness.")
            return

        async with self.config.guild(ctx.guild).settings() as settings:
            settings["need_decay_per_hour"][field] = float(value)
        await ctx.send(f"Set {field} decay per hour to {value}.")

    @lifesim_settings_group.command(name="setrest")
    async def lifesim_settings_setrest(self, ctx, need: str, value: float):
        """Set the base recovery amount from resting."""
        field = self._normalize_key(need)
        valid_fields = {"energy", "happiness"}
        if field not in valid_fields:
            await ctx.send("Need must be energy or happiness.")
            return

        async with self.config.guild(ctx.guild).settings() as settings:
            settings["rest_restore"][field] = float(value)
        await ctx.send(f"Set rest {field} recovery to {value}.")

    @lifesim_settings_group.command(name="setrestcooldown")
    async def lifesim_settings_setrestcooldown(self, ctx, hours: float):
        """Set how often rest can be used."""
        if hours <= 0:
            await ctx.send("Hours must be greater than 0.")
            return
        async with self.config.guild(ctx.guild).settings() as settings:
            settings["rest_cooldown_hours"] = float(hours)
        await ctx.send(f"Set rest cooldown to {hours} hours.")

    @lifesim_settings_group.command(name="setworkcooldown")
    async def lifesim_settings_setworkcooldown(self, ctx, hours: float):
        """Set how often work can be used."""
        if hours <= 0:
            await ctx.send("Hours must be greater than 0.")
            return
        async with self.config.guild(ctx.guild).settings() as settings:
            settings["work_cooldown_hours"] = float(hours)
        await ctx.send(f"Set work cooldown to {hours} hours.")

    @lifesim_settings_group.command(name="setsleepcooldown")
    async def lifesim_settings_setsleepcooldown(self, ctx, hours: float):
        """Set how often sleep can be used."""
        if hours <= 0:
            await ctx.send("Hours must be greater than 0.")
            return
        async with self.config.guild(ctx.guild).settings() as settings:
            settings["sleep_cooldown_hours"] = float(hours)
        await ctx.send(f"Set sleep cooldown to {hours} hours.")

    @lifesim_settings_group.command(name="setupkeep")
    async def lifesim_settings_setupkeep(self, ctx, hours: float):
        """Set how often house upkeep is charged."""
        if hours <= 0:
            await ctx.send("Hours must be greater than 0.")
            return
        async with self.config.guild(ctx.guild).settings() as settings:
            settings["upkeep_interval_hours"] = float(hours)
        await ctx.send(f"Set upkeep interval to {hours} hours.")

    @lifesim_settings_group.command(name="resetdefaults")
    async def lifesim_settings_resetdefaults(self, ctx):
        """Restore the built-in default settings."""
        await self.config.guild(ctx.guild).settings.set(copy.deepcopy(self._default_settings()))
        await ctx.send("Restored the default LifeSim settings.")

    @lifesim_group.group(name="member")
    @commands.admin_or_permissions(manage_guild=True)
    async def lifesim_member_group(self, ctx):
        """Admin tools for member state and cooldowns."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @lifesim_member_group.command(name="set")
    async def lifesim_member_set(
        self,
        ctx,
        member: discord.Member,
        field: str,
        *,
        value: str,
    ):
        """Set a member's life state."""
        field_name = self._normalize_key(field)
        valid_fields = {"hunger", "energy", "happiness", "xp", "job", "house"}
        if field_name not in valid_fields:
            await ctx.send("Field must be hunger, energy, happiness, xp, job, or house.")
            return

        guild_data = await self._get_guild_data(ctx.guild)
        member_conf = self.config.member(member)
        clear_tokens = {"", "none", "clear", "null"}

        if field_name in {"hunger", "energy", "happiness"}:
            await self._set_member_needs(member, **{field_name: float(value)})
        elif field_name == "xp":
            await member_conf.career_xp.set(max(0, int(float(value))))
        elif field_name == "job":
            job_key = self._normalize_key(value)
            if value.strip().lower() in clear_tokens:
                job_key = ""
            elif job_key not in guild_data["jobs"]:
                await ctx.send("That job does not exist.")
                return
            await member_conf.job_key.set(job_key)
        elif field_name == "house":
            house_key = self._normalize_key(value)
            if value.strip().lower() in clear_tokens:
                house_key = ""
            elif house_key not in guild_data["houses"]:
                await ctx.send("That house does not exist.")
                return
            await member_conf.house_key.set(house_key)
            await member_conf.last_upkeep_ts.set(time.time() if house_key else 0.0)

        await ctx.send(f"Updated {member.display_name}'s {field_name}.")

    @lifesim_member_group.group(name="cooldowns")
    @commands.admin_or_permissions(manage_guild=True)
    async def lifesim_member_cooldowns_group(self, ctx):
        """Admin tools for cooldowns."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @lifesim_member_cooldowns_group.command(name="reset")
    async def lifesim_member_cooldowns_reset(self, ctx, member: discord.Member):
        """Reset all LifeSim cooldowns for a member."""
        await self._reset_member_cooldowns(member)
        await ctx.send(f"Reset LifeSim cooldowns for {member.display_name}.")


async def setup(bot):
    await bot.add_cog(LifeSim(bot))
