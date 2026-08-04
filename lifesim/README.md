# LifeSim

LifeSim is a Red Discord bot cog that simulates a small life economy using Red bank credits.

## Features

- Choose from multiple configurable jobs
- Buy food from a server-editable shop
- Buy and live in houses with upkeep and bonuses
- Track hunger, energy, happiness, and career XP
- Edit jobs, foods, houses, and tuning values inside Discord
- Work every 4 hours by default
- Sleep once per day with an 8-hour LifeSim command lockout
- Rest every 2 hours

## Quick Start

1. Load the cog: `[p]load lifesim`
2. View commands: `[p]lifesim help`
3. Check your life state: `[p]lifesim profile`
4. Apply for a job: `[p]lifesim jobs apply freelance`
5. Work: `[p]lifesim work`
6. Sleep: `[p]lifesim sleep`
7. See everything: `[p]lifesim commands`

## Admin Tools

- `[p]lifesim jobs admin add|edit|remove|resetdefaults`
- `[p]lifesim shop admin add|edit|remove|resetdefaults`
- `[p]lifesim house admin add|edit|remove|resetdefaults`
- `[p]lifesim settings view|setstarting|setdecay|setrest|setrestcooldown|setupkeep|resetdefaults`
- `[p]lifesim settings setworkcooldown|setsleepcooldown`
- `[p]lifesim member set`
- `[p]lifesim member cooldowns reset`

## Data Statement

This cog stores per-guild life simulation settings and per-member life state including job, house, needs, inventory, XP, and timestamps.
