# Red Cogs

This repository contains Red Discord bot cogs:
- `saireply`: replies when specific words appear in selected channels.
- `markettrade`: fake crypto/stock trading game using Red bank credits.

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
- `[p]market buy <symbol> <quantity>`
- `[p]market sell <symbol> <quantity>`
- `[p]market portfolio [member]`
- `[p]market interval <minutes>` (admin)
- `[p]market tick` (admin, force immediate update)
- `[p]market asset add <symbol> <crypto|stock> <starting_price> <name...>` (admin)
- `[p]market asset remove <symbol>` (admin)
- `[p]market asset list` (admin)
- `[p]market asset setprice <symbol> <price>` (admin)
- `[p]market asset setvolatility <symbol> <percent>` (admin)
- `[p]market asset setrisk <symbol> <multiplier>` (admin)
- `[p]market asset setmomentum <symbol> <percent>` (admin)

Prices now use trend momentum, so dips/pumps can continue across multiple updates before reversing.
`[p]market prices` reuses and edits the last prices message in that channel for 5 minutes instead of sending a new one.
