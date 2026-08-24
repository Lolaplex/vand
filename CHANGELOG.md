# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-08-20

### Added

- Source-agnostic core: `SourceRef`, `SourceInstance`, and `GitDriver` types replace the git-shaped catalog; Git is the first driver, not the ontology.
- `source.yml` quotient manifest (protocol `version: 1`) mapping the fixed verb set `install` / `update` / `deinstall` plus optional `verify` to opaque shell commands (string or list, lists join with `&&`). Read aliases: `vand.yml/yaml/ini`, `vend.yml/yaml/ini`, JSON variants; canonical write is always `source.yml`.
- `origins.lock` v2 provenance ledger at the clone root: origin + pinned revision + relative target only — no hooks, no branch, no per-machine state. Read aliases: `vendor.lock`, `vand.lock`, `shared.lock`. v1 locks are rejected, not migrated.
- `vand deinstall NAME [--keep]`: removes the catalog entry and purges the target directory by default; runs an optional manifest `deinstall` hook first.
- All-or-nothing ledger discipline: catalog pins and ledger exports only update after successful materialize/verify; failed attempts go to `~/.vand/logs/`.
- `vand verify`: drift check against the ledger plus optional manifest `verify` hook.
- Machine-readable CLI spec via `vand --help-json` and a generated man page (`vand man`), both produced from argparse so they cannot drift from the parser.

### Changed

- Lock vs log separation documented: `origins.lock` records completed source facts; execution attempts live in `~/.vand/logs/`.
- `src/` package layout with console script entry point (`pip install -e .` puts `vand` on PATH).

## [Unreleased]

### Fixed

- Failed clones (missing remote/repository) now print a clean one-block error with git's stderr and a targeted hint instead of an unhandled traceback.
- `push` refuses when a repo diverged from its remote (ahead and behind); reconcile with `consolidate` instead of pushing over rewritten history.

### Changed

- Most commands accept multiple repos inline in order (e.g. `vand update agents-docs agents-memory vand`); with no names they apply to the whole catalog.
- `consolidate --rebase` refuses when it would rewrite local history across more than one upstream commit; plain merge is suggested instead, and completed rebases are logged explicitly with old/new SHAs.

[Unreleased]: https://github.com/Lolaplex/vand/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Lolaplex/vand/releases/tag/v1.0.0
