import random
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
        
        # Draw card for player
        self.game_data['player_hand'].add_card(self.game_data['deck'].draw())
        
        # Check if player busted
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
        
        # Dealer plays
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
        await interaction.response.edit_message(embed=embed, view=None)
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
        
        # Dealer hand
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
            value=f"💰 {humanize_number(self.game_data['bet'])} credits",
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
        else:
            return "Game in progress..."


class SaiCasino(commands.Cog):
    """A casino cog with blackjack games using Red bank credits."""
    
    def __init__(self, bot):
        self.bot = bot
    
    @commands.command()
    @commands.guild_only()
    async def blackjack(self, ctx, bet: int = None):
        """
        Play a game of blackjack!
        
        Usage: [p]blackjack <bet_amount>
        
        Bet your Red bank credits and try to get 21 or closer to the dealer's hand
        without going over!
        """
        if bet is None:
            return await ctx.send("Please specify a bet amount. Example: `[p]blackjack 100`")
        
        if bet <= 0:
            return await ctx.send("Bet amount must be greater than 0!")
        
        # Check if player has enough credits
        balance = await bank.get_balance(ctx.author)
        if not await bank.can_spend(ctx.author, bet):
            return await ctx.send(
                f"You don't have enough credits! Your balance: {humanize_number(balance)}"
            )
        
        # Withdraw the bet
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
            'player_id': ctx.author.id,
            'deck': deck,
            'player_hand': player_hand,
            'dealer_hand': dealer_hand,
            'bet': bet,
            'status': 'playing'
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
        
        # Handle final winnings/losses
        if game_data['status'] != 'playing':
            final_embed = view._create_game_embed()
            try:
                await message.edit(embed=final_embed)
            except discord.HTTPException:
                pass
            
            # Process final payouts
            if game_data['status'] in ['dealer_bust', 'player_win']:
                winnings = int(bet * 2)
                await bank.deposit_credits(ctx.author, winnings)
            elif game_data['status'] == 'player_blackjack':
                winnings = int(bet * 2.5)
                await bank.deposit_credits(ctx.author, winnings)
            elif game_data['status'] == 'push':
                await bank.deposit_credits(ctx.author, bet)


async def setup(bot):
    """Load the SaiCasino cog."""
    await bot.add_cog(SaiCasino(bot))
