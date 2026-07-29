import random
import asyncio
import discord
from redbot.core import commands, bank
from redbot.core.utils.chat_formatting import humanize_number


class OpponentAcceptView(discord.ui.View):
    """View for accepting/declining a challenge."""
    
    def __init__(self, timeout=30):
        super().__init__(timeout=timeout)
        self.accepted = False
        self.timed_out = False
    
    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green, emoji="✅")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Accept the challenge."""
        self.accepted = True
        self.timed_out = False
        await interaction.response.defer()
        self.stop()
    
    @discord.ui.button(label="Decline", style=discord.ButtonStyle.red, emoji="❌")
    async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Decline the challenge."""
        self.accepted = False
        self.timed_out = False
        await interaction.response.defer()
        self.stop()
    
    async def on_timeout(self):
        """Called when view times out."""
        self.accepted = False
        self.timed_out = True


class GameModeSelect(discord.ui.View):
    """View for selecting game mode (dealer vs player)."""
    
    def __init__(self, timeout=60):
        super().__init__(timeout=timeout)
        self.mode = None
        self.opponent = None
    
    @discord.ui.button(label="Play Dealer", style=discord.ButtonStyle.primary, emoji="🤖")
    async def dealer_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Player chooses to play against dealer."""
        self.mode = 'dealer'
        self.opponent = None
        await interaction.response.defer()
        self.stop()
    
    @discord.ui.button(label="Play Member", style=discord.ButtonStyle.secondary, emoji="👥")
    async def member_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Player chooses to play against another member."""
        self.mode = 'member'
        await interaction.response.send_modal(OpponentModal())
        self.stop()


class OpponentModal(discord.ui.Modal, title="Select Opponent"):
    """Modal for selecting an opponent member."""
    
    opponent_name = discord.ui.TextInput(
        label="Opponent Username/ID",
        placeholder="Enter username or mention",
        required=True
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()


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
        is_player = interaction.user.id == self.game_data['player_id']
        is_opponent = self.game_data['opponent_id'] and interaction.user.id == self.game_data['opponent_id']
        
        if not (is_player or is_opponent):
            await interaction.response.defer()
            return
        
        # Draw card for player
        if is_player:
            self.game_data['player_hand'].add_card(self.game_data['deck'].draw())
            if self.game_data['player_hand'].get_value() > 21:
                self.game_data['status'] = 'bust'
                self.game_over = True
                self.hit_button.disabled = True
                self.stand_button.disabled = True
        else:  # opponent
            self.game_data['opponent_hand'].add_card(self.game_data['deck'].draw())
            if self.game_data['opponent_hand'].get_value() > 21:
                self.game_data['status'] = 'opponent_bust'
                self.game_over = True
                self.hit_button.disabled = True
                self.stand_button.disabled = True
        
        embed = self._create_game_embed()
        await interaction.response.edit_message(embed=embed, view=self if not self.game_over else None)
        if self.game_over:
            self.stop()
    
    @discord.ui.button(label="Stand", style=discord.ButtonStyle.success, emoji="🛑")
    async def stand_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Player stands - dealer's turn or opponent's final comparison."""
        is_player = interaction.user.id == self.game_data['player_id']
        is_opponent = self.game_data['opponent_id'] and interaction.user.id == self.game_data['opponent_id']
        
        if not (is_player or is_opponent):
            await interaction.response.defer()
            return
        
        if self.game_data['mode'] == 'pvp':
            # PVP mode - mark player as stood
            if is_player:
                self.game_data['player_stood'] = True
            else:
                self.game_data['opponent_stood'] = True
            
            # Check if both have stood
            if self.game_data.get('player_stood') and self.game_data.get('opponent_stood'):
                # Both stood - compare hands
                player_value = self.game_data['player_hand'].get_value()
                opponent_value = self.game_data['opponent_hand'].get_value()
                
                if player_value > opponent_value:
                    self.game_data['status'] = 'player_win'
                elif opponent_value > player_value:
                    self.game_data['status'] = 'opponent_win'
                else:
                    self.game_data['status'] = 'push'
                
                self.game_over = True
                self.hit_button.disabled = True
                self.stand_button.disabled = True
        else:
            # Dealer mode
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
        
        if self.game_data['mode'] == 'pvp':
            # PVP mode - show opponent's hand
            opponent = self.game_data['opponent']
            opponent_hand = self.game_data['opponent_hand']
            embed.add_field(
                name=f"{opponent.name}'s Hand ({opponent_hand.get_value()})",
                value=opponent_hand.get_hand_string(),
                inline=False
            )
        else:
            # Dealer mode - show dealer's hand
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
        mode = self.game_data['mode']
        
        if mode == 'pvp':
            # PVP messages with player names
            opponent = self.game_data['opponent']
            opponent_name = opponent.mention if opponent else "Opponent"
            
            if status == 'playing':
                return "Players' turn - Hit or Stand?"
            elif status == 'bust':
                return f"❌ **Bust!** You went over 21. Lost {humanize_number(bet)} credits."
            elif status == 'opponent_bust':
                winnings = bet * 2
                return f"✅ **{opponent_name} Bust!** You win {humanize_number(winnings)} credits!"
            elif status == 'player_blackjack':
                winnings = int(bet * 2.5)
                return f"✅ **Blackjack!** You win {humanize_number(winnings)} credits!"
            elif status == 'opponent_blackjack':
                return f"❌ **{opponent_name} has Blackjack!** Lost {humanize_number(bet)} credits."
            elif status == 'player_win':
                winnings = bet * 2
                return f"✅ **You Win!** You win {humanize_number(winnings)} credits!"
            elif status == 'opponent_win':
                return f"❌ **{opponent_name} Wins!** Lost {humanize_number(bet)} credits."
            elif status == 'push':
                return f"🤝 **Push!** Your bet of {humanize_number(bet)} credits is returned."
        else:
            # Dealer mode messages
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
        is_player = interaction.user.id == self.game_data['player_id']
        is_opponent = self.game_data['opponent_id'] and interaction.user.id == self.game_data['opponent_id']
        
        if not (is_player or is_opponent):
            await interaction.response.defer()
            return
        
        if is_player:
            self.game_data['player_choice'] = 'heads'
        else:
            self.game_data['opponent_choice'] = 'heads'
        
        # Check if both players have chosen (if PVP) or just one (if dealer mode)
        if self._all_choices_made():
            self._flip_coin()
            self.game_over = True
            self.heads_button.disabled = True
            self.tails_button.disabled = True
            embed = self._create_game_embed()
            await interaction.response.edit_message(embed=embed, view=None)
            self.stop()
        else:
            await interaction.response.defer()
    
    @discord.ui.button(label="Tails", style=discord.ButtonStyle.primary, emoji="🪙")
    async def tails_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Player chooses tails."""
        is_player = interaction.user.id == self.game_data['player_id']
        is_opponent = self.game_data['opponent_id'] and interaction.user.id == self.game_data['opponent_id']
        
        if not (is_player or is_opponent):
            await interaction.response.defer()
            return
        
        if is_player:
            self.game_data['player_choice'] = 'tails'
        else:
            self.game_data['opponent_choice'] = 'tails'
        
        # Check if both players have chosen (if PVP) or just one (if dealer mode)
        if self._all_choices_made():
            self._flip_coin()
            self.game_over = True
            self.heads_button.disabled = True
            self.tails_button.disabled = True
            embed = self._create_game_embed()
            await interaction.response.edit_message(embed=embed, view=None)
            self.stop()
        else:
            await interaction.response.defer()
    
    def _all_choices_made(self):
        """Check if all necessary choices have been made."""
        if self.game_data['mode'] == 'pvp':
            return self.game_data['player_choice'] is not None and self.game_data['opponent_choice'] is not None
        else:
            return self.game_data['player_choice'] is not None
    
    def _flip_coin(self):
        """Flip the coin and determine outcome."""
        self.game_data['coin_result'] = random.choice(['heads', 'tails'])
        
        if self.game_data['mode'] == 'pvp':
            # PVP mode - compare both players' choices
            player_match = self.game_data['player_choice'] == self.game_data['coin_result']
            opponent_match = self.game_data['opponent_choice'] == self.game_data['coin_result']
            
            if player_match and opponent_match:
                self.game_data['status'] = 'push'
            elif player_match:
                self.game_data['status'] = 'player_win'
            elif opponent_match:
                self.game_data['status'] = 'opponent_win'
            else:
                self.game_data['status'] = 'both_lose'
        else:
            # Dealer mode
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
        
        if self.game_data['mode'] == 'pvp':
            # PVP mode - show both choices
            player_choice = self.game_data['player_choice']
            opponent_choice = self.game_data['opponent_choice']
            opponent = self.game_data['opponent']
            
            embed.add_field(
                name="Your Choice",
                value=f"**{player_choice.upper() if player_choice else '?'}**",
                inline=True
            )
            embed.add_field(
                name=f"{opponent.name}'s Choice",
                value=f"**{opponent_choice.upper() if opponent_choice else '?'}**",
                inline=True
            )
        else:
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
        
        if self.game_data['mode'] == 'pvp':
            # PVP messages with player names
            opponent = self.game_data['opponent']
            opponent_name = opponent.mention if opponent else "Opponent"
            
            if status == 'waiting':
                return "Waiting for players' choices..."
            elif status == 'player_win':
                winnings = bet * 2
                return f"✅ **You Win!** You win {humanize_number(winnings)} credits!"
            elif status == 'opponent_win':
                return f"❌ **{opponent_name} Wins!** Lost {humanize_number(bet)} credits."
            elif status == 'push':
                return f"🤝 **Push!** Both matched the coin. Your bet of {humanize_number(bet)} credits is returned."
            elif status == 'both_lose':
                return f"❌ **Both Lose!** Neither matched. Bets kept."
        else:
            # Dealer mode messages
            if status == 'waiting':
                return "Waiting for your choice..."
            elif status == 'win':
                winnings = bet * 2
                return f"✅ **You Win!** You win {humanize_number(winnings)} credits!"
            elif status == 'lose':
                return f"❌ **You Lose!** Lost {humanize_number(bet)} credits."
        
        return "Game in progress..."


class SaiCasino(commands.Cog):
    """A casino cog with blackjack and coinflip games using Red bank credits."""
    
    def __init__(self, bot):
        self.bot = bot
    
    async def _get_player_result_embed(self, player, opponent, game_data, perspective):
        """Generate a personalized result embed from a player's perspective."""
        status = game_data['status']
        bet = game_data['bet']
        
        if perspective == 'player':
            player_hand = game_data['player_hand']
            opponent_hand = game_data['opponent_hand']
            viewer = player
            other = opponent
        else:  # opponent perspective
            player_hand = game_data['opponent_hand']
            opponent_hand = game_data['player_hand']
            viewer = opponent
            other = player
        
        embed = discord.Embed(title="♠ Blackjack Result ♠", color=discord.Color.green())
        
        # Your hand
        embed.add_field(
            name=f"Your Hand ({player_hand.get_value()})",
            value=player_hand.get_hand_string(),
            inline=False
        )
        
        # Opponent's hand
        embed.add_field(
            name=f"{other.name}'s Hand ({opponent_hand.get_value()})",
            value=opponent_hand.get_hand_string(),
            inline=False
        )
        
        # Result message from this player's perspective
        if status == 'player_blackjack':
            if perspective == 'player':
                result_msg = f"✅ **You have Blackjack!** You win {humanize_number(int(bet * 2.5))} credits!"
            else:
                result_msg = f"❌ **{player.name} has Blackjack!** You lost {humanize_number(bet)} credits."
        elif status == 'opponent_blackjack':
            if perspective == 'player':
                result_msg = f"❌ **{opponent.name} has Blackjack!** You lost {humanize_number(bet)} credits."
            else:
                result_msg = f"✅ **You have Blackjack!** You win {humanize_number(int(bet * 2.5))} credits!"
        elif status == 'player_win':
            if perspective == 'player':
                result_msg = f"✅ **You Win!** You win {humanize_number(int(bet * 2))} credits!"
            else:
                result_msg = f"❌ **{player.name} Wins!** You lost {humanize_number(bet)} credits."
        elif status == 'opponent_win':
            if perspective == 'player':
                result_msg = f"❌ **{opponent.name} Wins!** You lost {humanize_number(bet)} credits."
            else:
                result_msg = f"✅ **You Win!** You win {humanize_number(int(bet * 2))} credits!"
        elif status == 'opponent_bust':
            if perspective == 'player':
                result_msg = f"✅ **{opponent.name} Bust!** You win {humanize_number(int(bet * 2))} credits!"
            else:
                result_msg = f"❌ **You Bust!** You lost {humanize_number(bet)} credits."
        elif status == 'bust':
            if perspective == 'player':
                result_msg = f"❌ **You Bust!** You lost {humanize_number(bet)} credits."
            else:
                result_msg = f"✅ **{player.name} Bust!** You win {humanize_number(int(bet * 2))} credits!"
        elif status == 'push':
            result_msg = f"🤝 **Push!** Your bet of {humanize_number(bet)} credits is returned."
        else:
            result_msg = "Game in progress..."
        
        embed.add_field(name="Result", value=result_msg, inline=False)
        return embed
    
    async def _get_coinflip_result_embed(self, player, opponent, game_data, perspective):
        """Generate a personalized coinflip result embed from a player's perspective."""
        status = game_data['status']
        bet = game_data['bet']
        coin_result = game_data['coin_result']
        
        if perspective == 'player':
            player_choice = game_data['player_choice']
            opponent_choice = game_data['opponent_choice']
            viewer = player
            other = opponent
        else:  # opponent perspective
            player_choice = game_data['opponent_choice']
            opponent_choice = game_data['player_choice']
            viewer = opponent
            other = player
        
        embed = discord.Embed(title="🪙 Coinflip Result 🪙", color=discord.Color.gold())
        
        # Show the flip result
        flip_emoji = "🟤" if coin_result == "Heads" else "⚪"
        embed.add_field(
            name="Coin Flip Result",
            value=f"{flip_emoji} **{coin_result}**",
            inline=False
        )
        
        # Show both players' choices
        embed.add_field(
            name="Choices",
            value=f"**Your choice:** {player_choice}\n**{other.name}'s choice:** {opponent_choice}",
            inline=False
        )
        
        # Result message from this player's perspective
        if status == 'player_win':
            if perspective == 'player':
                result_msg = f"✅ **You Win!** You win {humanize_number(bet * 2)} credits!"
            else:
                result_msg = f"❌ **{player.name} Wins!** You lost {humanize_number(bet)} credits."
        elif status == 'opponent_win':
            if perspective == 'player':
                result_msg = f"❌ **{opponent.name} Wins!** You lost {humanize_number(bet)} credits."
            else:
                result_msg = f"✅ **You Win!** You win {humanize_number(bet * 2)} credits!"
        elif status == 'push':
            result_msg = f"🤝 **Push!** Both chose {coin_result}. Your bet of {humanize_number(bet)} credits is returned."
        elif status == 'both_lose':
            result_msg = f"❌ **Both Wrong!** The coin landed on {coin_result}, but both chose incorrectly. Bets are lost."
        else:
            result_msg = "Game in progress..."
        
        embed.add_field(name="Result", value=result_msg, inline=False)
        return embed
    
    @commands.command()
    @commands.guild_only()
    async def blackjack(self, ctx, bet: int = None, opponent: discord.Member = None):
        """
        Play a game of blackjack!
        
        Usage: [p]blackjack <bet_amount> [opponent]
        
        Bet your Red bank credits and try to get 21 or closer to the dealer's hand
        without going over! Optionally play against another member.
        """
        if bet is None:
            return await ctx.send("Please specify a bet amount. Example: `[p]blackjack 100`")
        
        if opponent and opponent.bot:
            return await ctx.send("You can't play against a bot!")
        
        if opponent and opponent == ctx.author:
            return await ctx.send("You can't play against yourself!")
        
        if bet <= 0:
            return await ctx.send("Bet amount must be greater than 0!")
        
        # Check if both players have enough credits
        balance = await bank.get_balance(ctx.author)
        if not await bank.can_spend(ctx.author, bet):
            return await ctx.send(
                f"You don't have enough credits! Your balance: {humanize_number(balance)}"
            )
        
        if opponent:
            opponent_balance = await bank.get_balance(opponent)
            if not await bank.can_spend(opponent, bet):
                return await ctx.send(
                    f"{opponent.mention} doesn't have enough credits! Their balance: {humanize_number(opponent_balance)}"
                )
            
            # Ask opponent to accept
            accept_view = OpponentAcceptView(timeout=30)
            msg = await ctx.send(f"{opponent.mention}, {ctx.author.mention} challenges you to blackjack for {humanize_number(bet)} credits! Accept?", view=accept_view)
            await accept_view.wait()
            
            if not accept_view.accepted:
                decline_msg = f"❌ {opponent.mention} declined the challenge!" if accept_view.timed_out else f"❌ {opponent.mention} declined the challenge!"
                if accept_view.timed_out:
                    decline_msg = f"❌ Challenge acceptance period is over! {opponent.mention} didn't respond in time."
                return await msg.edit(content=decline_msg, view=None)
        
        # Withdraw bets
        await bank.withdraw_credits(ctx.author, bet)
        if opponent:
            await bank.withdraw_credits(opponent, bet)
        
        # Initialize game
        deck = Deck()
        player_hand = Hand()
        opponent_hand = Hand() if opponent else None
        dealer_hand = Hand() if not opponent else None
        
        # Deal initial cards
        player_hand.add_card(deck.draw())
        if opponent:
            opponent_hand.add_card(deck.draw())
            player_hand.add_card(deck.draw())
            opponent_hand.add_card(deck.draw())
        else:
            dealer_hand.add_card(deck.draw())
            player_hand.add_card(deck.draw())
            dealer_hand.add_card(deck.draw())
        
        # Check for blackjacks
        player_blackjack = player_hand.is_blackjack()
        opponent_blackjack = opponent_hand.is_blackjack() if opponent else None
        dealer_blackjack = dealer_hand.is_blackjack() if not opponent else None
        
        game_data = {
            'player_id': ctx.author.id,
            'opponent_id': opponent.id if opponent else None,
            'opponent': opponent,
            'deck': deck,
            'player_hand': player_hand,
            'opponent_hand': opponent_hand,
            'dealer_hand': dealer_hand,
            'bet': bet,
            'status': 'playing',
            'mode': 'pvp' if opponent else 'dealer'
        }
        
        # Determine if there's a natural blackjack situation
        if opponent:
            # PVP mode
            if player_blackjack and opponent_blackjack:
                game_data['status'] = 'push'
            elif player_blackjack and not opponent_blackjack:
                game_data['status'] = 'player_blackjack'
            elif opponent_blackjack and not player_blackjack:
                game_data['status'] = 'opponent_blackjack'
        else:
            # Dealer mode
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
            # Game timed out, return bets
            await bank.deposit_credits(ctx.author, bet)
            if opponent:
                await bank.deposit_credits(opponent, bet)
            
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
                await message.edit(embed=final_embed)
            except discord.HTTPException:
                pass
            
            # Process final payouts
            if game_data['mode'] == 'pvp':
                # PVP payouts
                if game_data['status'] in ['opponent_bust', 'player_win']:
                    winnings = int(bet * 2)
                    await bank.deposit_credits(ctx.author, winnings)
                    await bank.deposit_credits(opponent, 0)  # Opponent loses
                elif game_data['status'] == 'player_blackjack':
                    winnings = int(bet * 2.5)
                    await bank.deposit_credits(ctx.author, winnings)
                elif game_data['status'] == 'opponent_blackjack':
                    winnings = int(bet * 2.5)
                    await bank.deposit_credits(opponent, winnings)
                elif game_data['status'] == 'opponent_win':
                    winnings = int(bet * 2)
                    await bank.deposit_credits(opponent, winnings)
                elif game_data['status'] == 'push':
                    await bank.deposit_credits(ctx.author, bet)
                    await bank.deposit_credits(opponent, bet)
                elif game_data['status'] == 'bust':
                    # Player busted, opponent gets winnings
                    await bank.deposit_credits(opponent, bet * 2)
            else:
                # Dealer mode payouts
                if game_data['status'] in ['dealer_bust', 'player_win']:
                    winnings = int(bet * 2)
                    await bank.deposit_credits(ctx.author, winnings)
                elif game_data['status'] == 'player_blackjack':
                    winnings = int(bet * 2.5)
                    await bank.deposit_credits(ctx.author, winnings)
                elif game_data['status'] == 'push':
                    await bank.deposit_credits(ctx.author, bet)
        
        # Send personalized results to each player (PVP only)
        if game_data['mode'] == 'pvp':
            # Generate result embeds for each player
            player_embed = await self._get_player_result_embed(ctx.author, opponent, game_data, 'player')
            opponent_embed = await self._get_player_result_embed(ctx.author, opponent, game_data, 'opponent')
            
            try:
                await ctx.send(embed=player_embed, ephemeral=True)
                await opponent.send(embed=opponent_embed)
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
    
    @commands.command()
    @commands.guild_only()
    async def coinflip(self, ctx, bet: int = None, opponent: discord.Member = None):
        """
        Play a game of coinflip!
        
        Usage: [p]coinflip <bet_amount> [opponent]
        
        Bet your Red bank credits and choose heads or tails. 50/50 chance to win double!
        Optionally play against another member.
        """
        if bet is None:
            return await ctx.send("Please specify a bet amount. Example: `[p]coinflip 100`")
        
        if opponent and opponent.bot:
            return await ctx.send("You can't play against a bot!")
        
        if opponent and opponent == ctx.author:
            return await ctx.send("You can't play against yourself!")
        
        if bet <= 0:
            return await ctx.send("Bet amount must be greater than 0!")
        
        # Check if both players have enough credits
        balance = await bank.get_balance(ctx.author)
        if not await bank.can_spend(ctx.author, bet):
            return await ctx.send(
                f"You don't have enough credits! Your balance: {humanize_number(balance)}"
            )
        
        if opponent:
            opponent_balance = await bank.get_balance(opponent)
            if not await bank.can_spend(opponent, bet):
                return await ctx.send(
                    f"{opponent.mention} doesn't have enough credits! Their balance: {humanize_number(opponent_balance)}"
                )
            
            # Ask opponent to accept
            accept_view = OpponentAcceptView(timeout=30)
            msg = await ctx.send(f"{opponent.mention}, {ctx.author.mention} challenges you to coinflip for {humanize_number(bet)} credits! Accept?", view=accept_view)
            await accept_view.wait()
            
            if not accept_view.accepted:
                decline_msg = f"❌ {opponent.mention} declined the challenge!"
                if accept_view.timed_out:
                    decline_msg = f"❌ Challenge acceptance period is over! {opponent.mention} didn't respond in time."
                return await msg.edit(content=decline_msg, view=None)
        
        # Withdraw bets
        await bank.withdraw_credits(ctx.author, bet)
        if opponent:
            await bank.withdraw_credits(opponent, bet)
        
        # Initialize game
        game_data = {
            'player_id': ctx.author.id,
            'opponent_id': opponent.id if opponent else None,
            'opponent': opponent,
            'bet': bet,
            'player_choice': None,
            'opponent_choice': None,
            'coin_result': None,
            'status': 'waiting',
            'mode': 'pvp' if opponent else 'dealer'
        }
        
        # Create the game view and embed
        view = CoinflipGameView(game_data)
        
        # Create initial waiting embed
        embed = discord.Embed(title="🪙 Coinflip 🪙", color=discord.Color.gold())
        if opponent:
            embed.add_field(
                name="Choose Your Side",
                value=f"{ctx.author.mention} and {opponent.mention}\nPick **Heads** or **Tails** and let's flip!",
                inline=False
            )
        else:
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
            if opponent:
                await bank.deposit_credits(opponent, bet)
            
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
            await message.edit(embed=final_embed)
        except discord.HTTPException:
            pass
        
        # Process final payouts
        if game_data['mode'] == 'pvp':
            # PVP payouts
            if game_data['status'] == 'player_win':
                winnings = bet * 2
                await bank.deposit_credits(ctx.author, winnings)
            elif game_data['status'] == 'opponent_win':
                winnings = bet * 2
                await bank.deposit_credits(opponent, winnings)
            elif game_data['status'] == 'push':
                # Both matched - return both bets
                await bank.deposit_credits(ctx.author, bet)
                await bank.deposit_credits(opponent, bet)
            # If both_lose, neither gets credits (both bets are kept)
            
            # Send personalized messages to each player
            player_embed = await self._get_coinflip_result_embed(ctx.author, opponent, game_data, 'player')
            opponent_embed = await self._get_coinflip_result_embed(ctx.author, opponent, game_data, 'opponent')
            
            try:
                await ctx.send(embed=player_embed, ephemeral=True)
                await opponent.send(embed=opponent_embed)
            except discord.HTTPException:
                pass
        else:
            # Dealer mode payouts
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


async def setup(bot):
    """Load the SaiCasino cog."""
    await bot.add_cog(SaiCasino(bot))
