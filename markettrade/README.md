# MarketTrade

MarketTrade is a Red Discord Bot cog for simulated stock/crypto trading with Red bank credits.

## Features

- Configurable fake assets (stock/crypto)
- Behavior profiles (`stable`, `uptrend`, `downtrend`, `swing`, `wild`, `bullrun`, `crash`, `recovery`, `flat`)
- Automatic and manual price ticks
- Auto-buy and auto-sell orders
- Buy/sell confirmation prompts with ✅/❌ and timeout
- Auto-order setup confirmation prompts with ✅/❌ and timeout
- Configurable buy/sell fees
- Configurable daily trading limits (manual + auto orders consume limits)
- Daily UTC reset for trade-limit usage
- Portfolio tracking with cost basis and realized/unrealized P/L
- Profile-cycle announcements with configurable channel/toggle
- Optional live-updating price message
- Admin testing tools (`tick`, `ticks`, `triggerorders`)

## Quick Start

1. Load cog: `[p]load markettrade`
2. See commands: `[p]market help`
3. Prices update every minute automatically
4. Check prices: `[p]market prices`
5. Buy assets: `[p]market buy <symbol> <qty>`
6. Leaderboards: `[p]market top profit` / `[p]market top value`

## Common Admin Commands

- `[p]market asset add <symbol> <stock|crypto> <price> <name...>`
- `[p]market asset setprofile <symbol> <profile>`
- `[p]market cycle info <symbol>`
- `[p]market cycle announce <true|false>`
- `[p]market cycle history [limit]`
- `[p]market cycle clearhistory`
- `[p]market event channel [#channel]`
- `[p]market event clearchannel`
- `[p]market fees show|buy|sell`
- `[p]market limits show|value|trades|usage|reset`
- `[p]market asset setprice <symbol> <price>`
- `[p]market asset setminprice <symbol> <price>`
- `[p]market asset setmaxprice <symbol> <price>`
- `[p]market tick`
- `[p]market ticks <count>`

## Aliases

- Root: `[p]market` = `[p]mt`
- Trading: `buy|b`, `sell|s`, `prices|price|pr`, `portfolio|pf|port`, `graph|chart|g`, `top|leaderboard|lb`
- Auto orders: `autobuy|ab`, `autosell|as`
- Auto order subcommands: `set|create|add`, `list|ls`, `remove|rm|del`

## Data Statement

This cog stores:

- Per-guild asset settings/prices/events
- Per-member holdings/cost basis/realized profit
- Per-member auto-order configuration

No real-world financial data is used.
