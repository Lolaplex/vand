# Agent install (this repo)

You are installing **vand** for the human. Do every step yourself. Do not dump a command list for them to run.

This is a Python 3 stdlib CLI. Catalog is `~/.vand/` (this machine). Shareable pins are `vand.lock` (relative paths). Follow [`skills/vand/SKILL.md`](skills/vand/SKILL.md) for daily use. Flags: `vand --help-json`, never scrape `--help`. `--help-json` and `vand man` are **generated from argparse** — no committed man page.

## Procedure

1. Same interpreter your Agent uses. From the clone:
   - `python vand.py init --root <clone-root>`
   - Windows: `py -3` if that is what your Agent uses. macOS/Linux: `python3` if `python` is missing.
   - If they ran the command *inside* this checkout with default `--root .`, init uses the **parent** folder as clone root (stacks sit next to `vand`, not inside it).
2. `init` already does the rest:
   - writes `~/.vand/catalog.json`
   - `pip install -e` this checkout (CLI on PATH)
   - copies the skill to `~/.cursor/skills/vand` and `~/.agents/skills/vand`
   - adopts this checkout if it is under the clone root
   - replicates `examples/shared.lock` (clone missing repos, run their install hooks, adopt them)
   - `adopt` / `add` also run `hook-sync` (git pin hooks) on each registered clone
3. Ask once only if `<clone-root>` is unknown (the folder that holds their checkouts).
4. Tell the human **one** thing: reload your Agent so the user-level skill is picked up. You cannot do that for them.

After `init`, if they already had catalog repos before pin hooks existed, run once: `vand hook-sync`.

**Two hook commands (do not confuse):** `sync-hooks` refreshes manifest install/update commands in the catalog from `vand.yml`. `hook-sync` installs git `.git/hooks/` that run `pin --here --quiet` after commit/pull/rebase/checkout.

Do not also run `install-skills`, `adopt vand`, or `replicate` unless `init` failed partway. `--no-lock` skips the shared stack. `--no-pip` skips the editable install.

## Done when

- `vand --help-json` prints JSON (command on PATH)
- User skill files exist under `~/.cursor/skills/vand` and `~/.agents/skills/vand`
- Catalog lists this clone (and stack repos from the lock, if replicate ran)
