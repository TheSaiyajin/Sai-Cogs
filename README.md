# Red Cogs

This repository contains Red Discord bot cogs:
- `saireply`: replies when specific words appear in selected channels.
- `markettrade`: fake crypto/stock trading game using Red bank credits.
- `lifesim`: life simulation game with jobs, food, housing, and upkeep.
- `voterole`: grants a temporary role when a vote event is received (top.gg style events).

## Features
- Add/remove channels in Discord with commands
- Add/remove trigger words and replies in Discord with commands
- Replies when configured keywords are found as whole words

## Setup
1. Put the folder named `saireply` into your Red bot's `cogs` directory.
2. Load the cog with:
   - `[p]load saireply`
3. Configure channels and words with commands in Discord.

## LifeSim Setup
1. Install and load:
   - `[p]cog install <your-github-repo-url> lifesim`
   - `[p]load lifesim`
2. Use `[p]lifesim help` to see commands.
3. Configure jobs, food, houses, and simulation settings from Discord.

## Install from GitHub
Once this repo is pushed to GitHub, you can install it in Red with:

- `[p]cog install <your-github-repo-url>`
- `[p]load saireply`

For vote role rewards:

- `[p]cog install <your-github-repo-url> voterole`
- `[p]load voterole`
- `[p]voterole createrole Voter`
- `[p]voterole duration 2`

## LifeSim Commands
- `[p]lifesim profile [member]`
- `[p]lifesim status [member]`
- `[p]lifesim commands`
- `[p]lifesim work`
- `[p]lifesim rest`
- `[p]lifesim sleep`
- `[p]lifesim jobs list`
- `[p]lifesim jobs info <job>`
- `[p]lifesim jobs apply <job>`
- `[p]lifesim jobs clear`
- `[p]lifesim jobs admin add <key> <pay> <energy_cost> <hunger_cost> <happiness_cost> <cooldown_minutes> <name...>`
- `[p]lifesim jobs admin edit <key> <field> <value>`
- `[p]lifesim jobs admin remove <key>`
- `[p]lifesim jobs admin resetdefaults`
- `[p]lifesim shop list`
- `[p]lifesim shop info <item>`
- `[p]lifesim shop buy <item> [quantity]`
- `[p]lifesim shop admin add <key> <price> <hunger_restore> <energy_restore> <happiness_restore> <name...>`
- `[p]lifesim shop admin edit <key> <field> <value>`
- `[p]lifesim shop admin remove <key>`
- `[p]lifesim shop admin resetdefaults`
- `[p]lifesim inventory`
- `[p]lifesim eat <item> [quantity]`
- `[p]lifesim house list`
- `[p]lifesim house info <house>`
- `[p]lifesim house buy <house>`
- `[p]lifesim house current`
- `[p]lifesim house admin add <key> <price> <upkeep> <work_bonus> <rest_energy> <rest_happiness> <name...>`
- `[p]lifesim house admin edit <key> <field> <value>`
- `[p]lifesim house admin remove <key>`
- `[p]lifesim house admin resetdefaults`
- `[p]lifesim settings view`
- `[p]lifesim settings setstarting <hunger|energy|happiness> <value>`
- `[p]lifesim settings setdecay <hunger|energy|happiness> <value>`
- `[p]lifesim settings setrest <energy|happiness> <value>`
- `[p]lifesim settings setrestcooldown <hours>`
- `[p]lifesim settings setworkcooldown <hours>`
- `[p]lifesim settings setsleepcooldown <hours>`
- `[p]lifesim settings setupkeep <hours>`
- `[p]lifesim settings resetdefaults`
- `[p]lifesim member set <member> <hunger|energy|happiness|xp|job|house> <value>`
- `[p]lifesim member cooldowns reset <member>`

## Commands
- `[p]saireply channel add #channel`
- `[p]saireply channel remove #channel`
- `[p]saireply channel list`
- `[p]saireply trigger add #channel keyword your reply text`
- `[p]saireply trigger remove #channel keyword`
- `[p]saireply trigger list #channel`
- `[p]saireply trigger all` (or `listall`)

## MarketTrade Setup
1. Install and load:
   - `[p]cog install <your-github-repo-url> markettrade`
   - `[p]load markettrade`
2. Start trading with defaults, or add your own assets in Discord.

## MarketTrade Commands
- Common aliases:
  - Root: `[p]market` = `[p]mt`
  - Trading: `buy|b`, `sell|s`, `prices|price|pr`, `portfolio|pf|port`, `graph|chart|g`, `top|leaderboard|lb`
  - Auto orders: `autobuy|ab`, `autosell|as`
  - Auto order subcommands: `set|create|add`, `list|ls`, `remove|rm|del`
- `[p]market prices`
- `[p]market graph <symbol> [window]` (draws a PNG image, examples: `30m`, `6h`, max `24h`)
- `[p]market buy <symbol> <quantity>`
- `[p]market sell <symbol> <quantity>`
- `[p]market top profit [limit]` (top total profit leaderboard)
- `[p]market top value [limit]` (top portfolio value leaderboard)
- `[p]market autobuy set <symbol> <target_price> <quantity>` (with ✅/❌ confirmation)
- `[p]market autosell set <symbol> <target_price> <quantity|all>` (with ✅/❌ confirmation)
- `[p]market autobuy list`, `[p]market autobuy remove <symbol>`
- `[p]market autosell list`, `[p]market autosell remove <symbol>`
- `[p]market fees show` (admin)
- `[p]market fees buy <percent>` (admin)
- `[p]market fees sell <percent>` (admin)
- `[p]market limits show` (admin)
- `[p]market limits value <credits>` (admin, 0 = unlimited)
- `[p]market limits trades <count>` (admin, 0 = unlimited)
- `[p]market limits usage [member]` (admin)
- `[p]market limits reset <@member>` (admin)
- `[p]market cycle info <symbol>` (admin)
- `[p]market cycle announce <true|false>` (admin)
- `[p]market cycle history [limit]` (admin, recent profile change log)
- `[p]market cycle clearhistory` (admin)
- `[p]market event channel [#channel]` (admin, show/set announce channel)
- `[p]market event clearchannel` (admin)
- `[p]market portfolio [member]`
- `[p]market tick` (admin, force immediate update)
- `[p]market liveprices` (admin, creates one message that auto-edits every minute)
- `[p]market asset add <symbol> <crypto|stock> <starting_price> <name...>` (admin)
- `[p]market asset remove <symbol>` (admin)
- `[p]market asset list` (admin)
- `[p]market asset setprice <symbol> <price>` (admin)
- `[p]market asset setvolatility <symbol> <percent>` (admin)
- `[p]market asset setrisk <symbol> <multiplier>` (admin)
- `[p]market asset setmomentum <symbol> <percent>` (admin)

## VoteRole Commands
- `[p]voterole createrole [name]` (admin, creates and sets role)
- `[p]voterole setrole <@role>` (admin)
- `[p]voterole clearrole` (admin)
- `[p]voterole duration <days>` (admin, example: `1` or `2`)
- `[p]voterole deleteexpiredpollroles <true|false>` (admin)
- `[p]voterole status` (admin)
- `[p]voterole grant <@member>` (admin, manual test/refresh)
- `[p]voterole poll set <message_id> <answer_id> <@role>` (admin)
- `[p]voterole poll remove <message_id> <answer_id>` (admin)
- `[p]voterole poll clear <message_id>` (admin)
- `[p]voterole poll list [message_id]` (admin)
- `[p]voterole poll finalize <message_id>` (admin, manual fallback finalize)

VoteRole listens for `on_dbl_vote` and `on_topgg_vote` events from vote webhook integrations.
For Discord polls, votes are tracked and roles are granted automatically after the poll ends.
`poll finalize` is still available as a manual fallback.
If a configured reward role was deleted, the cog will auto-create a replacement role and assign it.

Prices now use trend momentum, so dips/pumps can continue across multiple updates before reversing.
`[p]market prices` reuses and edits the last prices message in that channel for 5 minutes instead of sending a new one.
