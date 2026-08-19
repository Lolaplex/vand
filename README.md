# git-updater

Track shared git repos on your machine, pin them to exact commits, and export a **shareable `vendor.lock`** so another computer can replicate the whole stack with one command.

Works with **any git remote** — GitHub, GitLab, Bitbucket, self-hosted, `file://` paths, and local folder clones — not just `github.com`.

Manual CLI only — no background scheduler. For general cron/timer automation, a separate desktop scheduler project is planned later.

## Why not submodules / myrepos / hawser?

| Tool | What it does | Gap |
|------|----------------|-----|
| **git submodules** | Pins repos inside one repo | Detached HEADs, nested checkout pain |
| **[myrepos](https://myrepos.branchable.com/)** (`mr update`) | Update many repos | No exact SHA lockfile for replication |
| **[hawser](https://github.com/Nastwinns/hawser)** (`haw sync`) | Multi-repo lockfile + verify | Rust stack, heavier scope |
| **[Repo Family](https://github.com/JohnsonArnek/Github-Family)** | Control repo + lock | No personal catalog curation |
| **git-updater** | Catalog + `vendor.lock` + `replicate` | Small Python stdlib CLI |

Per-app library pins (a `vendor.lock` *inside* one project) pin trees in that project. git-updater pins **whole repos** across a clone root on your machine.

## Install

Python 3.9+, Git, and pip. From a clone:

```powershell
python -m pip install -e .
git-updater init --root <clone-root>
```

Or from a fresh clone, skip the separate pip — `init` runs `pip install -e` itself:

```powershell
python git_updater.py init --root <clone-root>
```

That puts `git-updater` on PATH (Windows: the Python `Scripts` folder). Editable (`-e`) keeps the command pointed at this checkout, so `self-update` / `git pull` still work.

Without installing, you can still run `python git_updater.py` or `git-updater.cmd` from this directory.

**Coding agents:** follow [`AGENTS.md`](AGENTS.md). That file is the install spec. This README is the map. First-run is `python git_updater.py init --root <clone-root>` (catalog, pip -e, skills, adopt self, shared.lock). Usage skill: [`skills/git-updater/SKILL.md`](skills/git-updater/SKILL.md).

## Help that machines can read

Human `--help` is a wall of text. Agents and other tools should not scrape it.

```powershell
git-updater --help              # people
git-updater --help-json         # full spec: commands, flags, args, defaults
git-updater --help-json replicate
git-updater help --json
git-updater help replicate --json
git-updater man                 # roff man page on stdout (also: man/git-updater.1)
git-updater man --write man/git-updater.1
```

`--help-json` is generated from argparse, so it cannot drift from the real CLI. On Unix: `git-updater man | groff -man -Tutf8 | less` or install `man/git-updater.1` on `MANPATH`.

## Quick start

```powershell
# 1. First-run (catalog + PATH + skills + adopt this clone + shared.lock)
git-updater init --root <clone-root>

# 2. See existing clones not yet tracked
git-updater scan

# 3. Register an existing folder (reads .git-updater.yaml if present)
git-updater adopt <folder>
git-updater adopt <folder> --install "make install"  # optional override

# 4. Check state
git-updater status
git-updater status --fetch

# 5. Export shareable lock + human vendor log
git-updater export
# -> <clone-root>/vendor.lock
# -> <clone-root>/VENDOR.md
```

`<clone-root>` is whatever directory you keep checkouts in. Catalog state lives in `~/.git-updater/` on **this** machine only.

## Shared stack (Lolaplex)

This repo publishes a starter lock for the tools we share. It does **not** pin git-updater itself (the lock lives in this repo).

```powershell
cd <clone-root>
git clone https://github.com/Lolaplex/git-updater.git
python git-updater/git_updater.py init --root .
```

`init` creates the catalog, `pip install -e` this clone, installs the agent skill, adopts git-updater, replicates `examples/shared.lock`, and adopts those repos. If you run `init` inside the git-updater folder with default `--root .`, the clone root becomes the parent.

Daily sync (pull when the other person pushed):

```powershell
git-updater update agent-memory
git-updater push agent-memory   # after your own commits
```

`update` fast-forwards a clean tree and re-runs install hooks when the commit changes. Dirty or diverged trees are left alone — `consolidate` if you both edited.

## On another machine

Copy `vendor.lock` (and optionally `VENDOR.md`), then:

```powershell
git-updater replicate vendor.lock --root <clone-root>
```

If you omit `--root`, clones land next to the lockfile. Absolute `root` values from another computer are ignored.

Clones missing repos, checks out exact SHAs, runs each repo's `install` hook.

Dry run first:

```powershell
git-updater replicate vendor.lock --root <clone-root> --dry-run
```

## Daily workflow

```powershell
git-updater update          # fetch + fast-forward clean repos; re-run install on commit change
git-updater consolidate     # fetch + merge when ff-only fails; lists conflicts to fix
git-updater consolidate --continue NAME   # after fixing conflict markers
git-updater consolidate --abort NAME      # abort stuck merge/rebase
git-updater push            # push tracking branches; re-pin HEAD
git-updater pin             # pin catalog to current HEAD after local commits
git-updater pin --export    # pin + write vendor.lock
git-updater verify          # exit 1 if any clone != lock (CI gate)
```

Dirty or diverged repos are **never** force-reset. Use `consolidate` when `update` stops at diverged/ff-only failures, then `pin`.

## Lock-step with plain `git pull` / `git push`

Cursor, GitHub Desktop, and raw `git` do not update the catalog. Install per-clone hooks once:

```powershell
git-updater hook-sync              # all catalog repos
git-updater hook-sync agent-memory # one repo
```

`adopt` and `add` install these hooks automatically. Each hook runs `git-updater pin --here --quiet` after commit, merge (pull), rebase/amend, or branch checkout. Failures log to `~/.git-updater/logs/hook-pin.log` and never block git. Hooks are per-repo (not global `core.hooksPath`). Foreign hook files are skipped unless `--force` (appends the pin block). `vendor.lock` is still explicit: `pin --export` / `export`.

## Remote URLs

| Form | Example |
|------|---------|
| GitHub shorthand | `owner/repo` |
| HTTPS / SSH | `https://gitlab.com/group/project.git`, `git@host:org/repo.git` |
| Local path | a folder on disk, or `file://` URL |

`adopt` reads `origin` (push target). Extra remotes are stored as `mirrors` only if they **already contain the pinned SHA**. `vendor.lock` stores `remote` (id), `url` (clone source), and optional `mirrors` (other fetch URLs for that same commit). Older locks with only `github` still load.

**Pins are SHAs.** `Klix927/agent-memory` and `Lolaplex/agent-memory` are different remotes. They are fetch sources for a pin only when that exact commit exists there. `replicate` / `install` fetch `url` plus listed `mirrors` until the lock SHA is present, then check out that SHA. `update` / `push` still follow **origin**. A GitHub repo *name* match is not identity.

## For repo authors — `.git-updater.yaml`

Stop writing install guides only in README. Add a **machine-readable manifest** at the repo root:

```yaml
# .git-updater.yaml
version: 1
install: npm ci && npm run build
update: npm ci
verify: npm test
```

Also supported: `git-updater.json`, `.git-updater.json`, `.git-updater.yml`.

| Key | When it runs |
|-----|----------------|
| `install` | After `add`, `install`, `replicate` |
| `update` | After `update` / `consolidate` when the commit changed (defaults to `install`) |
| `verify` | Reserved — not executed yet |

Commands can be a string, a list (run in sequence with `&&`), or `{ run: scripts/setup.sh, shell: bash }`.

If no manifest exists, git-updater tries conservative heuristics (`Makefile` `install`, `package.json`, `requirements.txt`, `composer.json`, `go.mod`, `uv.lock`).

```powershell
git-updater scan          # shows [manifest file] next to repos that declare hooks
git-updater sync-hooks    # refresh catalog from on-disk manifests
```

The manifest is **in the repo** — it travels with the code, gets pinned in `vendor.lock`, and works on every machine after `replicate`. No Nix required.

See [`git-updater.schema.json`](git-updater.schema.json) for the JSON shape.

## Where files live

| File | Purpose |
|------|---------|
| `~/.git-updater/catalog.json` | Your curated repo list (private to this machine, includes local clone root) |
| `~/.git-updater/logs/` | Timestamped logs from update/install/replicate |
| `<clone-root>/vendor.lock` | Shareable pin snapshot (relative paths only; no machine root) |
| `<clone-root>/VENDOR.md` | Human-readable vendor table |

## Commands

| Command | Description |
|---------|-------------|
| `init [--root PATH]` | First-run: catalog, pip -e, skills, adopt self, replicate `examples/shared.lock` |
| `scan` | Git folders under root not in catalog |
| `add owner/repo [--install CMD]` | Clone + register |
| `adopt FOLDER [--install CMD]` | Register existing clone (auto-reads manifest) |
| `sync-hooks [NAME]` | Refresh install/update from repo manifests |
| `rm NAME` | Remove from catalog (keeps folder) |
| `status [NAME] [--fetch]` | pinned / behind / ahead / dirty / diverged / missing |
| `update [NAME]` | Fetch; ff-only if clean |
| `consolidate [NAME] [--rebase]` | Merge/rebase when update cannot ff-only |
| `consolidate --continue [NAME]` | Finish merge/rebase after fixing conflicts |
| `consolidate --abort [NAME]` | Abort in-progress merge/rebase |
| `push [NAME]` | Push branch |
| `pin [NAME] [--export]` | Pin to HEAD |
| `pin --here [--quiet]` | Pin catalog row for cwd (git hooks) |
| `hook-sync [NAME] [--force]` | Install pin hooks in catalog clones |
| `install [NAME]` | Clone missing + checkout pin + install hooks |
| `export [--out PATH]` | Write vendor.lock + VENDOR.md |
| `replicate LOCK [--root PATH] [--dry-run]` | Bootstrap from lock |
| `verify [--lock PATH]` | Drift check |
| `self-check [--fetch] [--json]` | Check if git-updater itself is up to date |
| `self-update` | Fast-forward this checkout (only refs that are descendants of HEAD) + reinstall |
| `install-skills` | Copy agent skill to `~/.cursor/skills` and `~/.agents/skills` |
| `help [CMD] [--json]` | Human or JSON help |
| `man [--write FILE]` | Print / write roff man page |

Global: `--no-self-check` skips the 24h residual self-update. `--help-json` prints the CLI spec and exits.

## vendor.lock format (v1)

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

Scheduled `git-updater update` belongs in a **separate desktop scheduler** project (general cron/timer for any command). git-updater v1 is intentionally manual with visible terminal output and logs under `~/.git-updater/logs/`.

Example future job:

```yaml
- name: sync-repos
  command: git-updater update && git-updater export
  cwd: /path/to/git-updater
  schedule: "0 9 * * *"
```

## Tests

```powershell
python -m unittest discover -s tests -v
```

## Self-update

git-updater keeps **itself** current from this checkout's remotes (personal `origin` + org mirror both count, same as other repos):

```powershell
git-updater self-check              # uses 24h cache when offline-friendly
git-updater self-check --fetch        # force fresh compare
git-updater self-update               # ff-only + `python -m pip install -e .`
```

After other commands, git-updater **observes** remotes at most once per 24h. If that observe shows a clean fast-forward residual, it patches (ff-only + reinstall). Within the TTL it only prints a hint from cache. Dirty or diverged trees are left alone. Disable with `--no-self-check` or `GIT_UPDATER_SKIP_SELF_CHECK=1`. `self-update` always observes and patches.

Detection order:

1. If `git_updater.py` lives in a git clone: compare `HEAD` to configured remotes. Catalog `update` follows origin. `self-update` fast-forwards a remote tip only when `HEAD` is an ancestor of that tip.
2. Else if `origin` is a GitHub `owner/repo` URL: GitHub API
3. Otherwise: unknown (no hardcoded upstream)

## License

MIT
