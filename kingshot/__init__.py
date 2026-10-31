__red_end_user_data_statement__ = (
    "Stores guild settings, dashboard message IDs, public game roster/power snapshots, "
    "manual event schedules and reminder delivery markers. No Discord user profiles or "
    "API keys are stored by this cog. The bot-wide API key uses Red's shared token storage."
)


async def setup(bot):
    from .kingshot import Kingshot

    cog = Kingshot(bot)
    try:
        await bot.add_cog(cog)
    except BaseException:
        await cog.cog_unload()
        raise
