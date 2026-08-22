# TradingCards (TCG)

TradingCards is a Red-DiscordBot cog for a Pokémon card collection game using Red economy credits.

## Installation

1. Add/update your repo as normal.
2. Install cog:
   - `[p]load tradingcards`
3. View commands:
   - `[p]tcg help`

## Optional Pokémon TCG API Key

The cog works without a key (public rate limits apply), but a key is recommended.

- Example:
  - `[p]set api pokemontcg api_key,YOUR_KEY_HERE`

The cog reads shared API tokens under `pokemontcg` (`api_key`, `key`, `token`, or `apikey`).

## Member Commands

- `[p]tcg shop`
- `[p]tcg sets`
- `[p]tcg set <set>`
- `[p]tcg buy <set> [amount]`
- `[p]tcg packs`
- `[p]tcg open <set> [amount]`
- `[p]tcg collection [member]`
- `[p]tcg cards [member] [set]`
- `[p]tcg card <instance_id>`
- `[p]tcg duplicates [member]`
- `[p]tcg missing <set>`
- `[p]tcg completion [member]`
- `[p]tcg favorite <instance_id>`
- `[p]tcg trade start @member`
- `[p]tcg trade add <instance_id>`
- `[p]tcg trade remove <instance_id>`
- `[p]tcg trade credits <amount>`
- `[p]tcg trade view`
- `[p]tcg trade confirm`
- `[p]tcg trade decline`
- `[p]tcg trade cancel`
- `[p]tcg market`
- `[p]tcg market search <card name|set|rarity>`
- `[p]tcg market view <listing_id>`
- `[p]tcg market list <instance_id> <credit_price>`
- `[p]tcg market buy <listing_id>`
- `[p]tcg market cancel <listing_id>`
- `[p]tcg market mine`
- `[p]tcg market history [card name]`
- `[p]tcg leaderboard <cards|unique|rare|completion|trades>`

## Admin Commands

- `[p]tcgset price <set> <credits>`
- `[p]tcgset enable <set>`
- `[p]tcgset disable <set>`
- `[p]tcgset maxbuy <amount>`
- `[p]tcgset maxopen <amount>`
- `[p]tcgset tradeexpiry <minutes>`
- `[p]tcgset markettax <percentage>`
- `[p]tcgset refreshsets`
- `[p]tcgset settings`
- `[p]tcgset repairlocks`
- `[p]tcgset resetmember @member` (destructive, explicit confirmation required)

## Supported Sets

- Scarlet & Violet—151 (`sv3pt5`)
- Paldean Fates (`sv4pt5`)
- Twilight Masquerade (`sv6`)
- Surging Sparks (`sv8`)

Set aliases include `151`, `paldean`, `twilight`, and `surging`.

## Pack Odds (Simulated)

Each opened pack generates:

- 6 Common
- 3 Uncommon
- 1 Reverse-Holo slot (eligible Common/Uncommon/Rare)
- 1 Rare-or-better slot
- 1 Basic Energy display slot (not collectible)

Rare-or-better slot odds:

- Regular Rare or Holo Rare: 70%
- Double Rare / equivalent: 16%
- Illustration Rare / equivalent: 7%
- Ultra Rare / equivalent: 4%
- Special Illustration Rare / equivalent: 2%
- Hyper Rare / equivalent: 1%

These odds are **simulated for gameplay** and are **not official Pokémon Company odds**.

## Trading Workflow

1. Start trade: `[p]tcg trade start @member`
2. Both sides add/remove cards and set optional credits.
3. Any offer change resets both confirmations.
4. Both members run `[p]tcg trade confirm`.
5. Trade completes atomically or fails safely.
6. Trades expire automatically (default 15 minutes; configurable).

## Marketplace Workflow

1. Seller lists a card with member-chosen price.
2. Card is locked while listed.
3. Buyer purchases listing if valid and affordable.
4. Credits and ownership transfer together; listing is removed only on success.
5. Optional guild market tax (default 0%).

All pricing is member-created marketplace data.  
The bot does **not** assign official, fixed, estimated, or rarity-based card values.

## Data Stored

Per guild:

- Enabled sets
- Pack prices
- Set cache (supported set card metadata snapshots)
- Active trades
- Active market listings
- Completed trade audit history (bounded)
- Completed market sale history (bounded)
- Pack limits, trade expiry, market tax, schema version
- Card lock state and ID counters

Per member (per guild):

- Owned card instances
- Unopened packs
- Favorites
- Pull statistics
- Completed trade count
- Pack opening count

## Disclaimer

- Pokémon names, card information, and card images belong to their respective owners.
- This is a fan-made game system for Discord gameplay using Red economy credits.
