# MarketTrade

MarketTrade is a Red Discord Bot cog for simulated stock/crypto trading with Red bank credits.

## Features

- Configurable fake assets (stock/crypto)
- Behavior profiles (`stable`, `uptrend`, `downtrend`, `swing`, `wild`)
- Automatic and manual price ticks
- Auto-buy and auto-sell orders
- Portfolio tracking with cost basis and realized/unrealized P/L
- Optional live-updating price message
- Admin testing tools (`tick`, `ticks`, `triggerorders`)

## Quick Start

1. Load cog: `[p]load markettrade`
2. See commands: `[p]market help`
3. Set update interval: `[p]market interval 5`
4. Check prices: `[p]market prices`
5. Buy assets: `[p]market buy <symbol> <qty>`

## Common Admin Commands

- `[p]market asset add <symbol> <stock|crypto> <price> <name...>`
- `[p]market asset setprofile <symbol> <profile>`
- `[p]market asset setprice <symbol> <price>`
- `[p]market asset setminprice <symbol> <price>`
- `[p]market asset setmaxprice <symbol> <price>`
- `[p]market tick`
- `[p]market ticks <count>`

## Data Statement

This cog stores:

- Per-guild asset settings/prices/events
- Per-member holdings/cost basis/realized profit
- Per-member auto-order configuration

No real-world financial data is used.
