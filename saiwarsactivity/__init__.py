async def setup(bot):
    from .saiwarsactivity import SaiWarsActivity

    await bot.add_cog(SaiWarsActivity(bot))