# Agent install (this repo)

You are installing **git-updater** for the human. Do every step yourself. Do not dump a command list for them to run.

This is a Python 3 stdlib CLI. Catalog is `~/.git-updater/` (this machine). Shareable pins are `vendor.lock` (relative paths). Follow [`skills/git-updater/SKILL.md`](skills/git-updater/SKILL.md) for daily use. Flags: `git-updater --help-json`, never scrape `--help`. `--help-json` and `git-updater man` are **generated from argparse** — no committed man page.

## Procedure

1. Same interpreter your Agent uses. From the clone:
   - `python git_updater.py init --root <clone-root>`
   - Windows: `py -3` if that is what your Agent uses. macOS/Linux: `python3` if `python` is missing.
   - If they ran the command *inside* this checkout with default `--root .`, init uses the **parent** folder as clone root (stacks sit next to `git-updater`, not inside it).
2. `init` already does the rest:
   - writes `~/.git-updater/catalog.json`
   - `pip install -e` this checkout (CLI on PATH)
   - copies the skill to `~/.cursor/skills/git-updater` and `~/.agents/skills/git-updater`
   - adopts this checkout if it is under the clone root
   - replicates `examples/shared.lock` (clone missing repos, run their install hooks, adopt them)
   - `adopt` / `add` also run `hook-sync` (git pin hooks) on each registered clone
3. Ask once only if `<clone-root>` is unknown (the folder that holds their checkouts).
4. Tell the human **one** thing: reload your Agent so the user-level skill is picked up. You cannot do that for them.

After `init`, if they already had catalog repos before pin hooks existed, run once: `git-updater hook-sync`.

**Two hook commands (do not confuse):** `sync-hooks` refreshes manifest install/update commands in the catalog from `.git-updater.yaml`. `hook-sync` installs git `.git/hooks/` that run `pin --here --quiet` after commit/pull/rebase/checkout.

Do not also run `install-skills`, `adopt git-updater`, or `replicate` unless `init` failed partway. `--no-lock` skips the shared stack. `--no-pip` skips the editable install.

## Done when

- `git-updater --help-json` prints JSON (command on PATH)
- User skill files exist under `~/.cursor/skills/git-updater` and `~/.agents/skills/git-updater`
- Catalog lists this clone (and stack repos from the lock, if replicate ran)
