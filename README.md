# Red Cogs

This repository contains Red Discord bot cogs:
- `saireply`: replies when specific words appear in selected channels.
- `markettrade`: fake crypto/stock trading game using Red bank credits.
- `voterole`: grants a temporary role when a vote event is received (top.gg style events).

## Features
- Add/remove channels in Discord with commands
- Add/remove trigger words and replies in Discord with commands
- Replies when configured keywords are found

## Setup
1. Put the folder named `saireply` into your Red bot's `cogs` directory.
2. Load the cog with:
   - `[p]load saireply`
3. Configure channels and words with commands in Discord.

## Install from GitHub
Once this repo is pushed to GitHub, you can install it in Red with:

- `[p]cog install <your-github-repo-url>`
- `[p]load saireply`

For vote role rewards:

- `[p]cog install <your-github-repo-url> voterole`
- `[p]load voterole`
- `[p]voterole createrole Voter`
- `[p]voterole duration 2`

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
- `[p]market prices`
- `[p]market graph <symbol> [window]` (examples: `30m`, `6h`, max `24h`)
- `[p]market buy <symbol> <quantity>`
- `[p]market sell <symbol> <quantity>`
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
