import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import discord
from redbot.core import Config, commands
from redbot.core.utils.chat_formatting import humanize_number


log = logging.getLogger("red.quickduel")


REACTION_DELAY_RANGE = (2, 6)
ROUND_TIMEOUT_SECONDS = 20
CHALLENGE_TIMEOUT_SECONDS = 60
DEFAULT_ROUNDS = 5
MIN_ROUNDS = 3
MAX_ROUNDS = 9

ODD_EMOJIS = ["🍎", "🍊", "🍌", "🍐", "🍇", "🥝", "🍓", "🍋", "🫐"]
MATH_OPERATORS = ["+", "-", "×"]


@dataclass
class RoundOutcome:
    winner_id: Optional[int]
    title: str
    description: str
    color: discord.Color = field(default_factory=discord.Color.blurple)


@dataclass
class DuelSession:
    guild_id: int
    channel_id: int
    challenger: discord.Member
    opponent: discord.Member
    rounds_target: int
    challenge_message: Optional[discord.Message] = None
    duel_message: Optional[discord.Message] = None
    task: Optional[asyncio.Task] = None
    state: str = "pending"
    round_index: int = 0
    challenger_round_wins: int = 0
    opponent_round_wins: int = 0
    started_at: float = field(default_factory=time.time)

    @property
    def participant_ids(self):
        return {self.challenger.id, self.opponent.id}

    @property
    def rounds_needed(self):
        return self.rounds_target // 2 + 1


class DuelChallengeView(discord.ui.View):
    def __init__(self, cog, session: DuelSession):
        super().__init__(timeout=CHALLENGE_TIMEOUT_SECONDS)
        self.cog = cog
        self.session = session
        self.result: asyncio.Future = asyncio.get_running_loop().create_future()
        self._decision_lock = asyncio.Lock()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.session.opponent.id:
            await interaction.response.send_message(
                embed=self.cog._error_embed("Only the challenged member can respond to this duel."),
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="✅")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self._decision_lock:
            if not self.result.done():
                self.result.set_result("accepted")
            self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="❌")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self._decision_lock:
            if not self.result.done():
                self.result.set_result("declined")
            self.stop()
        await interaction.response.defer()

    async def on_timeout(self):
        if not self.result.done():
            self.result.set_result("timeout")


class BaseRoundView(discord.ui.View):
    def __init__(self, cog, session: DuelSession, round_name: str, prompt: str):
        super().__init__(timeout=ROUND_TIMEOUT_SECONDS)
        self.cog = cog
        self.session = session
        self.round_name = round_name
        self.prompt = prompt
        self.result: asyncio.Future = asyncio.get_running_loop().create_future()
        self.message: Optional[discord.Message] = None
        self._ended = False
        self._resolve_lock = asyncio.Lock()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in self.session.participant_ids:
            await interaction.response.send_message(
                embed=self.cog._error_embed("Only the two duel participants can use these buttons."),
                ephemeral=True,
            )
            return False
        return True

    def _make_embed(self, title: str, description: str, color: discord.Color = None):
        embed = discord.Embed(title=title, description=description, color=color or discord.Color.blurple())
        embed.add_field(name="Round", value=f"{self.session.round_index}/{self.session.rounds_target}", inline=True)
        embed.add_field(
            name="Score",
            value=self.cog._format_score(self.session),
            inline=True,
        )
        return embed

    async def _finish(self, outcome: RoundOutcome):
        async with self._resolve_lock:
            if self._ended:
                return
            self._ended = True
            for child in self.children:
                if hasattr(child, "disabled"):
                    child.disabled = True
            if not self.result.done():
                self.result.set_result(outcome)
            self.stop()

    async def on_timeout(self):
        if self._ended:
            return
        outcome = RoundOutcome(
            winner_id=None,
            title=f"{self.round_name} Timeout",
            description="⏰ No one won this round in time.",
            color=discord.Color.orange(),
        )
        await self._finish(outcome)


class ReactionRoundView(BaseRoundView):
    def __init__(self, cog, session: DuelSession):
        super().__init__(cog, session, "Reaction", "Wait...")
        self.ready = False
        self.button = discord.ui.Button(label="WAIT...", style=discord.ButtonStyle.secondary, emoji="⏳")
        self.button.callback = self._button_callback
        self.add_item(self.button)

    async def start(self):
        await asyncio.sleep(random.randint(*REACTION_DELAY_RANGE))
        if self._ended:
            return
        self.ready = True
        self.button.label = "CLICK NOW!"
        self.button.style = discord.ButtonStyle.success
        if self.message:
            try:
                await self.message.edit(embed=self.build_embed(), view=self)
            except discord.HTTPException:
                pass

    def build_embed(self):
        if self.ready:
            desc = "⚡ CLICK NOW! First click wins."
            color = discord.Color.green()
        else:
            desc = "Wait for the signal. Clicking early loses."
            color = discord.Color.gold()
        return self._make_embed("⚡ Reaction Duel", desc, color)

    async def _button_callback(self, interaction: discord.Interaction):
        if self._ended:
            await interaction.response.defer()
            return

        clicker = interaction.user
        if not self.ready:
            winner_id = self.cog._other_participant(self.session, clicker.id)
            outcome = RoundOutcome(
                winner_id=winner_id,
                title="Too Soon!",
                description=f"❌ {clicker.display_name} clicked before the signal.",
                color=discord.Color.red(),
            )
        else:
            outcome = RoundOutcome(
                winner_id=clicker.id,
                title="Reaction Winner!",
                description=f"✅ {clicker.display_name} clicked first.",
                color=discord.Color.green(),
            )
        await self._finish(outcome)
        await interaction.response.defer()


class OddEmojiRoundView(BaseRoundView):
    def __init__(self, cog, session: DuelSession):
        self.normal_emoji = random.choice(ODD_EMOJIS)
        self.odd_emoji = random.choice([e for e in ODD_EMOJIS if e != self.normal_emoji])
        self.odd_index = random.randint(0, 8)
        super().__init__(cog, session, "Odd Emoji", "Find the different emoji.")
        self._build_buttons()

    def _build_buttons(self):
        for index in range(9):
            emoji = self.odd_emoji if index == self.odd_index else self.normal_emoji
            button = discord.ui.Button(emoji=emoji, style=discord.ButtonStyle.secondary, row=index // 3)

            async def callback(interaction: discord.Interaction, idx=index, btn=button, picked=emoji):
                if self._ended:
                    await interaction.response.defer()
                    return

                clicker = interaction.user
                if picked == self.odd_emoji:
                    winner_id = clicker.id
                    description = f"✅ {clicker.display_name} found the odd emoji."
                    title = "Odd Emoji Winner!"
                    color = discord.Color.green()
                else:
                    winner_id = self.cog._other_participant(self.session, clicker.id)
                    description = f"❌ {clicker.display_name} clicked the wrong emoji."
                    title = "Wrong Emoji!"
                    color = discord.Color.red()

                await self._finish(RoundOutcome(winner_id=winner_id, title=title, description=description, color=color))
                await interaction.response.defer()

            button.callback = callback
            self.add_item(button)

    def build_embed(self):
        return self._make_embed(
            "🧩 Odd Emoji Duel",
            "Click the odd emoji first.",
            discord.Color.blurple(),
        )


class MathRoundView(BaseRoundView):
    def __init__(self, cog, session: DuelSession):
        self.a = random.randint(1, 20)
        self.b = random.randint(1, 20)
        self.operator = random.choice(MATH_OPERATORS)
        self.correct_answer = self._compute_answer()
        self.attempted_users = set()
        super().__init__(cog, session, "Math", "Solve the equation.")
        self._build_buttons()

    def _compute_answer(self):
        if self.operator == "+":
            return self.a + self.b
        if self.operator == "-":
            return self.a - self.b
        return self.a * self.b

    def _build_buttons(self):
        answers = {self.correct_answer}
        while len(answers) < 4:
            delta = random.randint(-8, 8) or random.choice([-5, -4, 4, 5])
            answers.add(self.correct_answer + delta)
        answers = list(answers)
        random.shuffle(answers)

        for answer in answers:
            button = discord.ui.Button(label=str(answer), style=discord.ButtonStyle.primary)

            async def callback(interaction: discord.Interaction, value=answer):
                if self._ended:
                    await interaction.response.defer()
                    return

                if interaction.user.id in self.attempted_users:
                    await interaction.response.send_message(
                        embed=self.cog._error_embed("You already answered this round."),
                        ephemeral=True,
                    )
                    return

                self.attempted_users.add(interaction.user.id)
                if value == self.correct_answer:
                    await self._finish(
                        RoundOutcome(
                            winner_id=interaction.user.id,
                            title="Correct Answer!",
                            description=f"✅ {interaction.user.display_name} solved the math problem.",
                            color=discord.Color.green(),
                        )
                    )
                elif len(self.attempted_users) >= 2:
                    await self._finish(
                        RoundOutcome(
                            winner_id=None,
                            title="No Correct Answer",
                            description="⏰ Both players answered incorrectly.",
                            color=discord.Color.orange(),
                        )
                    )
                await interaction.response.defer()

            button.callback = callback
            self.add_item(button)

    def build_embed(self):
        return self._make_embed(
            "➕ Math Duel",
            f"Solve this first: **{self.a} {self.operator} {self.b} = ?**\nWrong answers lock you out for the round.",
            discord.Color.blurple(),
        )


class QuickDuel(commands.Cog):
    """Simple PvP duel mini-games with persistent stats."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2405202601, force_registration=True)
        self.config.register_guild(channel_id=0, default_rounds=DEFAULT_ROUNDS)
        self.config.register_member(
            wins=0,
            losses=0,
            games_played=0,
            win_streak=0,
            best_win_streak=0,
            rounds_won=0,
            rounds_lost=0,
        )
        self.active_duels: Dict[int, DuelSession] = {}
        self.active_users: Dict[int, int] = {}

    def cog_unload(self):
        for session in list(self.active_duels.values()):
            if session.task and not session.task.done():
                session.task.cancel()
        self.active_duels.clear()
        self.active_users.clear()

    def _error_embed(self, message: str) -> discord.Embed:
        return discord.Embed(title="QuickDuel", description=message, color=discord.Color.red())

    def _info_embed(self, title: str, message: str, color: discord.Color = None) -> discord.Embed:
        return discord.Embed(title=title, description=message, color=color or discord.Color.blurple())

    def _format_score(self, session: DuelSession) -> str:
        return (
            f"{session.challenger.display_name}: **{session.challenger_round_wins}**\n"
            f"{session.opponent.display_name}: **{session.opponent_round_wins}**"
        )

    def _other_participant(self, session: DuelSession, user_id: int) -> int:
        return session.opponent.id if user_id == session.challenger.id else session.challenger.id

    def _can_start_duel(self, guild_id: int, user_ids: List[int]) -> Optional[str]:
        if guild_id in self.active_duels:
            return "There is already an active duel in this server."
        for user_id in user_ids:
            if user_id in self.active_users:
                return "One of those users is already in an active duel."
        return None

    async def _register_session(self, session: DuelSession):
        self.active_duels[session.guild_id] = session
        self.active_users[session.challenger.id] = session.guild_id
        self.active_users[session.opponent.id] = session.guild_id

    async def _cleanup_session(self, session: DuelSession):
        if self.active_duels.get(session.guild_id) is session:
            self.active_duels.pop(session.guild_id, None)
        self.active_users.pop(session.challenger.id, None)
        self.active_users.pop(session.opponent.id, None)
        current_task = asyncio.current_task()
        if session.task and session.task is not current_task and not session.task.done():
            session.task.cancel()

    async def _increment_round_stats(self, winner: discord.Member, loser: discord.Member):
        await self.config.member(winner).rounds_won.set((await self.config.member(winner).rounds_won()) + 1)
        await self.config.member(loser).rounds_lost.set((await self.config.member(loser).rounds_lost()) + 1)

    async def _build_round_embed(self, session: DuelSession, round_view: BaseRoundView) -> discord.Embed:
        embed = round_view.build_embed()
        embed.set_footer(text=f"Best-of-{session.rounds_target} • First to {session.rounds_needed} wins")
        return embed

    async def _run_match(self, session: DuelSession):
        try:
            if session.challenge_message:
                await session.challenge_message.edit(
                    embed=self._info_embed(
                        "QuickDuel",
                        f"✅ Duel accepted!\n{session.challenger.mention} vs {session.opponent.mention}",
                        discord.Color.green(),
                    ),
                    view=None,
                )

            while True:
                if session.challenger_round_wins >= session.rounds_needed:
                    await self._finish_match(session, session.challenger, session.opponent)
                    return
                if session.opponent_round_wins >= session.rounds_needed:
                    await self._finish_match(session, session.opponent, session.challenger)
                    return

                if session.round_index >= session.rounds_target and session.challenger_round_wins != session.opponent_round_wins:
                    winner = session.challenger if session.challenger_round_wins > session.opponent_round_wins else session.opponent
                    loser = session.opponent if winner is session.challenger else session.challenger
                    await self._finish_match(session, winner, loser)
                    return

                if session.round_index >= session.rounds_target and session.challenger_round_wins == session.opponent_round_wins:
                    session.rounds_target += 1

                session.round_index += 1
                round_view = self._pick_round_view(session)
                round_embed = await self._build_round_embed(session, round_view)
                if session.duel_message is None:
                    session.duel_message = session.challenge_message
                await session.duel_message.edit(embed=round_embed, view=round_view)
                round_view.message = session.duel_message

                activation_task = None
                if isinstance(round_view, ReactionRoundView):
                    activation_task = asyncio.create_task(round_view.start())

                outcome = await round_view.result
                if activation_task:
                    activation_task.cancel()

                if outcome.winner_id is None:
                    result_embed = self._info_embed(outcome.title, outcome.description, outcome.color)
                else:
                    winner = session.challenger if outcome.winner_id == session.challenger.id else session.opponent
                    loser = session.opponent if winner is session.challenger else session.challenger
                    if winner.id == session.challenger.id:
                        session.challenger_round_wins += 1
                    else:
                        session.opponent_round_wins += 1
                    await self._increment_round_stats(winner, loser)
                    result_embed = self._info_embed(
                        outcome.title,
                        f"{outcome.description}\n\n{self._format_score(session)}",
                        outcome.color,
                    )

                await session.duel_message.edit(embed=result_embed, view=None)

                if outcome.winner_id is not None:
                    remaining = session.rounds_target - session.round_index
                    if session.challenger_round_wins > session.opponent_round_wins + remaining:
                        await self._finish_match(session, session.challenger, session.opponent)
                        return
                    if session.opponent_round_wins > session.challenger_round_wins + remaining:
                        await self._finish_match(session, session.opponent, session.challenger)
                        return

                await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("QuickDuel match failed")
            if session.duel_message:
                try:
                    await session.duel_message.edit(
                        embed=self._error_embed("An unexpected error ended the duel."),
                        view=None,
                    )
                except discord.HTTPException:
                    pass
        finally:
            await self._cleanup_session(session)

    def _pick_round_view(self, session: DuelSession) -> BaseRoundView:
        choice = random.choice(["reaction", "odd", "math"])
        if choice == "reaction":
            return ReactionRoundView(self, session)
        if choice == "odd":
            return OddEmojiRoundView(self, session)
        return MathRoundView(self, session)

    async def _finish_match(self, session: DuelSession, winner: discord.Member, loser: discord.Member):
        session.state = "finished"
        await self.config.member(winner).wins.set((await self.config.member(winner).wins()) + 1)
        await self.config.member(loser).losses.set((await self.config.member(loser).losses()) + 1)
        await self.config.member(winner).games_played.set((await self.config.member(winner).games_played()) + 1)
        await self.config.member(loser).games_played.set((await self.config.member(loser).games_played()) + 1)
        await self.config.member(loser).win_streak.set(0)
        winner_streak = (await self.config.member(winner).win_streak()) + 1
        await self.config.member(winner).win_streak.set(winner_streak)
        if winner_streak > (await self.config.member(winner).best_win_streak()):
            await self.config.member(winner).best_win_streak.set(winner_streak)

        final_embed = self._info_embed(
            "🏆 Duel Finished",
            f"**Winner:** {winner.mention}\n**Loser:** {loser.mention}\n\nFinal score:\n{self._format_score(session)}",
            discord.Color.green(),
        )
        if session.duel_message:
            try:
                await session.duel_message.edit(embed=final_embed, view=None)
            except discord.HTTPException:
                pass

    @commands.hybrid_group(name="duel", invoke_without_command=True)
    @commands.guild_only()
    async def duel(self, ctx: commands.Context, member: discord.Member):
        """Challenge another member to a best-of duel."""
        if member.bot:
            return await ctx.send(embed=self._error_embed("You cannot challenge bots."))
        if member.id == ctx.author.id:
            return await ctx.send(embed=self._error_embed("You cannot duel yourself."))
        restriction = await self.config.guild(ctx.guild).channel_id()
        if restriction and ctx.channel.id != restriction:
            channel = ctx.guild.get_channel(restriction)
            name = channel.mention if channel else "the configured duel channel"
            return await ctx.send(embed=self._error_embed(f"Duels can only be started in {name}."))
        conflict = self._can_start_duel(ctx.guild.id, [ctx.author.id, member.id])
        if conflict:
            return await ctx.send(embed=self._error_embed(conflict))

        rounds = int(await self.config.guild(ctx.guild).default_rounds())
        session = DuelSession(
            guild_id=ctx.guild.id,
            channel_id=ctx.channel.id,
            challenger=ctx.author,
            opponent=member,
            rounds_target=rounds,
        )
        await self._register_session(session)

        challenge_embed = self._info_embed(
            "⚔️ Duel Challenge",
            f"{ctx.author.mention} challenged {member.mention}!\n\n"
            f"Best of {rounds} rounds.\n"
            f"Accept within {CHALLENGE_TIMEOUT_SECONDS} seconds.",
            discord.Color.gold(),
        )
        view = DuelChallengeView(self, session)
        challenge_message = await ctx.send(embed=challenge_embed, view=view)
        session.challenge_message = challenge_message

        decision = await view.result
        if decision != "accepted":
            title = "Challenge Expired" if decision == "timeout" else "Challenge Declined"
            desc = (
                f"⏰ {member.mention} did not respond in time."
                if decision == "timeout"
                else f"❌ {member.mention} declined the challenge."
            )
            await challenge_message.edit(embed=self._info_embed(title, desc, discord.Color.red()), view=None)
            await self._cleanup_session(session)
            return

        session.state = "active"
        session.task = asyncio.create_task(self._run_match(session))

    @duel.command(name="stats")
    @commands.guild_only()
    async def duel_stats(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        member = member or ctx.author
        data = await self.config.member(member).all()
        embed = discord.Embed(title=f"QuickDuel Stats - {member.display_name}", color=discord.Color.blurple())
        embed.add_field(name="Wins", value=humanize_number(data["wins"]), inline=True)
        embed.add_field(name="Losses", value=humanize_number(data["losses"]), inline=True)
        embed.add_field(name="Games Played", value=humanize_number(data["games_played"]), inline=True)
        embed.add_field(name="Win Streak", value=humanize_number(data["win_streak"]), inline=True)
        embed.add_field(name="Best Win Streak", value=humanize_number(data["best_win_streak"]), inline=True)
        embed.add_field(name="Round Wins", value=humanize_number(data["rounds_won"]), inline=True)
        embed.add_field(name="Round Losses", value=humanize_number(data["rounds_lost"]), inline=True)
        await ctx.send(embed=embed)

    @duel.command(name="leaderboard")
    @commands.guild_only()
    async def duel_leaderboard(self, ctx: commands.Context):
        all_members = await self.config.all_members(ctx.guild)
        rows = []
        for member_id, data in all_members.items():
            wins = int(data.get("wins", 0))
            if wins <= 0:
                continue
            member = ctx.guild.get_member(int(member_id))
            name = member.display_name if member else f"User {member_id}"
            rows.append((wins, name, data))
        rows.sort(key=lambda item: (-item[0], item[1].lower()))
        embed = discord.Embed(title="QuickDuel Leaderboard", color=discord.Color.gold())
        if not rows:
            embed.description = "No duel wins yet."
        else:
            lines = []
            for idx, (wins, name, data) in enumerate(rows[:10], start=1):
                lines.append(f"**{idx}. {name}** — {wins} wins")
            embed.description = "\n".join(lines)
        await ctx.send(embed=embed)

    @duel.command(name="cancel")
    @commands.guild_only()
    async def duel_cancel(self, ctx: commands.Context):
        session = self.active_duels.get(ctx.guild.id)
        if not session:
            return await ctx.send(embed=self._error_embed("There is no active duel to cancel."))
        if session.challenger.id != ctx.author.id:
            return await ctx.send(embed=self._error_embed("Only the challenger can cancel this duel."))
        if session.state != "pending":
            return await ctx.send(embed=self._error_embed("You can only cancel before the duel is accepted."))

        if session.challenge_message:
            try:
                await session.challenge_message.edit(
                    embed=self._info_embed(
                        "Duel Cancelled",
                        f"{ctx.author.mention} cancelled the challenge.",
                        discord.Color.red(),
                    ),
                    view=None,
                )
            except discord.HTTPException:
                pass
        await self._cleanup_session(session)
        await ctx.send(embed=self._info_embed("Duel Cancelled", "Your duel challenge was cancelled.", discord.Color.red()))

    @commands.group(name="quickduelset", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def quickduelset(self, ctx: commands.Context):
        await ctx.send(embed=self._info_embed("QuickDuel Settings", "Use `channel` or `rounds` subcommands."))

    @quickduelset.command(name="channel")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def quickduelset_channel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        await self.config.guild(ctx.guild).channel_id.set(channel.id if channel else 0)
        if channel:
            msg = f"Duels are now restricted to {channel.mention}."
        else:
            msg = "Duels are no longer restricted to a single channel."
        await ctx.send(embed=self._info_embed("QuickDuel Settings", msg, discord.Color.green()))

    @quickduelset.command(name="rounds")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def quickduelset_rounds(self, ctx: commands.Context, rounds: int):
        if rounds < MIN_ROUNDS or rounds > MAX_ROUNDS:
            return await ctx.send(embed=self._error_embed("Rounds must be between 3 and 9."))
        await self.config.guild(ctx.guild).default_rounds.set(rounds)
        await ctx.send(embed=self._info_embed("QuickDuel Settings", f"Default rounds set to {rounds}.", discord.Color.green()))


async def setup(bot):
    await bot.add_cog(QuickDuel(bot))
