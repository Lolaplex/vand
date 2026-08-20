---
name: vand
description: Catalog source instances, pin revisions in origins.lock, replicate stacks, adopt remotes, and update without force-reset. Use when the user mentions vand, origins.lock, vand.lock, clone root, replicate a stack, pin commits, adopt a repo, catalog.json, source.yml, or shared clones across machines. Prefer vand --help-json over scraping --help or README.
---

# vand

Python 3 stdlib CLI. Materializes and pins **source instances** on a machine (not npm/pip lockfiles inside one project). Git is the first driver; manifests and the provenance ledger are source-agnostic.

<!-- vand-paths -->
First install from this clone: `pip install -e .` then `vand init --root <clone-root>`. That does pip -e, skills, catalog, adopt self, and `examples/origins.lock`. After that, invoke `vand` on PATH. Fallback: `python -m vand`. Machine-readable spec: `vand --help-json` (generated from argparse; same source as `vand man`).
<!-- /vand-paths -->

## Always

1. Run `vand --help-json` (or `vand --help-json <command>`) for flags. Do not scrape `--help` or README.
2. Never `git reset --hard` / force-checkout dirty or diverged trees. `update` is ff-only. Use `consolidate` for merge/rebase.
3. vand reads **only** the remote named `origin`. Fix a wrong URL with `git remote set-url origin <url>`, not `git remote add origin`. Then `vand rm NAME` and `adopt` if the catalog already stored the old URL (`pin` does not refresh `url`/`remote`).
4. Catalog (`~/.vand/catalog.json`) is **this machine**. `origins.lock` is shareable provenance: relative `target` only, no hooks, no absolute clone root. Execution attempts go to `~/.vand/logs/` — never confuse lock and log.

## Layout

| Path | What |
|------|------|
| `~/.vand/catalog.json` | Curated source instances + clone root (private) |
| `~/.vand/logs/` | Timestamped command logs (attempts, failures) |
| `<clone-root>/origins.lock` | Shareable provenance ledger (v2) |
| `<clone-root>/VAND.md` | Human table |
| `<repo>/source.yml` | Quotient manifest: install/update/verify/deinstall shell commands |
| `<clone>/.git/hooks/` | Git pin hooks from `hook-sync` (calls `pin --here --quiet`) |

**Read aliases:** manifests — `vand.yml`, `vend.ini`, etc.; ledger — `vendor.lock`, `vand.lock`, `shared.lock`. **Write always:** `source.yml`, `origins.lock`.

## When this skill fires

- Bootstrap or sync a shared stack (`replicate`, `update`, `push`)
- Register an existing folder (`adopt`) or clone (`add`)
- Export/pin a ledger for another computer
- Remove a source instance (`deinstall`; default purges target dir)
- Origin mismatch after a GitHub transfer into an org
- Repo authors: add `source.yml` instead of README-only install steps
- New clone under the clone root that should be tracked

## Buddy / other machine

If vand is not installed yet (clone root = folder that will hold checkouts):

```powershell
git clone https://github.com/Lolaplex/vand.git
python vand/vand.py init --root .
```

`init` is the whole first-run: catalog, `pip install -e` this clone, agent skills, adopt vand, replicate `examples/origins.lock`, adopt those repos. If you run `init` *inside* the vand checkout with default `--root .`, clone root becomes the parent directory.

`--no-lock` skips the shared stack. `--no-pip` skips the editable install. `vand install-skills` remains if you only need to refresh the skill files.

## Daily

```powershell
vand status
vand status --fetch
vand update              # ff-only; re-runs update hook on commit change
vand consolidate         # merge when ff-only cannot; --rebase optional
vand consolidate --continue NAME
vand push
vand pin --export        # after local commits you want in the ledger
vand hook-sync           # install pin hooks in all catalog clones (once)
vand deinstall NAME      # remove from catalog + purge target (default)
vand deinstall NAME --keep  # catalog-only removal (like rm)
```

Dirty/diverged: leave them. Tell the user. Do not invent a reset.

## Lock-step (git pin hooks)

Plain `git pull` / commit / rebase does not update the catalog. **`hook-sync`** installs git hooks in `.git/hooks/`. Do not confuse with **`sync-hooks`** (manifest install/update commands → catalog).

| Command | Syncs |
|---------|--------|
| `sync-hooks` | `source.yml` install/update → `catalog.json` |
| `hook-sync` | git hooks → `pin --here --quiet` after git events |

| Git hook | Event |
|----------|--------|
| `post-commit` | commit |
| `post-merge` | pull merge / ff |
| `post-rewrite` | rebase / amend |
| `post-checkout` | checkout when HEAD changed |

Each runs `vand pin --here --quiet` (`|| true`). Lookup is by clone path. Failures: `~/.vand/logs/hook-pin.log`. `adopt` / `add` install hooks; existing clones need `vand hook-sync` once. `--force` appends onto a foreign hook file. Do not set global `core.hooksPath`. Do not auto-export `origins.lock`. `git push` does not change HEAD.

## Catalog vs ledger

- `init [--root PATH]` first-run: catalog, pip -e this clone, skills, adopt self, replicate `examples/origins.lock`. Default `--root .` is cwd; if that is this checkout, parent is used.
- `scan` lists git folders under root not in the catalog.
- `adopt FOLDER` registers an existing clone; origin URL becomes source origin.
- `add owner/repo` clones then registers (GitHub shorthand, https/ssh, `file://`, local path).
- `export` writes `origins.lock` + `VAND.md` beside the clone root.
- `replicate LOCK [--root PATH]` clones missing targets, checks out pinned revisions, runs install hooks from each tree's manifest. Omit `--root` → directory containing the lockfile.
- `verify` exits 1 on drift (HEAD != ledger revision); runs manifest `verify` hook when present.

## Manifests (repo authors)

At repo root: **`source.yml`** (canonical). Read aliases: `vand.yml`, `vend.ini`, json variants, etc.

```yaml
version: 1
install: python -m pip install -e .
update: python -m pip install -e .
verify: python -m unittest discover -s tests -v
deinstall: optional prelude before purge
```

`version: 1` is **required**. Each hook is a string or YAML list (multi-step → joined with `&&`).

`install` after add/install/replicate. `update` after update/consolidate when the revision changed (defaults to `install`). `vand sync-hooks` refreshes the catalog from disk. Heuristics if no manifest: Makefile `install`, package.json, requirements.txt, uv.lock, composer.json, go.mod.

## Origin mismatch

Different remotes are allowed. Identity is the **commit SHA**, not the GitHub repo name.

`update` / `push` still use `origin`. `self-update` may fast-forward another configured remote only if `HEAD` is an ancestor of that remote's branch tip (same history, extra commits). It then runs the update hook (`pip install -e .`) so PATH stays on this checkout. After other commands, vand observes at most once per 24h and patches only when that residual is a clean fast-forward.

A personal fork and an org copy (`you/app` vs `Lolaplex/app`) are not the same project just because the name matches. Add the org remote (or set origin to it) so the pin can be fetched:

```powershell
git remote add lolaplex https://github.com/Lolaplex/<repo>.git
git fetch lolaplex
```

If origin should be the org (push target):

```powershell
git remote set-url origin https://github.com/<org>/<repo>.git
git fetch origin
git branch --set-upstream-to=origin/<branch>
```

Do not `git remote add origin` (that name is taken). Needs org write access to push.

## Agent files

This repo ships `skills/vand/SKILL.md`. First `init` copies it to `~/.cursor/skills/vand/` and `~/.agents/skills/vand/` (paths block filled with this checkout). `AGENTS.md` is the install spec. Reload your Agent after first install. In-repo `.cursor/` and `man/` are generated / local copies — gitignored, not the source of truth.
