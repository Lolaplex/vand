# vand

Track shared git repos on your machine, pin them to exact commits, and export a **shareable `vand.lock`** so another computer can replicate the whole stack with one command.

Works with **any git remote** — GitHub, GitLab, Bitbucket, self-hosted, `file://` paths, and local folder clones — not just `github.com`.

Manual CLI only — no background scheduler. For general cron/timer automation, a separate desktop scheduler project is planned later.

## Why not submodules / myrepos / hawser?

| Tool | What it does | Gap |
|------|----------------|-----|
| **git submodules** | Pins repos inside one repo | Detached HEADs, nested checkout pain |
| **[myrepos](https://myrepos.branchable.com/)** (`mr update`) | Update many repos | No exact SHA lockfile for replication |
| **[hawser](https://github.com/Nastwinns/hawser)** (`haw sync`) | Multi-repo lockfile + verify | Rust stack, heavier scope |
| **[Repo Family](https://github.com/JohnsonArnek/Github-Family)** | Control repo + lock | No personal catalog curation |
| **vand** | Catalog + `vand.lock` + `replicate` | Small Python stdlib CLI |

Per-app library pins (a `vand.lock` *inside* one project) pin trees in that project. vand pins **whole repos** across a clone root on your machine.

## Install

Python 3.9+, Git, and pip. From a clone:

```powershell
python -m pip install -e .
vand init --root <clone-root>
```

Or from a fresh clone, skip the separate pip — `init` runs `pip install -e` itself:

```powershell
python vand.py init --root <clone-root>
```

That puts `vand` on PATH (Windows: the Python `Scripts` folder). Editable (`-e`) keeps the command pointed at this checkout, so `self-update` / `git pull` still work.

Without installing, you can still run `python vand.py` or `vand.cmd` from this directory.

**Coding agents:** follow [`AGENTS.md`](AGENTS.md). That file is the install spec. This README is the map. First-run is `python vand.py init --root <clone-root>` (catalog, pip -e, skills, adopt self, shared.lock). Usage skill: [`skills/vand/SKILL.md`](skills/vand/SKILL.md).

## Help that machines can read

Human `--help` is a wall of text. Agents and other tools should not scrape it.

**All machine-readable help is generated from argparse at runtime** — there is no committed man page or JSON spec in the repo. If you add a flag or command, `--help-json` and `man` update automatically.

```powershell
vand --help              # people
vand --help-json         # full spec: commands, flags, args, defaults
vand --help-json replicate
vand help --json
vand help replicate --json
vand man                 # roff on stdout (generated; same source as --help-json)
vand man --write FILE    # optional local copy, e.g. man/vand.1 for groff
```

On Unix: `vand man | groff -man -Tutf8 | less`. Optional: `vand man --write man/vand.1` then `MANPATH=man man vand`.

Do **not** commit `man/` or in-repo `.cursor/` skill copies — both are gitignored local output. Source of truth: `vand.py` (argparse) and `skills/vand/SKILL.md`.

## Quick start

```powershell
# 1. First-run (catalog + PATH + skills + adopt this clone + shared.lock)
vand init --root <clone-root>

# 2. See existing clones not yet tracked
vand scan

# 3. Register an existing folder (reads vand.yml if present)
vand adopt <folder>
vand adopt <folder> --install "make install"  # optional override

# 4. Check state
vand status
vand status --fetch

# 5. Export shareable lock + human vendor log
vand export
# -> <clone-root>/vand.lock
# -> <clone-root>/VAND.md
```

`<clone-root>` is whatever directory you keep checkouts in. Catalog state lives in `~/.vand/` on **this** machine only.

## Shared stack (Lolaplex)

This repo publishes a starter lock for the tools we share. It does **not** pin vand itself (the lock lives in this repo).

```powershell
cd <clone-root>
git clone https://github.com/Lolaplex/vand.git
python vand/vand.py init --root .
```

`init` creates the catalog, `pip install -e` this clone, installs the agent skill, adopts vand, replicates `examples/shared.lock`, and adopts those repos. If you run `init` inside the vand folder with default `--root .`, the clone root becomes the parent.

Daily sync (pull when the other person pushed):

```powershell
vand update agent-memory
vand push agent-memory   # after your own commits
```

`update` fast-forwards a clean tree and re-runs install hooks when the commit changes. Dirty or diverged trees are left alone — `consolidate` if you both edited.

## On another machine

Copy `vand.lock` (and optionally `VAND.md`), then:

```powershell
vand replicate vand.lock --root <clone-root>
```

If you omit `--root`, clones land next to the lockfile. Absolute `root` values from another computer are ignored.

Clones missing repos, checks out exact SHAs, runs each repo's `install` hook.

Dry run first:

```powershell
vand replicate vand.lock --root <clone-root> --dry-run
```

## Daily workflow

```powershell
vand update          # fetch + fast-forward clean repos; re-run install on commit change
vand consolidate     # fetch + merge when ff-only fails; lists conflicts to fix
vand consolidate --continue NAME   # after fixing conflict markers
vand consolidate --abort NAME      # abort stuck merge/rebase
vand push            # push tracking branches; re-pin HEAD
vand pin             # pin catalog to current HEAD after local commits
vand pin --export    # pin + write vand.lock
vand hook-sync       # once: install git hooks so plain git keeps catalog pinned
vand verify          # exit 1 if any clone != lock (CI gate)
```

Dirty or diverged repos are **never** force-reset. Use `consolidate` when `update` stops at diverged/ff-only failures, then `pin`.

## Lock-step with plain `git pull` / `git push`

`update`, `push`, `pin`, and `consolidate` write HEAD into `~/.vand/catalog.json`. Your Agent, GitHub Desktop, and raw `git` do not.

### Two different "hooks"

| Command | What it syncs | Where |
|---------|----------------|-------|
| **`sync-hooks`** | **Manifest** install/update shell commands from each repo's `vand.yml` into the catalog | `catalog.json` fields |
| **`hook-sync`** | **Git** hooks that re-pin the catalog after commit / pull / rebase / checkout | `<clone>/.git/hooks/` |

Do not confuse them. `sync-hooks` does not install pin hooks. `hook-sync` does not read manifests.

### Pin hooks (`hook-sync`)

```powershell
vand hook-sync                 # all catalog repos
vand hook-sync agent-memory    # one repo
vand pin --here                # pin the catalog row for cwd (manual test)
vand pin --here --quiet        # same, used inside git hooks
```

`adopt` and `add` run `hook-sync` on the new clone. **Existing catalog entries:** run `hook-sync` once after upgrading vand.

| Git hook | When it runs |
|----------|----------------|
| `post-commit` | Local commit |
| `post-merge` | `git pull` that fast-forwards or merges |
| `post-rewrite` | Rebase / amend |
| `post-checkout` | Branch switch, only when HEAD actually changed |

Each hook runs `vand pin --here --quiet`. That looks up the catalog row by this clone's path and sets `commit` (and branch) to HEAD. Failures append to `~/.vand/logs/hook-pin.log` and **never** fail the git command (`|| true`).

**Prerequisites:** clone must be in the catalog (`adopt` / `add` / `init`). `vand` must be on PATH (or the hook falls back to `py -3` / `python` + this checkout's `vand.py`).

`git push` does not move HEAD. Commit/pull already pinned the SHA; `status --fetch` is enough to see whether origin is caught up.

Do not set global `core.hooksPath` (Git replaces per-repo hooks instead of chaining). Do not alias `git`. Do not auto-export `vand.lock` from hooks (`pin --export` / `export` stay explicit). Foreign hook files are left alone unless `--force` (appends the pin block after the existing script).

`desktop-commander` is the later scheduler clock (`vand update` at 09:00). Pin hooks are the residual patch for ad-hoc git in the working tree.

## Remote URLs

| Form | Example |
|------|---------|
| GitHub shorthand | `owner/repo` |
| HTTPS / SSH | `https://gitlab.com/group/project.git`, `git@host:org/repo.git` |
| Local path | a folder on disk, or `file://` URL |

`adopt` reads `origin` (push target). Extra remotes are stored as `mirrors` only if they **already contain the pinned SHA**. `vand.lock` stores `remote` (id), `url` (clone source), and optional `mirrors` (other fetch URLs for that same commit). Older locks with only `github` still load.

**Pins are SHAs.** `Klix927/agent-memory` and `Lolaplex/agent-memory` are different remotes. They are fetch sources for a pin only when that exact commit exists there. `replicate` / `install` fetch `url` plus listed `mirrors` until the lock SHA is present, then check out that SHA. `update` / `push` still follow **origin**. A GitHub repo *name* match is not identity.

## For repo authors — `vand.yml`

Stop writing install guides only in README. Add a **machine-readable manifest** at the repo root:

```yaml
# vand.yml
version: 1
install: npm ci && npm run build
update: npm ci
verify: npm test
```

Also supported: `vand.yaml`, `vand.json`.

| Key | When it runs |
|-----|----------------|
| `install` | After `add`, `install`, `replicate` |
| `update` | After `update` / `consolidate` when the commit changed (defaults to `install`) |
| `verify` | Reserved — not executed yet |

Commands can be a string, a list (run in sequence with `&&`), or `{ run: scripts/setup.sh, shell: bash }`.

If no manifest exists, vand tries conservative heuristics (`Makefile` `install`, `package.json`, `requirements.txt`, `composer.json`, `go.mod`, `uv.lock`).

```powershell
vand scan          # shows [manifest file] next to repos that declare hooks
vand sync-hooks    # refresh catalog from on-disk manifests
```

The manifest is **in the repo** — it travels with the code, gets pinned in `vand.lock`, and works on every machine after `replicate`. No Nix required.

See [`vand.schema.json`](vand.schema.json) for the JSON shape.

## Where files live

| File | Purpose |
|------|---------|
| `~/.vand/catalog.json` | Your curated repo list (private to this machine, includes local clone root) |
| `~/.vand/logs/` | Timestamped logs from update/install/replicate |
| `~/.vand/logs/hook-pin.log` | Quiet pin failures from git hooks |
| `<clone>/.git/hooks/` | Pin hooks installed by `hook-sync` (not in the clone's tree) |
| `<clone-root>/vand.lock` | Shareable pin snapshot (relative paths only; no machine root) |
| `<clone-root>/VAND.md` | Human-readable vendor table |

## Commands

| Command | Description |
|---------|-------------|
| `init [--root PATH]` | First-run: catalog, pip -e, skills, adopt self, replicate `examples/shared.lock` |
| `scan` | Git folders under root not in catalog |
| `add owner/repo [--install CMD]` | Clone + register |
| `adopt FOLDER [--install CMD]` | Register existing clone (auto-reads manifest; runs `hook-sync`) |
| `sync-hooks [NAME]` | Refresh catalog **install/update commands** from repo manifests (not git hooks) |
| `rm NAME` | Remove from catalog (keeps folder) |
| `status [NAME] [--fetch]` | pinned / behind / ahead / dirty / diverged / missing |
| `update [NAME]` | Fetch; ff-only if clean |
| `consolidate [NAME] [--rebase]` | Merge/rebase when update cannot ff-only |
| `consolidate --continue [NAME]` | Finish merge/rebase after fixing conflicts |
| `consolidate --abort [NAME]` | Abort in-progress merge/rebase |
| `push [NAME]` | Push branch |
| `pin [NAME] [--export]` | Pin named repo (or all) to HEAD |
| `pin --here [--quiet]` | Pin catalog row for cwd; `--quiet` for git hooks (logs on failure) |
| `hook-sync [NAME] [--force]` | Install **git** pin hooks in `.git/hooks/` |
| `install [NAME]` | Clone missing + checkout pin + install hooks |
| `export [--out PATH]` | Write vand.lock + VAND.md |
| `replicate LOCK [--root PATH] [--dry-run]` | Bootstrap from lock |
| `verify [--lock PATH]` | Drift check |
| `self-check [--fetch] [--json]` | Check if vand itself is up to date |
| `self-update` | Fast-forward this checkout (only refs that are descendants of HEAD) + reinstall |
| `install-skills` | Copy agent skill to `~/.cursor/skills` and `~/.agents/skills` |
| `help [CMD] [--json]` | Human or JSON help (JSON generated from argparse) |
| `man [--write FILE]` | Print / write roff man page (generated from argparse; not in repo) |

Global: `--no-self-check` skips the 24h residual self-update. `--help-json` prints the CLI spec and exits.

## vand.lock format (v1)

Portable. No absolute paths. `path` is relative to the clone root you pass to `replicate`.

```json
{
  "version": 1,
  "repos": [
    {
      "name": "example-app",
      "remote": "acme/example-app",
      "github": "acme/example-app",
      "url": "https://github.com/acme/example-app.git",
      "path": "example-app",
      "branch": "main",
      "commit": "b6dfd52…",
      "install": "npm ci && npm run build",
      "update": "npm ci",
      "mirrors": ["https://github.com/you/example-app.git"]
    }
  ]
}
```

## Automation (future)

Scheduled `vand update` belongs in a **separate desktop scheduler** project (general cron/timer for any command). vand v1 is intentionally manual with visible terminal output and logs under `~/.vand/logs/`.

Example future job:

```yaml
- name: sync-repos
  command: vand update && vand export
  cwd: /path/to/vand
  schedule: "0 9 * * *"
```

## Tests

```powershell
python -m unittest discover -s tests -v
```

## Self-update

vand keeps **itself** current from this checkout's remotes (personal `origin` + org mirror both count, same as other repos):

```powershell
vand self-check              # uses 24h cache when offline-friendly
vand self-check --fetch        # force fresh compare
vand self-update               # ff-only + `python -m pip install -e .`
```

After other commands, vand **observes** remotes at most once per 24h. If that observe shows a clean fast-forward residual, it patches (ff-only + reinstall). Within the TTL it only prints a hint from cache. Dirty or diverged trees are left alone. Disable with `--no-self-check` or `VAND_SKIP_SELF_CHECK=1`. `self-update` always observes and patches.

Detection order:

1. If `vand.py` lives in a git clone: compare `HEAD` to configured remotes. Catalog `update` follows origin. `self-update` fast-forwards a remote tip only when `HEAD` is an ancestor of that tip.
2. Else if `origin` is a GitHub `owner/repo` URL: GitHub API
3. Otherwise: unknown (no hardcoded upstream)

## License

MIT
