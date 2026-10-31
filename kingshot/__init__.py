__red_end_user_data_statement__ = (
    "Stores guild settings, dashboard message IDs, public game roster/power snapshots, "
    "manual event schedules and reminder delivery markers. Self-service linking stores "
    "per-server Discord member/governor associations, link timestamps, cog-assigned role "
    "IDs and original/applied/pending nicknames for cleanup. Links do not verify ownership. "
    "Red data deletion removes member linking records. API keys use Red's shared token storage."
)


async def setup(bot):
    from .kingshot import Kingshot

    cog = Kingshot(bot)
    try:
        await bot.add_cog(cog)
    except BaseException:
        await cog.cog_unload()
        raise
