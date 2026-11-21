# 001 — Core architecture

**Status:** implemented (git backend)  
**Source chat:** [GitHub repo vendor log](../research/001-origin-chat-github-repo-vendor-log.md)

## Decisions

1. **Two-layer storage:** machine-private catalog (`~/.vand/catalog.json`) + shareable lock at clone root (`vand.lock`, relative paths only).
2. **Git is v1 backend only** — protocol vocabulary (source identity, origin, revision, acquire, update, materialize, verify) may later accept Fossil, archives, digests. No migration code for renames; users reclone/adopt manually.
3. **Lifecycle hooks live in repo** (`vand.yml`) as shell commands — agents read `--help-json`, not README scrape.
4. **Update policy:** ff-only fetch/checkout; dirty/diverged trees left alone; `consolidate` for merge/rebase conflicts. Never `git reset --hard`.
5. **Origin discipline:** vand reads only remote named `origin`; fix URL with `git remote set-url`, refresh catalog via rm/adopt if URL stored wrong.
6. **Lock-step:** git hooks from `hook-sync` call `pin --here --quiet` after commit/merge/rebase/checkout. Distinct from `sync-hooks` (manifest → catalog).
7. **Self-update:** throttled observe (not every invocation); safe fast-forward when HEAD is ancestor of remote tip.
8. **Install bootstrap:** `init` does catalog, pip editable, skills, adopt self, optional replicate from lock — one first-run path.
9. **Scheduler out of scope** — cron/timer automation is a separate project.

## Open rename (discussed, not done)

- `vand.yml` → `.source.yml`
- `vand.lock` → `origin.lock`
- Restore **deinstall** hook in manifest schema
