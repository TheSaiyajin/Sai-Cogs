# TerritoryWar

TerritoryWar is a Red-DiscordBot alpha cog for a two-alliance, seven-day territory war.

## Core alpha features

- Registration, random alliance assignment, random leader/advisor assignment
- Configurable preparation phase before war starts
- Fixed 21-territory map with adjacency rules and two capitals
- Officer-led power allocation, withdrawals, attacks, and reinforcements
- Tick-based battles with configurable interval and damage
- Capital lock until configurable day (default day 5)
- Persistent map message in a configured channel (edited in-place)
- Two always-available minigames (`codebreaker`, `memory`) with personal-best-only power rewards
- Season winner history and seasonal reset behavior

## Installation

1. Install and load the cog.
2. Set channels:
   - `[p]war admin setmapchannel #channel`
   - `[p]war admin setfeedchannel #channel`
3. Open registration:
   - `[p]war admin openreg`
4. Players join:
   - `[p]war join`
5. Close registration and begin prep:
   - `[p]war admin closereg`

## Command overview

### Player commands

- `[p]war status`
- `[p]war join`
- `[p]war leave`
- `[p]war map`
- `[p]war profile [member]`
- `[p]war leaderboard`
- `[p]war battles`
- `[p]war play codebreaker start`
- `[p]war play codebreaker guess <4 unique digits>`
- `[p]war play memory start`
- `[p]war play memory answer <sequence>`

### Officer commands

- `[p]war officer allocate <territory> <amount>`
- `[p]war officer withdraw <territory> <amount>`
- `[p]war officer attack <source> <target> <amount>`
- `[p]war officer reinforce <battle_id> <amount>`

### Admin commands

- `[p]war admin setmapchannel #channel`
- `[p]war admin setfeedchannel #channel`
- `[p]war admin openreg`
- `[p]war admin closereg`
- `[p]war admin start`
- `[p]war admin stop`
- `[p]war admin reset`
- `[p]war admin timing <key> <value>`
- `[p]war admin value <key> <value>`
- `[p]war admin setleader <1|2> <member>`
- `[p]war admin setadvisor <1|2> <member>`
- `[p]war admin forceresolve <battle_id> <attacker|defender>`
- `[p]war admin regeneratemap`

## Notes

- Uses UTC epoch timestamps for persistence.
- Background checks run periodically and survive restarts.
- No command cooldowns or energy systems for minigames.
- Power generation is capped by personal bests (max 100 per minigame per player per season).
