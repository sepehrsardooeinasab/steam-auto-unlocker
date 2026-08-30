# Steam Auto Unlocker

Paces out Steam achievement unlocks for a game through [ArchiSteamFarm](https://github.com/JustArchiNET/ArchiSteamFarm) (ASF), spreading them across realistic delays and sessions instead of firing them all at once.

**[Unlock Scheduler](https://sepehrsardooeinasab.github.io/steam-auto-unlocker/)** — paste a SteamHunters achievement export in the browser and it generates a delay/session config for you.

## How it works

1. Generate a `config_<name>.json` from a SteamHunters achievement export using the [Unlock Scheduler](https://sepehrsardooeinasab.github.io/steam-auto-unlocker/) and drop it in `jsons/` (see [Setup](#setup)).
2. Run `runsteamunlocker <name>` — it makes sure ArchiSteamFarm is running, then walks the achievement list, calling `aset <appid> <achievement>` through ASF's local API with the configured delays between unlocks and idle sessions between groups.
3. Progress is checkpointed to `jsons/progress_<name>.json`, so the script can be stopped and resumed without re-unlocking or losing its place. Once every achievement is unlocked, both files are cleaned up automatically.

## Setup

### 1. ArchiSteamFarm + achievement plugin

`aset` is not a built-in ASF command — it's provided by the [ASFAchievementManager](https://github.com/CatPoweredPlugins/ASFAchievementManager) plugin.

- Download and set up [ArchiSteamFarm](https://github.com/JustArchiNET/ArchiSteamFarm).
- Download [ASFAchievementManager](https://github.com/CatPoweredPlugins/ASFAchievementManager) and drop it into ASF's `plugins/` folder.
- Create a bot following ASF's own setup instructions. ASF supports running multiple bots, but this project only assumes a single one (`bot1`) — unlocking achievements on your own account doesn't need more.
- For steadier performance, tweak the configs:
  - In the bot's config (`archifarm/config/bot1.json`), set `"FarmingPreferences": 1` to disable card farming.
  - In `archifarm/config/ASF.json`, set `"AutoRestart": false` to stop ASF from auto-restarting.

### 2. Generate a config from SteamHunters

- On [SteamHunters](https://steamhunters.com), in settings enable **show hidden achievements**, and prefer showing time in seconds.
- Open the page for the game you own and want achievements unlocked for. Find a player with a legitimate, completed profile for it, and use the default sort (normal in-game achievement order, not grouped).
- Select and copy the achievement list text, from the first achievement through the last.
- Paste it into the [Unlock Scheduler](https://sepehrsardooeinasab.github.io/steam-auto-unlocker/), generate the config, and save the resulting file into `jsons/` in this project.

This step is manual because SteamHunters has no public API for per-player achievement data and blocks automated access (Cloudflare-protected, `robots.txt` disallows crawlers) — the Unlock Scheduler exists specifically to make pasted, human-copied text quick to turn into a config.

### 3. Python

Python 3 is required. The unlocker only uses the standard library — no extra packages to install.

### 4. Shell integration

Add the launcher to your `PATH` and (if you use zsh) source the completion script:

```sh
export PATH="/path/to/steam-auto-unlocker:$PATH"
source "/path/to/steam-auto-unlocker/runsteamunlocker.zsh-completion"
```

Put these lines in `~/.zshrc` — or, if you use oh-my-zsh, in a file under `~/.oh-my-zsh/custom/` instead, since anything there is auto-sourced. The completion script is zsh-specific; skip the `source` line under bash.

## Layout

- `unlocker/` — the Python package that drives unlocking:
  - `runner.py` — the unlock loop: delays, session breaks, resuming from saved progress
  - `api.py` — talks to ArchiSteamFarm's local Web API (`aset`, `play`, `reset`)
  - `state.py` — reads/writes `jsons/config_*.json` and `jsons/progress_*.json`
  - `run_unlocker.py` — CLI entry point
- `runsteamunlocker` — bash launcher: starts ArchiSteamFarm if needed, then runs the unlocker for a given config
- `runsteamunlocker.zsh-completion` — tab-completion for available configs
- `docs/` — the Unlock Scheduler web page (published via GitHub Pages)
- `jsons/`, `csvs/`, `archifarm/` — per-machine runtime data (ASF install, bot credentials, generated configs); gitignored, not part of the repo

## Usage

```sh
runsteamunlocker <config-name>   # run the unlocker using jsons/config_<config-name>.json
runsteamunlocker -h              # help
```

## Disclaimer

This doesn't violate Steam's own terms of service, but it does violate the rules of achievement-tracking/ranking sites (e.g. SteamHunters) that expect achievements to reflect legitimate play. Use at your own risk.
