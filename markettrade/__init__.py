__red_end_user_data_statement__ = (
    "This cog stores per-guild fake market asset settings/prices/events and per-member holdings, "
    "cost basis, realized profit, and auto-order configurations. It does not store real financial data."
)

import importlib

from . import events, markettrade, profiles


async def setup(bot):
    importlib.reload(profiles)
    importlib.reload(events)
    importlib.reload(markettrade)
    await markettrade.setup(bot)
