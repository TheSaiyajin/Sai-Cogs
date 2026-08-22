__red_end_user_data_statement__ = (
    "This cog stores per-guild trading card game settings, cached Pokémon card metadata for supported sets, "
    "active trades, active marketplace listings, and bounded trade/market audit history. "
    "It stores per-member owned card instances, unopened packs, favorites, pull stats, and trade/open counters."
)

import importlib

async def setup(bot):
    from . import cards as cards_module
    from . import tradingcards as tradingcards_module

    importlib.reload(cards_module)
    importlib.reload(tradingcards_module)
    await tradingcards_module.setup(bot)
