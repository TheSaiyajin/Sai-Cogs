__red_end_user_data_statement__ = (
    "This cog stores per-guild life simulation settings (jobs, foods, houses, and tuning values) "
    "and per-member life state (job, house, needs, inventory, XP, and timestamps). It does not "
    "store real-world personal data."
)

import importlib

from . import lifesim


async def setup(bot):
    importlib.reload(lifesim)
    await lifesim.setup(bot)
