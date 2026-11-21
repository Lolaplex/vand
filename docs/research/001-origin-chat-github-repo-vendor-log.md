# Origin chat: GitHub repo vendor log

**Title:** GitHub repo vendor log  
**Transcript:** [1508b941-6886-4a83-a62d-8e1ffb02614c](file:///C:/Users/fabi0/.cursor/projects/c-Users-fabi0-repos-git-updater/agent-transcripts/1508b941-6886-4a83-a62d-8e1ffb02614c/1508b941-6886-4a83-a62d-8e1ffb02614c.jsonl)  
**When:** 2026-08-18 → 2026-08-19  
**Canonical repo today:** https://github.com/Lolaplex/vand

## Problem statement (opening message)

Shared GitHub repos across a team are painful to bootstrap on a new machine: you lose track of what is installed, from where, and at which revision. Need a **vendor log** beside clones that pins origin + commit, supports update/reinstall when upstream changes, and push-back when you edit locally — without manual re-clone loops.

## What shipped from this thread

| Iteration | Name | Notes |
|-----------|------|-------|
| v0 plan | git-updater | Python stdlib CLI, `~/.git-updater/catalog.json`, shareable lock beside clone root |
| rename | vend | Source-driver abstraction discussed; git = first backend only |
| rename | **vand** | Current name; repo `Lolaplex/vand`; config `~/.vand/` |

Built features (still in tree unless renamed again):

- Machine catalog (`~/.vand/catalog.json`) + shareable `vand.lock` + human `VAND.md`
- Per-repo `vand.yml` lifecycle hooks (install/update/verify) — not README-only install
- `vand --help-json` + generated man (from argparse, not committed)
- Commands: init, add, adopt, update, consolidate, push, pin, replicate, hook-sync, sync-hooks, install-skills
- Git pin hooks (`pin --here`) for lock-step with plain git pull/commit
- Self-update throttled (not every CLI invocation)
- ff-only update; never force-reset dirty trees
- Agent skill install on `init`

## Explicit non-goals from chat

- **No cron / scheduler in vand** — desktop automation is a separate future project
- **No baked-in migration** when renaming repos (manual reclone / adopt)
- **No LLM-on-write in memory layer** — agent-memory uses markdown + tools over it
- **Nix rejected** as install story (too painful for this use case)

## Naming evolution (user-authored, late in thread)

Target vocabulary (aspirational — not all renamed in code yet):

| Concept | Current in repo | User target |
|---------|-----------------|-------------|
| CLI | `vand` | `vand` |
| Lifecycle descriptor | `vand.yml` | `.source.yml` |
| Portable provenance | `vand.lock` | `origin.lock` |
| Core verbs | add/update/install/verify | same |

Semantic object: **source instance** — origin, immutable revision/digest, materialized target, optional lifecycle hooks. Git is one **source driver**, not the ontology.

## Buddy / second machine (manual, no migration tooling)

1. Clone `https://github.com/Lolaplex/vand.git`
2. `python -m pip install -e .` then `vand init --root <clone-root>` (or adopt existing stack)
3. Move `~/.git-updater` → `~/.vand` if old config exists
4. `vand rm git-updater` + `vand adopt vand` if catalog still lists old name/path
5. Fix origin URL in git + catalog after org transfers (`git remote set-url`, then rm/adopt)
6. `vand hook-sync`, `vand install-skills`

## Related chats (same workspace folder)

- [6a2d10f7-42ca-4de0-a708-fcb263657668](file:///C:/Users/fabi0/.cursor/projects/c-Users-fabi0-repos-git-updater/agent-transcripts/6a2d10f7-42ca-4de0-a708-fcb263657668/6a2d10f7-42ca-4de0-a708-fcb263657668.jsonl) — agent-memory install + ingest design (spawned from this work)

## Competing tools noted in thread

git submodules, myrepos, hawser, Repo Family — vand gap: small stdlib CLI + machine catalog + shareable SHA lock + replicate + optional install hooks.
