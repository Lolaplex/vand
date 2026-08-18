---
name: git-updater
description: Catalog git clones, pin SHAs in vendor.lock, replicate stacks, adopt remotes, and update without force-reset. Use when the user mentions git-updater, vendor.lock, clone root, replicate a stack, pin commits, adopt a repo, catalog.json, or shared GitHub clones across machines. Prefer git-updater --help-json over scraping --help or README.
---

# git-updater

Python 3 stdlib CLI. Pins **whole repos** on a machine (not npm/pip lockfiles inside one project).

<!-- git-updater-paths -->
First install from this clone: `python git_updater.py init --root <clone-root>`. That does pip -e, skills, catalog, adopt self, and `examples/shared.lock`. After that, invoke `git-updater` on PATH. Fallback: `python git_updater.py`. Machine-readable spec: `git-updater --help-json`.
<!-- /git-updater-paths -->

## Always

1. Run `git-updater --help-json` (or `git-updater --help-json <command>`) for flags. Do not scrape `--help` or README.
2. Never `git reset --hard` / force-checkout dirty or diverged trees. `update` is ff-only. Use `consolidate` for merge/rebase.
3. git-updater reads **only** the remote named `origin`. Fix a wrong URL with `git remote set-url origin <url>`, not `git remote add origin`. Then `git-updater rm NAME` and `adopt` if the catalog already stored the old URL (`pin` does not refresh `url`/`remote`).
4. Catalog (`~/.git-updater/catalog.json`) is **this machine**. `vendor.lock` is shareable: relative `path` only, no absolute clone root.

## Layout

| Path | What |
|------|------|
| `~/.git-updater/catalog.json` | Curated repos + clone root (private) |
| `~/.git-updater/logs/` | Timestamped command logs |
| `<clone-root>/vendor.lock` | Shareable SHA pins |
| `<clone-root>/VENDOR.md` | Human table |
| `<repo>/.git-updater.yaml` | Install/update/verify hooks in the repo |

## When this skill fires

- Bootstrap or sync a shared stack (`replicate`, `update`, `push`)
- Register an existing folder (`adopt`) or clone (`add`)
- Export/pin a lock for another computer
- Origin mismatch after a GitHub transfer into an org
- Repo authors: add `.git-updater.yaml` instead of README-only install steps
- New clone under the clone root that should be tracked

## Buddy / other machine

If git-updater is not installed yet (clone root = folder that will hold checkouts):

```powershell
git clone https://github.com/Lolaplex/git-updater.git
python git-updater/git_updater.py init --root .
```

`init` is the whole first-run: catalog, `pip install -e` this clone, agent skills, adopt git-updater, replicate `examples/shared.lock`, adopt those repos. If you run `init` *inside* the git-updater checkout with default `--root .`, clone root becomes the parent directory.

`--no-lock` skips the shared stack. `--no-pip` skips the editable install. `git-updater install-skills` remains if you only need to refresh the skill files.

## Daily

```powershell
git-updater status
git-updater status --fetch
git-updater update              # ff-only; re-runs update/install hook on commit change
git-updater consolidate         # merge when ff-only cannot; --rebase optional
git-updater consolidate --continue NAME
git-updater push
git-updater pin --export        # after local commits you want in the lock
```

Dirty/diverged: leave them. Tell the user. Do not invent a reset.

## Catalog vs lock

- `init [--root PATH]` first-run: catalog, pip -e this clone, skills, adopt self, replicate `examples/shared.lock`. Default `--root .` is cwd; if that is this checkout, parent is used.
- `scan` lists git folders under root not in the catalog.
- `adopt FOLDER` registers an existing clone; origin URL becomes `remote` + `url`.
- `add owner/repo` clones then registers (GitHub shorthand, https/ssh, `file://`, local path).
- `export` writes `vendor.lock` + `VENDOR.md` beside the clone root.
- `replicate LOCK [--root PATH]` clones missing repos to that root, checks out pins, runs install hooks. Omit `--root` → directory containing the lockfile. Ignore absolute `root` in old locks.
- `verify` exits 1 on drift (HEAD != lock SHA).

## Manifests (repo authors)

At repo root: `.git-updater.yaml` / `.json` / `.yml`.

```yaml
version: 1
install: python -m pip install -e .
update: python -m pip install -e .
verify: python -m unittest discover -s tests -v
```

`install` after add/install/replicate. `update` after update/consolidate when the commit changed (defaults to `install`). `git-updater sync-hooks` refreshes the catalog from disk. Heuristics if no manifest: Makefile `install`, package.json, requirements.txt, uv.lock, composer.json, go.mod.

## Origin mismatch

Different remotes are allowed. Identity is the **commit SHA**, not the GitHub repo name.

`mirrors` in the lock are extra fetch URLs for that pin. `replicate` / `install` fetch origin, `url`, and mirrors until the SHA exists, then check out that SHA. `update` / `push` still use `origin`.

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

This repo ships `skills/git-updater/SKILL.md`. First `init` copies it to `~/.cursor/skills/git-updater/` and `~/.agents/skills/git-updater/` (paths block filled with this checkout). `AGENTS.md` is the install spec. Reload Cursor after first install.
