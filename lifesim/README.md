# LifeSim

LifeSim is a Red Discord bot cog that simulates a small life economy using Red bank credits.

## Features

- Choose from multiple configurable jobs
- Buy food from a server-editable shop
- Buy and live in houses with upkeep and bonuses
- Track hunger, energy, happiness, and career XP
- Edit jobs, foods, houses, and tuning values inside Discord

## Quick Start

1. Load the cog: `[p]load lifesim`
2. View commands: `[p]lifesim help`
3. Check your life state: `[p]lifesim profile`
4. Apply for a job: `[p]lifesim jobs apply freelance`
5. Work: `[p]lifesim work`

## Admin Tools

- `[p]lifesim jobs admin add|edit|remove|resetdefaults`
- `[p]lifesim shop admin add|edit|remove|resetdefaults`
- `[p]lifesim house admin add|edit|remove|resetdefaults`
- `[p]lifesim settings view|setstarting|setdecay|setrest|setupkeep|resetdefaults`

## Data Statement

This cog stores per-guild life simulation settings and per-member life state including job, house, needs, inventory, XP, and timestamps.
