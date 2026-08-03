import random
import asyncio
import discord
from redbot.core import commands, bank
from redbot.core.utils.chat_formatting import humanize_number


class Deck:
    """Represents a deck of cards for blackjack."""
    
    SUITS = ['♠', '♥', '♦', '♣']
    RANKS = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']
    
    def __init__(self):
        self.cards = []
        self.reset()
    
    def reset(self):
        """Reset deck with all 52 cards."""
        self.cards = [(rank, suit) for suit in self.SUITS for rank in self.RANKS]
        random.shuffle(self.cards)
    
    def draw(self):
        """Draw a card from the deck."""
        if len(self.cards) < 10:
            self.reset()
        return self.cards.pop()
    
    @staticmethod
    def card_to_string(card):
        """Convert card tuple to string representation."""
        return f"{card[0]}{card[1]}"


class Hand:
    """Represents a hand of cards."""
    
    def __init__(self):
        self.cards = []
    
    def add_card(self, card):
        """Add a card to the hand."""
        self.cards.append(card)
    
    def get_value(self):
        """Calculate the best value of the hand."""
        value = 0
        aces = 0
        
        for card in self.cards:
            rank = card[0]
            if rank == 'A':
                aces += 1
                value += 11
            elif rank in ['J', 'Q', 'K']:
                value += 10
            else:
                value += int(rank)
        
        # Adjust for aces if we're over 21
        while value > 21 and aces > 0:
            value -= 10
            aces -= 1
        
        return value
    
    def is_blackjack(self):
        """Check if this hand is a natural blackjack."""
        return len(self.cards) == 2 and self.get_value() == 21
    
    def get_hand_string(self):
        """Get string representation of the hand."""
        cards_str = ' '.join(Deck.card_to_string(card) for card in self.cards)
        return f"{cards_str} | **{self.get_value()}**"


class BlackjackGameView(discord.ui.View):
    """View for blackjack game buttons."""
    
    def __init__(self, game_data, timeout=60):
        super().__init__(timeout=timeout)
        self.game_data = game_data
        self.game_over = False
    
    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary, emoji="🎴")
    async def hit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Player hits - draws another card."""
        if interaction.user.id != self.game_data['player_id']:
            await interaction.response.defer()
            return
        
        self.game_data['player_hand'].add_card(self.game_data['deck'].draw())
        if self.game_data['player_hand'].get_value() > 21:
            self.game_data['status'] = 'bust'
            self.game_over = True
            self.hit_button.disabled = True
            self.stand_button.disabled = True
        
        embed = self._create_game_embed()
        await interaction.response.edit_message(embed=embed, view=self if not self.game_over else None)
        if self.game_over:
            self.stop()
    
    @discord.ui.button(label="Stand", style=discord.ButtonStyle.success, emoji="🛑")
    async def stand_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Player stands - dealer's turn."""
        if interaction.user.id != self.game_data['player_id']:
            await interaction.response.defer()
            return
        
        self.game_data['status'] = 'dealer_playing'
        dealer_hand = self.game_data['dealer_hand']
        
        # Dealer hits on 16 or less, stands on 17 or more
        while dealer_hand.get_value() < 17:
            dealer_hand.add_card(self.game_data['deck'].draw())
        
        # Determine winner
        player_value = self.game_data['player_hand'].get_value()
        dealer_value = dealer_hand.get_value()
        
        if dealer_value > 21:
            self.game_data['status'] = 'dealer_bust'
        elif player_value > dealer_value:
            self.game_data['status'] = 'player_win'
        elif dealer_value > player_value:
            self.game_data['status'] = 'dealer_win'
        else:
            self.game_data['status'] = 'push'
        
        self.game_over = True
        self.hit_button.disabled = True
        self.stand_button.disabled = True
        
        embed = self._create_game_embed()
        await interaction.response.edit_message(embed=embed, view=self if not self.game_over else None)
        if self.game_over:
            self.stop()
    
    def _create_game_embed(self):
        """Create the embed for the current game state."""
        embed = discord.Embed(title="♠ Blackjack ♠", color=discord.Color.green())
        
        # Player hand
        player_hand = self.game_data['player_hand']
        embed.add_field(
            name=f"Your Hand ({player_hand.get_value()})",
            value=player_hand.get_hand_string(),
            inline=False
        )
        
        dealer_hand = self.game_data['dealer_hand']
        if self.game_over:
            dealer_display = dealer_hand.get_hand_string()
        else:
            # Hide dealer's second card
            visible_cards = [Deck.card_to_string(dealer_hand.cards[0]), "?"]
            dealer_display = ' '.join(visible_cards)
        
        embed.add_field(
            name="Dealer's Hand",
            value=dealer_display,
            inline=False
        )
        
        # Bet amount
        embed.add_field(
            name="Bet",
            value=f"💰 {humanize_number(self.game_data['bet'])} credits per player",
            inline=False
        )
        
        # Game status
        status_msg = self._get_status_message()
        embed.add_field(name="Status", value=status_msg, inline=False)
        
        return embed
    
    def _get_status_message(self):
        """Get the status message based on game state."""
        status = self.game_data['status']
        bet = self.game_data['bet']
        if status == 'playing':
            return "Your turn - Hit or Stand?"
        elif status == 'bust':
            return f"❌ **Bust!** You went over 21. Lost {humanize_number(bet)} credits."
        elif status == 'dealer_bust':
            winnings = bet * 2
            return f"✅ **Dealer Bust!** You win {humanize_number(winnings)} credits!"
        elif status == 'player_blackjack':
            winnings = int(bet * 2.5)
            return f"✅ **Blackjack!** You win {humanize_number(winnings)} credits!"
        elif status == 'player_win':
            winnings = bet * 2
            return f"✅ **You Win!** You win {humanize_number(winnings)} credits!"
        elif status == 'dealer_win':
            return f"❌ **Dealer Wins!** Lost {humanize_number(bet)} credits."
        elif status == 'push':
            return f"🤝 **Push!** Your bet of {humanize_number(bet)} credits is returned."
        
        return "Game in progress..."


class CoinflipGameView(discord.ui.View):
    """View for coinflip game buttons."""
    
    def __init__(self, game_data, timeout=60):
        super().__init__(timeout=timeout)
        self.game_data = game_data
        self.game_over = False
    
    @discord.ui.button(label="Heads", style=discord.ButtonStyle.primary, emoji="🪙")
    async def heads_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Player chooses heads."""
        if interaction.user.id != self.game_data['player_id']:
            await interaction.response.defer()
            return
        
        self.game_data['player_choice'] = 'heads'
        self._flip_coin()
        self.game_over = True
        self.heads_button.disabled = True
        self.tails_button.disabled = True
        embed = self._create_game_embed()
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()
    
    @discord.ui.button(label="Tails", style=discord.ButtonStyle.primary, emoji="🪙")
    async def tails_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Player chooses tails."""
        if interaction.user.id != self.game_data['player_id']:
            await interaction.response.defer()
            return
        
        self.game_data['player_choice'] = 'tails'
        self._flip_coin()
        self.game_over = True
        self.heads_button.disabled = True
        self.tails_button.disabled = True
        embed = self._create_game_embed()
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()
    
    def _flip_coin(self):
        """Flip the coin and determine outcome."""
        self.game_data['coin_result'] = random.choice(['heads', 'tails'])
        
        # Dealer mode only
        if self.game_data['player_choice'] == self.game_data['coin_result']:
            self.game_data['status'] = 'win'
        else:
            self.game_data['status'] = 'lose'
    
    def _create_game_embed(self):
        """Create the embed for the current game state."""
        embed = discord.Embed(title="🪙 Coinflip 🪙", color=discord.Color.gold())
        
        # Coin result
        result_emoji = "🟡" if self.game_data['coin_result'] == 'heads' else "⚫"
        embed.add_field(
            name="Coin Result",
            value=f"{result_emoji} **{self.game_data['coin_result'].upper()}**",
            inline=False
        )
        
        # Dealer mode - show only player choice
        player_choice = self.game_data['player_choice']
        embed.add_field(
            name="Your Choice",
            value=f"**{player_choice.upper() if player_choice else '?'}**",
            inline=False
        )
        
        # Bet amount
        embed.add_field(
            name="Bet",
            value=f"💰 {humanize_number(self.game_data['bet'])} credits",
            inline=False
        )
        
        # Game status
        status_msg = self._get_status_message()
        embed.add_field(name="Result", value=status_msg, inline=False)
        
        return embed
    
    def _get_status_message(self):
        """Get the status message based on game outcome."""
        status = self.game_data['status']
        bet = self.game_data['bet']
        
        # Dealer mode messages
        if status == 'waiting':
            return "Waiting for your choice..."
        elif status == 'win':
            winnings = bet * 2
            return f"✅ **You Win!** You win {humanize_number(winnings)} credits!"
        elif status == 'lose':
            return f"❌ **You Lose!** Lost {humanize_number(bet)} credits."
        
        return "Game in progress..."


class ScratchTicketView(discord.ui.View):
    """View for a scratch ticket game."""

    def __init__(self, game_data, timeout=90):
        super().__init__(timeout=timeout)
        self.game_data = game_data
        self.game_over = False
        self.board = self._build_board()
        self.buttons = []

        for index in range(9):
            button = discord.ui.Button(
                label="?",
                style=discord.ButtonStyle.secondary,
                row=index // 3,
            )

            async def callback(interaction: discord.Interaction, idx=index, btn=button):
                if interaction.user.id != self.game_data["player_id"]:
                    await interaction.response.defer()
                    return

                if idx in self.game_data["scratched"]:
                    await interaction.response.defer()
                    return

                self.game_data["scratched"].add(idx)
                symbol = self.board[idx]
                btn.label = symbol
                btn.style = discord.ButtonStyle.success if symbol == "⭐" else discord.ButtonStyle.danger
                btn.disabled = True

                if len(self.game_data["scratched"]) >= 9:
                    self._finish_ticket()
                    self.game_over = True
                    for tile in self.buttons:
                        tile.disabled = True
                    embed = self._create_game_embed()
                    await interaction.response.edit_message(embed=embed, view=None)
                    self.stop()
                else:
                    embed = self._create_game_embed()
                    await interaction.response.edit_message(embed=embed, view=self)

            button.callback = callback
            self.buttons.append(button)
            self.add_item(button)

    def _build_board(self):
        """Build the hidden scratch ticket board."""
        star_count = random.choices([0, 1, 2, 3], weights=[45, 35, 15, 5], k=1)[0]
        board = ["⭐"] * star_count + ["💣"] * (9 - star_count)
        random.shuffle(board)
        self.game_data["star_count"] = star_count
        return board

    def _finish_ticket(self):
        """Resolve the ticket after all tiles are scratched."""
        star_count = self.board.count("⭐")
        bet = self.game_data["bet"]

        if star_count == 0:
            self.game_data["status"] = "lose"
            self.game_data["payout"] = 0
        elif star_count == 1:
            self.game_data["status"] = "small_win"
            self.game_data["payout"] = int(bet * 1.5)
        elif star_count == 2:
            self.game_data["status"] = "big_win"
            self.game_data["payout"] = bet * 3
        else:
            self.game_data["status"] = "jackpot"
            self.game_data["payout"] = bet * 7

    def _create_game_embed(self):
        """Create the embed for the current scratch ticket state."""
        embed = discord.Embed(title="🎟️ Scratch Ticket 🎟️", color=discord.Color.gold())
        embed.add_field(
            name="How it works",
            value="Scratch all tiles. Find ⭐ symbols to win credits.",
            inline=False,
        )
        embed.add_field(
            name="Tiles Scratched",
            value=f"{len(self.game_data['scratched'])}/9",
            inline=True,
        )
        embed.add_field(
            name="Stars Found",
            value=f"{sum(1 for i in self.game_data['scratched'] if self.board[i] == '⭐')}",
            inline=True,
        )
        embed.add_field(
            name="Bet",
            value=f"💰 {humanize_number(self.game_data['bet'])} credits",
            inline=False,
        )
        embed.add_field(
            name="Result",
            value=self._get_status_message(),
            inline=False,
        )
        return embed

    def _get_status_message(self):
        """Get the result message for the scratch ticket."""
        status = self.game_data["status"]
        bet = self.game_data["bet"]
        payout = self.game_data.get("payout", 0)
        stars = self.board.count("⭐")

        if status == "playing":
            return "Keep scratching to reveal the prize."
        if status == "lose":
            return f"❌ No stars. You lost {humanize_number(bet)} credits."
        if status == "small_win":
            return f"✨ 1 star! You win {humanize_number(payout)} credits."
        if status == "big_win":
            return f"💎 2 stars! You win {humanize_number(payout)} credits."
        if status == "jackpot":
            return f"🏆 JACKPOT! 3 stars! You win {humanize_number(payout)} credits."
        return f"Scratch complete. Stars found: {stars}."


class SaiCasino(commands.Cog):
    """A casino cog with blackjack and coinflip games using Red bank credits."""
    
    def __init__(self, bot):
        self.bot = bot

    async def _delete_invoking_message(self, ctx):
        """Best-effort delete of the command message."""
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
    
    @commands.command()
    @commands.guild_only()
    async def blackjack(self, ctx, bet: int = None):
        """
        Play a game of blackjack!
        
        Usage: [p]blackjack <bet_amount>
        
        Bet your Red bank credits and try to get 21 or closer to the dealer's hand
        without going over!
        """
        asyncio.create_task(self._delete_invoking_message(ctx))

        if bet is None:
            return await ctx.send("Please specify a bet amount. Example: `[p]blackjack 100`")
        
        if bet <= 0:
            return await ctx.send("Bet amount must be greater than 0!")
        
        # Check if both players have enough credits
        balance = await bank.get_balance(ctx.author)
        if not await bank.can_spend(ctx.author, bet):
            return await ctx.send(
                f"You don't have enough credits! Your balance: {humanize_number(balance)}"
            )
        
        # Withdraw bets
        await bank.withdraw_credits(ctx.author, bet)
        
        # Initialize game
        deck = Deck()
        player_hand = Hand()
        dealer_hand = Hand()
        
        # Deal initial cards
        player_hand.add_card(deck.draw())
        dealer_hand.add_card(deck.draw())
        player_hand.add_card(deck.draw())
        dealer_hand.add_card(deck.draw())
        
        # Check for blackjacks
        player_blackjack = player_hand.is_blackjack()
        dealer_blackjack = dealer_hand.is_blackjack()
        
        game_data = {
            'player': ctx.author,
            'player_id': ctx.author.id,
            'deck': deck,
            'player_hand': player_hand,
            'dealer_hand': dealer_hand,
            'bet': bet,
            'status': 'playing',
            'mode': 'dealer'
        }
        
        # Determine if there's a natural blackjack situation
        if player_blackjack and dealer_blackjack:
            game_data['status'] = 'push'
        elif player_blackjack:
            game_data['status'] = 'player_blackjack'
        elif dealer_blackjack:
            game_data['status'] = 'dealer_win'
        
        # Create the game view and embed
        view = BlackjackGameView(game_data)
        
        # If game is already over (blackjack cases), disable buttons
        if game_data['status'] != 'playing':
            view.game_over = True
            view.hit_button.disabled = True
            view.stand_button.disabled = True
            view_to_send = None
        else:
            view_to_send = view
        
        embed = view._create_game_embed()
        
        message = await ctx.send(embed=embed, view=view_to_send)
        
        # Wait for the game to finish
        if game_data['status'] == 'playing':
            await view.wait()
        
        # Handle timeout (game timed out without completion)
        if game_data['status'] == 'playing':
            # Game timed out, return bet
            await bank.deposit_credits(ctx.author, bet)
            
            timeout_embed = discord.Embed(title="♠ Blackjack ♠", color=discord.Color.red())
            timeout_embed.add_field(
                name="Game Timeout",
                value="⏰ Game timed out. All bets returned.",
                inline=False
            )
            try:
                await message.edit(embed=timeout_embed)
            except discord.HTTPException:
                pass
            
            # Schedule message deletion after 2 minutes
            async def delete_message():
                await asyncio.sleep(120)
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
            
            asyncio.create_task(delete_message())
            return
        
        # Handle final winnings/losses
        if game_data['status'] != 'playing':
            final_embed = view._create_game_embed()
            try:
                await message.edit(embed=final_embed, view=None)
            except discord.HTTPException:
                pass
            
            # Dealer mode payouts
            if game_data['status'] in ['dealer_bust', 'player_win']:
                winnings = int(bet * 2)
                await bank.deposit_credits(ctx.author, winnings)
            elif game_data['status'] == 'player_blackjack':
                winnings = int(bet * 2.5)
                await bank.deposit_credits(ctx.author, winnings)
            elif game_data['status'] == 'push':
                await bank.deposit_credits(ctx.author, bet)
        
        # Schedule message deletion after 2 minutes
        async def delete_message():
            await asyncio.sleep(120)
            try:
                await message.delete()
            except discord.HTTPException:
                pass
        
        asyncio.create_task(delete_message())
    
    @commands.command()
    @commands.guild_only()
    async def coinflip(self, ctx, bet: int = None):
        """
        Play a game of coinflip!
        
        Usage: [p]coinflip <bet_amount>
        
        Bet your Red bank credits and choose heads or tails. 50/50 chance to win double!
        """
        asyncio.create_task(self._delete_invoking_message(ctx))

        if bet is None:
            return await ctx.send("Please specify a bet amount. Example: `[p]coinflip 100`")
        
        if bet <= 0:
            return await ctx.send("Bet amount must be greater than 0!")
        
        # Check if both players have enough credits
        balance = await bank.get_balance(ctx.author)
        if not await bank.can_spend(ctx.author, bet):
            return await ctx.send(
                f"You don't have enough credits! Your balance: {humanize_number(balance)}"
            )
        
        # Withdraw bets
        await bank.withdraw_credits(ctx.author, bet)
        
        # Initialize game
        game_data = {
            'player': ctx.author,
            'player_id': ctx.author.id,
            'bet': bet,
            'player_choice': None,
            'coin_result': None,
            'status': 'waiting',
            'mode': 'dealer'
        }
        
        # Create the game view and embed
        view = CoinflipGameView(game_data)
        
        # Create initial waiting embed
        embed = discord.Embed(title="🪙 Coinflip 🪙", color=discord.Color.gold())
        embed.add_field(
            name="Choose Your Side",
            value="Pick **Heads** or **Tails** and the coin will flip!",
            inline=False
        )
        embed.add_field(
            name="Bet",
            value=f"💰 {humanize_number(bet)} credits",
            inline=False
        )
        embed.add_field(
            name="Odds",
            value="**50/50** chance to win double your bet!",
            inline=False
        )
        
        message = await ctx.send(embed=embed, view=view)
        
        # Wait for player choice
        await view.wait()
        
        # Handle timeout (game didn't get a choice)
        if game_data['coin_result'] is None:
            # Game timed out, return bets
            await bank.deposit_credits(ctx.author, bet)
            
            timeout_embed = discord.Embed(title="🪙 Coinflip 🪙", color=discord.Color.red())
            timeout_embed.add_field(
                name="Game Timeout",
                value="⏰ No one made a choice in time. All bets returned.",
                inline=False
            )
            try:
                await message.edit(embed=timeout_embed)
            except discord.HTTPException:
                pass
            return
        
        # Handle final winnings/losses
        final_embed = view._create_game_embed()
        try:
            await message.edit(embed=final_embed, view=None)
        except discord.HTTPException:
            pass
        
        # Process final payouts
        if game_data['status'] == 'win':
            winnings = bet * 2
            await bank.deposit_credits(ctx.author, winnings)
        # If lost, bet was already withdrawn
        
        # Schedule message deletion after 2 minutes
        async def delete_message():
            await asyncio.sleep(120)
            try:
                await message.delete()
            except discord.HTTPException:
                pass
        
        asyncio.create_task(delete_message())

    @commands.command(aliases=["scratch", "scratchticket", "ticket"])
    @commands.guild_only()
    async def lotto(self, ctx, bet: int = None):
        """
        Play a scratch ticket lottery game.

        Usage: [p]lotto <bet_amount>

        Scratch tiles to reveal stars. More stars means a bigger payout.
        """
        asyncio.create_task(self._delete_invoking_message(ctx))

        if bet is None:
            return await ctx.send("Please specify a bet amount. Example: `[p]lotto 100`")

        if bet <= 0:
            return await ctx.send("Bet amount must be greater than 0!")

        balance = await bank.get_balance(ctx.author)
        if not await bank.can_spend(ctx.author, bet):
            return await ctx.send(
                f"You don't have enough credits! Your balance: {humanize_number(balance)}"
            )

        await bank.withdraw_credits(ctx.author, bet)

        game_data = {
            'player': ctx.author,
            'player_id': ctx.author.id,
            'bet': bet,
            'status': 'playing',
            'scratched': set(),
            'payout': 0,
        }

        view = ScratchTicketView(game_data)
        embed = view._create_game_embed()
        message = await ctx.send(embed=embed, view=view)

        await view.wait()

        if game_data['status'] == 'playing':
            await bank.deposit_credits(ctx.author, bet)
            timeout_embed = discord.Embed(title="🎟️ Scratch Ticket 🎟️", color=discord.Color.red())
            timeout_embed.add_field(
                name="Game Timeout",
                value="⏰ Ticket scratched too slowly. Your bet was returned.",
                inline=False,
            )
            try:
                await message.edit(embed=timeout_embed, view=None)
            except discord.HTTPException:
                pass

            async def delete_message():
                await asyncio.sleep(120)
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass

            asyncio.create_task(delete_message())
            return

        final_embed = view._create_game_embed()
        payout = game_data.get('payout', 0)
        if payout > 0:
            await bank.deposit_credits(ctx.author, payout)

        try:
            await message.edit(embed=final_embed, view=None)
        except discord.HTTPException:
            pass

        async def delete_message():
            await asyncio.sleep(120)
            try:
                await message.delete()
            except discord.HTTPException:
                pass

        asyncio.create_task(delete_message())


async def setup(bot):
    """Load the SaiCasino cog."""
    await bot.add_cog(SaiCasino(bot))
