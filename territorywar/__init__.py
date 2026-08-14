__red_end_user_data_statement__ = (
	"This cog stores per-guild season state, registrations, alliance assignments, battle state, "
	"territory ownership/power, map message IDs, and per-member minigame best scores plus contributions."
)


async def setup(bot):
	from .territorywar import setup as real_setup

	await real_setup(bot)
