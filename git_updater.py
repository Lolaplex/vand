#!/usr/bin/env python3
"""Git vendor tracker — catalog, vendor.lock, replicate."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

CATALOG_VERSION = 1
LOCK_VERSION = 1
CONFIG_DIR = Path.home() / ".git-updater"
CATALOG_PATH = CONFIG_DIR / "catalog.json"
LOG_DIR = CONFIG_DIR / "logs"
SELF_CHECK_CACHE = CONFIG_DIR / "self-check.json"
SELF_CHECK_TTL_SECS = 24 * 60 * 60
SKIP_SELF_CHECK_COMMANDS = frozenset(
    {"self-check", "self-update", "init", "help", "man", "install-skills", "pin", "hook-sync"}
)
SKILL_NAME = "git-updater"
SKILL_PATHS_BEGIN = "<!-- git-updater-paths -->"
SKILL_PATHS_END = "<!-- /git-updater-paths -->"
PIN_HOOK_MARKER = "git-updater-managed: pin --here"
PIN_HOOK_NAMES = ("post-commit", "post-merge", "post-rewrite", "post-checkout")

MANIFEST_FILENAMES = (
    ".git-updater.json",
    "git-updater.json",
    ".git-updater.yaml",
    "git-updater.yaml",
    ".git-updater.yml",
    "git-updater.yml",
)

GITHUB_RE = re.compile(
    r"(?:github\.com[/:]|git@github\.com:)([^/]+)/([^/\s]+?)(?:\.git)?/?$"
)


@dataclass
class RepoEntry:
    name: str
    remote: str
    url: str
    path: str
    branch: str
    commit: str
    install: str | None = None
    update: str | None = None
    mirrors: list[str] = field(default_factory=list)

    @property
    def github(self) -> str:
        """Backward-compatible alias; same as remote id."""
        return self.remote

    def to_dict(self) -> dict[str, Any]:
        d = {
            "name": self.name,
            "remote": self.remote,
            "github": self.remote,
            "url": self.url,
            "path": self.path,
            "branch": self.branch,
            "commit": self.commit,
        }
        if self.install is not None:
            d["install"] = self.install
        if self.update is not None:
            d["update"] = self.update
        if self.mirrors:
            d["mirrors"] = list(self.mirrors)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepoEntry:
        remote = data.get("remote") or data.get("github") or data.get("url", "?")
        mirrors = data.get("mirrors") or []
        if isinstance(mirrors, str):
            mirrors = [mirrors]
        return cls(
            name=data["name"],
            remote=remote,
            url=data["url"],
            path=data["path"],
            branch=data["branch"],
            commit=data["commit"],
            install=data.get("install"),
            update=data.get("update"),
            mirrors=[str(item) for item in mirrors if str(item).strip()],
        )


@dataclass
class Catalog:
    version: int
    root: str
    repos: list[RepoEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "root": self.root,
            "repos": [r.to_dict() for r in self.repos],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Catalog:
        return cls(
            version=data.get("version", 1),
            root=data["root"],
            repos=[RepoEntry.from_dict(r) for r in data.get("repos", [])],
        )

    def find(self, name: str) -> RepoEntry | None:
        for repo in self.repos:
            if repo.name == name:
                return repo
        return None

    def remove(self, name: str) -> bool:
        before = len(self.repos)
        self.repos = [r for r in self.repos if r.name != name]
        return len(self.repos) < before


def normalize_path(path: str | Path) -> str:
    return str(Path(path).resolve()).replace("\\", "/")


def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def load_catalog() -> Catalog:
    if not CATALOG_PATH.exists():
        raise SystemExit(
            f"No catalog at {CATALOG_PATH}. Run: git-updater init --root <path>"
        )
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return Catalog.from_dict(data)


def save_catalog(catalog: Catalog) -> None:
    ensure_config_dir()
    CATALOG_PATH.write_text(
        json.dumps(catalog.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_log(command: str, lines: list[str]) -> Path:
    ensure_config_dir()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = LOG_DIR / f"{stamp}-{command}.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def install_root() -> Path:
    return Path(__file__).resolve().parent


def skill_template_path() -> Path:
    return install_root() / "skills" / SKILL_NAME / "SKILL.md"


def user_skill_targets(home: Path | None = None) -> list[Path]:
    home = home or Path.home()
    return [
        home / ".cursor" / "skills" / SKILL_NAME / "SKILL.md",
        home / ".agents" / "skills" / SKILL_NAME / "SKILL.md",
    ]


def machine_skill_text(root: Path | None = None) -> str:
    template_path = skill_template_path()
    if not template_path.is_file():
        raise SystemExit(f"Missing skill template: {template_path}")
    template = template_path.read_text(encoding="utf-8")
    root = root or install_root()
    root_s = normalize_path(root)
    block = (
        f"Install (engine): `{root_s}`\n"
        "Invoke (PATH): `git-updater` after `python -m pip install -e .` from that clone.\n"
        f"Fallback: `python {root_s}/git_updater.py`\n"
        "Machine-readable spec: `git-updater --help-json` (or `git-updater --help-json COMMAND`).\n"
    )
    if SKILL_PATHS_BEGIN in template and SKILL_PATHS_END in template:
        pre, rest = template.split(SKILL_PATHS_BEGIN, 1)
        _, post = rest.split(SKILL_PATHS_END, 1)
        return pre + SKILL_PATHS_BEGIN + "\n" + block + SKILL_PATHS_END + post
    return template


def install_user_skills(home: Path | None = None) -> list[Path]:
    text = machine_skill_text()
    written: list[Path] = []
    for path in user_skill_targets(home):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def cmd_install_skills(args: argparse.Namespace) -> None:
    for path in install_user_skills():
        print(f"Wrote {path}")


def pip_editable_self() -> None:
    root = install_root()
    print(f"-> {sys.executable} -m pip install -e {root}")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", str(root)],
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit("pip install -e failed; git-updater is not on PATH")


def infer_clone_root(root_arg: str) -> Path:
    """If --root is this checkout, use the parent so stacks land next to git-updater."""
    requested = Path(root_arg).expanduser().resolve()
    self_root = install_root()
    if requested == self_root:
        parent = self_root.parent
        print(f"This checkout is git-updater; clone root -> {parent}")
        return parent
    return requested


def default_stack_lock() -> Path | None:
    path = install_root() / "examples" / "shared.lock"
    return path if path.is_file() else None


def resolve_self_remote(install_path: Path | None = None) -> str:
    """Remote id from this checkout's origin. Empty if unknown (no hardcoded upstream)."""
    root = install_path or install_root()
    origin = read_origin(root)
    return origin[0] if origin else ""


@dataclass
class SelfCheckResult:
    install_path: Path
    remote: str
    branch: str | None
    local_commit: str | None
    remote_commit: str | None
    status: str
    ahead: int = 0
    behind: int = 0
    source: str = "git"
    message: str = ""
    sync_ref: str | None = None


def github_api_json(url: str) -> dict[str, Any] | None:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "git-updater",
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return None


def github_remote_tip(github: str, branch: str | None = None) -> tuple[str, str] | None:
    if not github or "/" not in github:
        return None
    owner, repo = github.split("/", 1)
    if not owner or not repo or "/" in repo:
        return None
    if not branch:
        meta = github_api_json(f"https://api.github.com/repos/{owner}/{repo}")
        if not meta:
            return None
        branch = meta.get("default_branch") or "main"
    commit_data = github_api_json(
        f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}"
    )
    if not commit_data:
        return None
    sha = commit_data.get("sha")
    if not sha:
        return None
    return branch, sha


def load_self_check_cache() -> dict[str, Any] | None:
    if not SELF_CHECK_CACHE.exists():
        return None
    try:
        return json.loads(SELF_CHECK_CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_self_check_cache(result: SelfCheckResult) -> None:
    ensure_config_dir()
    payload = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "install_path": normalize_path(result.install_path),
        "remote": result.remote,
        "github": result.remote,
        "branch": result.branch,
        "local_commit": result.local_commit,
        "remote_commit": result.remote_commit,
        "status": result.status,
        "ahead": result.ahead,
        "behind": result.behind,
        "source": result.source,
        "sync_ref": result.sync_ref,
    }
    SELF_CHECK_CACHE.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def cache_is_fresh(cache: dict[str, Any], fetch: bool) -> bool:
    if fetch:
        return False
    checked_at = cache.get("checked_at")
    if not checked_at:
        return False
    try:
        ts = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    age = datetime.now(timezone.utc) - ts.astimezone(timezone.utc)
    return age.total_seconds() < SELF_CHECK_TTL_SECS


def check_self(fetch: bool = True, use_cache: bool = True) -> SelfCheckResult:
    root = install_root()
    remote = resolve_self_remote(root)
    cached = load_self_check_cache() if use_cache else None
    if cached and cache_is_fresh(cached, fetch):
        if cached.get("install_path") == normalize_path(root):
            cached_remote = cached.get("remote") or cached.get("github", remote)
            return SelfCheckResult(
                install_path=root,
                remote=cached_remote,
                branch=cached.get("branch"),
                local_commit=cached.get("local_commit"),
                remote_commit=cached.get("remote_commit"),
                status=cached.get("status", "unknown"),
                ahead=int(cached.get("ahead", 0)),
                behind=int(cached.get("behind", 0)),
                source=cached.get("source", "cache"),
                message="(cached)",
                sync_ref=cached.get("sync_ref"),
            )

    local_commit: str | None = None
    branch: str | None = None
    remote_commit: str | None = None
    ahead = behind = 0
    source = "git"
    status = "unknown"
    sync_ref: str | None = None

    if (root / ".git").exists():
        if fetch:
            fetch_all_remotes(root)
        local_commit = current_commit(root)
        branch = current_branch(root)
        if is_dirty(root):
            status = "dirty"
        elif branch:
            names = self_remote_names(root)
            ref, sync = pick_self_ff_ref(root, branch, names)
            if sync == "behind" and ref:
                status = "behind"
                sync_ref = ref
                remote_commit = parse_rev(root, ref)
                remote_name = remote_from_sync_ref(ref, branch)
                if remote_name:
                    ahead, behind = remote_ahead_behind(root, branch, remote_name)
            elif sync == "diverged":
                status = "diverged"
                if "origin" in names:
                    ahead, behind = remote_ahead_behind(root, branch, "origin")
                    remote_commit = parse_rev(root, f"origin/{branch}")
            elif "origin" in names:
                origin_ahead, _origin_behind = remote_ahead_behind(
                    root, branch, "origin"
                )
                remote_commit = parse_rev(root, f"origin/{branch}")
                if origin_ahead:
                    status = "ahead"
                    ahead = origin_ahead
                elif local_commit and remote_commit:
                    status = (
                        "up-to-date" if local_commit == remote_commit else "unpinned"
                    )
                elif local_commit:
                    status = "up-to-date"
                    remote_commit = local_commit
            elif names:
                remote_commit = parse_rev(root, f"{names[0]}/{branch}")
                if local_commit and remote_commit:
                    status = (
                        "up-to-date" if local_commit == remote_commit else "unpinned"
                    )
                elif local_commit:
                    status = "up-to-date"
            elif local_commit:
                source = "github-api"
                origin_info = read_origin(root)
                if can_use_github_api(remote, origin_info[1] if origin_info else None):
                    tip = github_remote_tip(remote, branch)
                else:
                    tip = None
                if tip:
                    branch, remote_commit = tip
                    status = "behind" if local_commit != remote_commit else "up-to-date"
                    behind = 0 if status == "up-to-date" else 1
        elif local_commit:
            source = "github-api"
            origin = read_origin(root)
            if can_use_github_api(remote, origin[1] if origin else None):
                tip = github_remote_tip(remote)
            else:
                tip = None
            if tip:
                branch, remote_commit = tip
                status = "behind" if local_commit != remote_commit else "up-to-date"
                behind = 0 if status == "up-to-date" else 1
    else:
        source = "github-api"
        origin = read_origin(root)
        if can_use_github_api(remote, origin[1] if origin else None):
            tip = github_remote_tip(remote)
        else:
            tip = None
        if tip:
            branch, remote_commit = tip
            status = "no-local-git"
        else:
            status = "unknown"

    result = SelfCheckResult(
        install_path=root,
        remote=remote,
        branch=branch,
        local_commit=local_commit,
        remote_commit=remote_commit,
        status=status,
        ahead=ahead,
        behind=behind,
        source=source,
        sync_ref=sync_ref,
    )
    save_self_check_cache(result)
    return result


def remote_display_url(remote_id: str) -> str:
    if not remote_id:
        return "(no origin)"
    if remote_id.startswith("local:"):
        return remote_id.removeprefix("local:")
    if "/" in remote_id and not remote_id.startswith("local:"):
        if remote_id.count("/") == 1 and not remote_id.startswith("http"):
            return f"https://github.com/{remote_id}"
    return remote_id


def format_self_check(result: SelfCheckResult, verbose: bool = True) -> str:
    local = result.local_commit[:7] if result.local_commit else "n/a"
    remote = result.remote_commit[:7] if result.remote_commit else "n/a"
    upstream = remote_display_url(result.remote)
    lines = [
        f"git-updater install: {normalize_path(result.install_path)}",
        f"upstream: {upstream}" + (f" ({result.branch})" if result.branch else ""),
        f"local:  {local}   remote: {remote}   status: {result.status}",
    ]
    if result.status == "behind":
        detail = f"{result.behind} commit(s) behind" if result.behind else "update available"
        lines.append(f"-> {detail}. Run: git-updater self-update")
    elif result.status == "dirty":
        lines.append("-> local changes present - commit or stash before self-update")
    elif result.status == "diverged":
        lines.append("-> diverged from upstream - resolve manually, then self-update")
    elif result.status == "no-local-git":
        lines.append(
            f"-> not a git checkout; remote tip is {remote}. Clone from GitHub to track updates."
        )
    elif result.status == "unknown":
        lines.append("-> could not reach upstream (offline or repo not published yet)")
    elif verbose and result.status == "up-to-date":
        lines.append("-> up to date")
    if result.message:
        lines.append(result.message)
    return "\n".join(lines)


def maybe_warn_self_update() -> None:
    if os.environ.get("GIT_UPDATER_SKIP_SELF_CHECK") == "1":
        return
    result = check_self(fetch=False, use_cache=True)
    if result.status == "behind":
        remote = result.remote_commit[:7] if result.remote_commit else "?"
        local = result.local_commit[:7] if result.local_commit else "?"
        print(
            f"\nNote: git-updater update available ({local} -> {remote}). "
            f"Run: git-updater self-check --fetch  |  git-updater self-update",
            file=sys.stderr,
        )


def apply_self_update(
    *,
    interactive: bool = True,
    observed: SelfCheckResult | None = None,
) -> SelfCheckResult:
    """Fast-forward this checkout from remotes that are descendants of HEAD, then reinstall."""
    root = install_root()
    if not (root / ".git").exists():
        message = "git-updater is not a git checkout - clone from GitHub to self-update."
        if interactive:
            raise SystemExit(message)
        return observed or check_self(fetch=False, use_cache=True)

    result = observed if observed is not None else check_self(
        fetch=True, use_cache=False
    )
    if interactive:
        print(format_self_check(result))
    if result.status == "up-to-date":
        _pin_catalog_self(root, result.branch)
        _refresh_self_skills()
        return result

    refuse = {
        "dirty": "Refusing to self-update: working tree has local changes.",
        "diverged": "Refusing to self-update: diverged from upstream.",
        "unknown": "Could not determine upstream state.",
        "ahead": "Local git-updater is ahead of upstream - not pulling.",
        "no-local-git": "git-updater is not a git checkout - clone from GitHub to self-update.",
    }
    if result.status != "behind":
        message = refuse.get(
            result.status, f"Cannot self-update (status: {result.status})."
        )
        if interactive:
            raise SystemExit(message)
        if result.status in ("dirty", "diverged"):
            print(
                f"\nNote: git-updater {result.status} - skip self-update.",
                file=sys.stderr,
            )
        return result

    if is_dirty(root):
        message = "Refusing to self-update: working tree has local changes."
        if interactive:
            raise SystemExit(message)
        print(f"\nNote: {message}", file=sys.stderr)
        return result

    branch = result.branch or current_branch(root) or "main"
    ref = result.sync_ref or f"origin/{branch}"
    print(f"-> git-updater (self)")
    print(f"  git merge --ff-only {ref}")
    try:
        run_git(root, "merge", "--ff-only", ref)
    except subprocess.CalledProcessError as exc:
        message = f"self-update fast-forward failed ({ref})"
        if interactive:
            raise SystemExit(message) from exc
        print(f"Note: {message}", file=sys.stderr)
        return result

    new_head = current_commit(root)
    old = result.local_commit[:7] if result.local_commit else "?"
    if new_head:
        print(f"  fast-forward {old} -> {new_head[:7]} ({ref})")

    entry = self_hook_entry(root)
    if resolve_hook_command(entry, root, "update")[0]:
        run_update_hook(entry, root)
    _refresh_self_skills()
    _pin_catalog_self(root, result.branch, new_head)
    return check_self(fetch=False, use_cache=False)


def _refresh_self_skills() -> None:
    if skill_template_path().is_file():
        for path in install_user_skills():
            print(f"Wrote {path}")


def _pin_catalog_self(
    root: Path, branch: str | None, commit: str | None = None
) -> None:
    pair = catalog_self_entry()
    if not pair:
        return
    catalog, catalog_entry = pair
    head = commit or current_commit(root)
    if not head or catalog_entry.commit == head:
        return
    catalog_entry.commit = head
    if branch:
        catalog_entry.branch = branch
    save_catalog(catalog)
    print(f"  catalog pin {catalog_entry.name} -> {head[:7]}")


def maybe_apply_self_update() -> None:
    """Observe at most once per TTL; patch only when residual is a safe fast-forward."""
    if os.environ.get("GIT_UPDATER_SKIP_SELF_CHECK") == "1":
        return
    cached = load_self_check_cache()
    if cached and cache_is_fresh(cached, fetch=False):
        maybe_warn_self_update()
        return
    result = check_self(fetch=True, use_cache=False)
    if result.status == "behind":
        apply_self_update(interactive=False, observed=result)


def run_git(
    repo_path: Path | None,
    *args: str,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    cmd = ["git"]
    if repo_path is not None:
        cmd.extend(["-C", str(repo_path)])
    cmd.extend(args)
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
    )


def parse_github_remote(url: str) -> tuple[str, str] | None:
    url = url.strip()
    match = GITHUB_RE.search(url)
    if match:
        owner, repo = match.group(1), match.group(2)
        if repo.endswith(".git"):
            repo = repo[:-4]
        return owner, repo
    parsed = urlparse(url)
    if parsed.netloc in ("github.com", "www.github.com"):
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2:
            return parts[0], parts[1].removesuffix(".git")
    return None


def is_local_remote(url: str) -> bool:
    url = url.strip()
    if url.startswith("file:"):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", url):
        return True
    if url.startswith("/"):
        return True
    path = Path(url)
    return path.exists()


def normalize_clone_url(url: str) -> str:
    url = url.strip()
    if url.startswith("file:"):
        parsed = urlparse(url)
        if parsed.netloc:
            return normalize_path(f"{parsed.netloc}{parsed.path}")
        return normalize_path(parsed.path)
    if is_local_remote(url) and not url.startswith("file:"):
        return normalize_path(url)
    return url


def remote_id_from_url(url: str) -> str:
    url = normalize_clone_url(url)
    parsed_github = parse_github_remote(url)
    if parsed_github:
        return github_slug(*parsed_github)
    if is_local_remote(url):
        return f"local:{normalize_path(url)}"
    if url.startswith("git@"):
        host_path = url.split("@", 1)[1]
        if ":" in host_path:
            host, repo_path = host_path.split(":", 1)
            repo_path = repo_path.removesuffix(".git")
            return f"{host}/{repo_path}"
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https", "ssh", "git") and parsed.netloc:
        parts = parsed.path.strip("/").removesuffix(".git")
        if parts:
            return f"{parsed.netloc}/{parts}"
    return url


def default_name_from_spec(spec: str, remote_id: str) -> str:
    if remote_id.startswith("local:"):
        return Path(remote_id.removeprefix("local:")).name
    if "/" in remote_id:
        return remote_id.rsplit("/", 1)[-1]
    return Path(spec).name if spec else "repo"


def github_slug(owner: str, repo: str) -> str:
    return f"{owner}/{repo}"


def canonical_clone_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}.git"


def parse_repo_spec(spec: str) -> tuple[str, str, str]:
    """Return (default_name, remote_id, clone_url)."""
    spec = spec.strip().rstrip("/")
    if re.match(r"^[\w.-]+/[\w.-]+$", spec) and "://" not in spec and not spec.startswith("git@"):
        owner, repo = spec.split("/", 1)
        url = canonical_clone_url(owner, repo)
        return repo, github_slug(owner, repo), url
    if spec.startswith("git@") or "://" in spec or is_local_remote(spec):
        url = normalize_clone_url(spec)
        remote_id = remote_id_from_url(url)
        name = default_name_from_spec(spec, remote_id)
        return name, remote_id, url
    raise ValueError(
        f"Invalid repo spec: {spec} "
        "(use owner/repo, https/ssh git URL, file:// path, or existing local path)"
    )


def repo_abs_path(catalog: Catalog, entry: RepoEntry) -> Path:
    return Path(catalog.root) / entry.path


def read_origin(entry_path: Path) -> tuple[str, str] | None:
    if not (entry_path / ".git").exists():
        return None
    try:
        result = run_git(entry_path, "remote", "get-url", "origin")
    except subprocess.CalledProcessError:
        return None
    url = normalize_clone_url(result.stdout.strip())
    return remote_id_from_url(url), url


def github_owner_repo(remote_id: str, url: str | None = None) -> tuple[str, str] | None:
    """Lowercase (owner, repo) for GitHub remotes; None otherwise."""
    if url:
        parsed = parse_github_remote(url)
        if parsed:
            return parsed[0].lower(), parsed[1].lower()
    parsed = parse_github_remote(remote_id)
    if parsed:
        return parsed[0].lower(), parsed[1].lower()
    if (
        remote_id.count("/") == 1
        and not remote_id.startswith("local:")
        and "://" not in remote_id
        and not remote_id.startswith("git@")
    ):
        owner, repo = remote_id.split("/", 1)
        if "." not in owner:
            return owner.lower(), repo.removesuffix(".git").lower()
    return None


def same_github_project(
    remote_a: str,
    remote_b: str,
    url_a: str | None = None,
    url_b: str | None = None,
) -> bool:
    """True only when both sides are the same clone URL/id. Owner+name, not name alone."""
    return urls_equivalent(url_a or remote_a, url_b or remote_b)


def list_remotes(path: Path) -> list[tuple[str, str, str]]:
    """Return [(name, url, remote_id), ...] for fetch remotes."""
    try:
        result = run_git(path, "remote", "-v")
    except subprocess.CalledProcessError:
        return []
    found: dict[str, tuple[str, str, str]] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        if len(parts) >= 3 and parts[-1] != "(fetch)":
            continue
        name = parts[0]
        url = normalize_clone_url(parts[1])
        found[name] = (name, url, remote_id_from_url(url))
    return list(found.values())


def urls_equivalent(url_a: str, url_b: str) -> bool:
    left = normalize_clone_url(url_a).rstrip("/").lower()
    right = normalize_clone_url(url_b).rstrip("/").lower()
    if left == right:
        return True
    return remote_id_from_url(url_a).lower() == remote_id_from_url(url_b).lower()


def suggested_remote_name(url: str) -> str:
    parts = github_owner_repo(remote_id_from_url(url), url)
    if parts:
        return parts[0]
    return "git-updater"


def ensure_remote(path: Path, url: str) -> str | None:
    """Add url as a named remote if missing. Name = GitHub owner (e.g. lolaplex)."""
    url = normalize_clone_url(url)
    if not url:
        return None
    remotes = list_remotes(path)
    for name, existing, _rid in remotes:
        if urls_equivalent(existing, url):
            return name
    name = suggested_remote_name(url)
    taken = {existing_name for existing_name, _, _ in remotes}
    if name in taken:
        suffix = 2
        while f"{name}-{suffix}" in taken:
            suffix += 1
        name = f"{name}-{suffix}"
    try:
        run_git(path, "remote", "add", name, url)
    except subprocess.CalledProcessError:
        return None
    print(f"  remote + {name} -> {url}")
    return name


def entry_fetch_urls(entry: RepoEntry) -> list[str]:
    urls: list[str] = []
    for candidate in [entry.url, *entry.mirrors]:
        if candidate and not any(urls_equivalent(candidate, existing) for existing in urls):
            urls.append(candidate)
    return urls


def attach_entry_remotes(path: Path, entry: RepoEntry) -> None:
    for url in entry_fetch_urls(entry):
        ensure_remote(path, url)


def remotes_containing_commit(path: Path, commit: str) -> set[str]:
    """Remote names whose tracking refs contain this SHA (empty if unknown)."""
    if not commit or not commit_exists(path, commit):
        return set()
    result = run_git(path, "branch", "-r", "--contains", commit, check=False)
    if result.returncode != 0:
        return set()
    names: set[str] = set()
    for line in result.stdout.splitlines():
        ref = line.strip()
        if not ref or " -> " in ref:
            continue
        if "/" in ref:
            names.add(ref.split("/", 1)[0])
    return names


def mirrors_from_clone(
    path: Path,
    origin_url: str,
    commit: str | None = None,
) -> list[str]:
    """Extra remotes that actually have this commit. Name matching is not identity."""
    commit = commit or current_commit(path)
    containing = remotes_containing_commit(path, commit) if commit else set()
    mirrors: list[str] = []
    for name, url, _rid in list_remotes(path):
        if urls_equivalent(url, origin_url):
            continue
        if containing and name not in containing:
            continue
        if not containing:
            continue
        mirrors.append(url)
    return mirrors


def commit_exists(path: Path, commit: str) -> bool:
    result = run_git(path, "cat-file", "-e", f"{commit}^{{commit}}", check=False)
    return result.returncode == 0


def pin_fetch_remote_names(path: Path, entry: RepoEntry) -> list[str]:
    """Remotes we may fetch the pin SHA from. Not which branch tip to follow."""
    wanted = entry_fetch_urls(entry)
    names: list[str] = []
    for name, url, _rid in list_remotes(path):
        if name == "origin" or any(urls_equivalent(url, w) for w in wanted):
            names.append(name)
    if names:
        return names
    existing = [name for name, _, _ in list_remotes(path)]
    if "origin" in existing:
        return ["origin"]
    return existing[:1]


def self_remote_names(path: Path) -> list[str]:
    """Remotes configured on this checkout. Name matching is not identity."""
    remotes = list_remotes(path)
    names = [name for name, _url, _rid in remotes]
    if names:
        return names
    return ["origin"]


def remote_from_sync_ref(ref: str, branch: str) -> str | None:
    suffix = f"/{branch}"
    if ref.endswith(suffix) and len(ref) > len(suffix):
        return ref[: -len(suffix)]
    return None


def parse_rev(path: Path, ref: str) -> str | None:
    result = run_git(path, "rev-parse", "--verify", ref, check=False)
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def self_hook_entry(root: Path) -> RepoEntry:
    origin = read_origin(root)
    remote, url = origin if origin else ("", "")
    return RepoEntry(
        name="git-updater",
        remote=remote or "git-updater",
        url=url,
        path=str(root),
        branch=current_branch(root) or "main",
        commit=current_commit(root) or "",
    )


def catalog_self_entry() -> tuple[Catalog, RepoEntry] | None:
    if not CATALOG_PATH.exists():
        return None
    try:
        catalog = load_catalog()
    except (OSError, json.JSONDecodeError, KeyError, SystemExit):
        return None
    root = install_root().resolve()
    for entry in catalog.repos:
        if repo_abs_path(catalog, entry).resolve() == root:
            return catalog, entry
    return None


def is_self_checkout(catalog: Catalog, entry: RepoEntry) -> bool:
    return repo_abs_path(catalog, entry).resolve() == install_root().resolve()


def should_defer_self(args: argparse.Namespace, catalog: Catalog, entry: RepoEntry) -> bool:
    """Skip catalog git-updater during bulk update; self-update runs after."""
    if getattr(args, "name", None):
        return False
    if getattr(args, "no_self_check", False):
        return False
    return is_self_checkout(catalog, entry)


def fetch_all_remotes(path: Path, extra_urls: list[str] | None = None) -> None:
    for url in extra_urls or []:
        ensure_remote(path, url)
    for name, _url, _rid in list_remotes(path):
        run_git(path, "fetch", name, check=False)


def fetch_commit(path: Path, commit: str, extra_urls: list[str] | None = None) -> None:
    """Fetch until the pin SHA exists. Extra URLs are fetch sources, not aliases."""
    if commit_exists(path, commit):
        return
    remotes = list_remotes(path)
    names = [name for name, _, _ in remotes]
    if "origin" in names:
        names = ["origin"] + [name for name in names if name != "origin"]
    for name in names:
        run_git(path, "fetch", name, commit, check=False)
        if commit_exists(path, commit):
            return
        run_git(path, "fetch", name, check=False)
        if commit_exists(path, commit):
            return
    for url in extra_urls or []:
        ensure_remote(path, url)
        run_git(path, "fetch", url, commit, check=False)
        if commit_exists(path, commit):
            return
        run_git(path, "fetch", url, check=False)
        if commit_exists(path, commit):
            return


def remote_ahead_behind(path: Path, branch: str, remote: str) -> tuple[int, int]:
    try:
        result = run_git(
            path,
            "rev-list",
            "--left-right",
            "--count",
            f"HEAD...{remote}/{branch}",
        )
    except subprocess.CalledProcessError:
        return 0, 0
    parts = result.stdout.strip().split()
    if len(parts) != 2:
        return 0, 0
    return int(parts[0]), int(parts[1])


def is_ancestor(path: Path, maybe_ancestor: str, rev: str) -> bool:
    result = run_git(
        path, "merge-base", "--is-ancestor", maybe_ancestor, rev, check=False
    )
    return result.returncode == 0


def pick_self_ff_ref(
    path: Path, branch: str, remote_names: list[str]
) -> tuple[str | None, str]:
    """Self-update: ff to a remote tip only if HEAD is an ancestor of that tip."""
    if "origin" in remote_names:
        ahead, behind = remote_ahead_behind(path, branch, "origin")
        if ahead and behind:
            return None, "diverged"
    ff_refs: list[tuple[int, str]] = []
    for remote in remote_names:
        ahead, behind = remote_ahead_behind(path, branch, remote)
        if ahead and behind:
            continue
        if not behind:
            continue
        ref = f"{remote}/{branch}"
        if is_ancestor(path, "HEAD", ref):
            ff_refs.append((behind, ref))
    if ff_refs:
        ff_refs.sort(key=lambda item: item[0], reverse=True)
        return ff_refs[0][1], "behind"
    return None, "current"


def pick_sync_ref(path: Path, branch: str, remote_names: list[str]) -> tuple[str | None, str]:
    """Fast-forward target is origin (push/pull). Extra remotes are pin-fetch only."""
    remote = "origin" if "origin" in remote_names else (remote_names[0] if remote_names else "origin")
    ahead, behind = remote_ahead_behind(path, branch, remote)
    if ahead and behind:
        return None, "diverged"
    if behind:
        return f"{remote}/{branch}", "behind"
    return None, "current"


def default_sync_ref(branch: str, remote_names: list[str]) -> str:
    if "origin" in remote_names:
        return f"origin/{branch}"
    if remote_names:
        return f"{remote_names[0]}/{branch}"
    return f"origin/{branch}"


def current_branch(path: Path) -> str | None:
    try:
        result = run_git(path, "rev-parse", "--abbrev-ref", "HEAD")
    except subprocess.CalledProcessError:
        return None
    branch = result.stdout.strip()
    return None if branch == "HEAD" else branch


def current_commit(path: Path) -> str | None:
    try:
        result = run_git(path, "rev-parse", "HEAD")
    except subprocess.CalledProcessError:
        return None
    return result.stdout.strip()


def is_dirty(path: Path) -> bool:
    try:
        result = run_git(path, "status", "--porcelain")
    except subprocess.CalledProcessError:
        return True
    return bool(result.stdout.strip())


def ahead_behind(path: Path, branch: str) -> tuple[int, int]:
    run_git(path, "fetch", "origin", branch, check=False)
    return remote_ahead_behind(path, branch, "origin")


def classify_repo(catalog: Catalog, entry: RepoEntry, fetch: bool = False) -> str:
    path = repo_abs_path(catalog, entry)
    if not path.exists() or not (path / ".git").exists():
        return "missing"
    if is_dirty(path):
        return "dirty"
    head = current_commit(path)
    if not head:
        return "missing"
    if fetch:
        fetch_all_remotes(path, entry_fetch_urls(entry))
    names = pin_fetch_remote_names(path, entry)
    branch = current_branch(path) or entry.branch
    _ref, sync = pick_sync_ref(path, branch, names)
    origin_ahead, _origin_behind = remote_ahead_behind(path, branch, "origin")
    if head != entry.commit:
        if sync == "diverged":
            return "diverged"
        if sync == "behind":
            return "behind"
        if origin_ahead:
            return "ahead"
        return "unpinned"
    if sync == "diverged":
        return "diverged"
    if sync == "behind":
        return "behind"
    if origin_ahead:
        return "ahead"
    return "pinned"


def select_repos(catalog: Catalog, name: str | None) -> list[RepoEntry]:
    if name:
        entry = catalog.find(name)
        if not entry:
            raise SystemExit(f"Unknown repo: {name}")
        return [entry]
    return list(catalog.repos)


def catalog_entry_for_path(catalog: Catalog, path: Path) -> RepoEntry | None:
    resolved = path.resolve()
    for entry in catalog.repos:
        try:
            if repo_abs_path(catalog, entry).resolve() == resolved:
                return entry
        except OSError:
            continue
    return None


def log_hook_pin(message: str) -> None:
    ensure_config_dir()
    log_path = LOG_DIR / "hook-pin.log"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def resolve_git_updater_invoke() -> str:
    if shutil.which("git-updater"):
        return "git-updater"
    script = install_root() / "git_updater.py"
    if shutil.which("py"):
        return f'py -3 "{script}"'
    for name in ("python3", "python"):
        exe = shutil.which(name)
        if exe:
            return f'"{exe}" "{script}"'
    return "git-updater"


def pin_hook_script(hook_name: str, invoke: str) -> str:
    lines = [
        "#!/bin/sh",
        f"# {PIN_HOOK_MARKER}",
        'LOG="${HOME:-$USERPROFILE}/.git-updater/logs/hook-pin.log"',
        'mkdir -p "$(dirname "$LOG")" 2>/dev/null || true',
    ]
    if hook_name == "post-checkout":
        lines.append('if [ "$1" = "$2" ]; then exit 0; fi')
    lines.append(f'{invoke} pin --here --quiet 2>>"$LOG" || true')
    lines.append("")
    return "\n".join(lines)


def install_pin_hooks(repo_path: Path, *, force: bool = False) -> tuple[list[str], list[str]]:
    """Install git hooks that keep the catalog pin in sync with HEAD."""
    hooks_dir = repo_path / ".git" / "hooks"
    if not hooks_dir.is_dir():
        return [], [f"missing .git/hooks under {repo_path}"]
    invoke = resolve_git_updater_invoke()
    installed: list[str] = []
    skipped: list[str] = []
    for hook_name in PIN_HOOK_NAMES:
        hook_path = hooks_dir / hook_name
        content = pin_hook_script(hook_name, invoke)
        if hook_path.exists():
            existing = hook_path.read_text(encoding="utf-8", errors="replace")
            if PIN_HOOK_MARKER in existing:
                hook_path.write_text(content, encoding="utf-8", newline="\n")
                installed.append(hook_name)
                continue
            if force:
                hook_path.write_text(
                    existing.rstrip() + "\n\n" + content,
                    encoding="utf-8",
                    newline="\n",
                )
                installed.append(hook_name)
                continue
            skipped.append(f"{hook_name} (foreign hook)")
            continue
        hook_path.write_text(content, encoding="utf-8", newline="\n")
        installed.append(hook_name)
        try:
            hook_path.chmod(hook_path.stat().st_mode | 0o111)
        except OSError:
            pass
    return installed, skipped


def sync_pin_hooks(repo_path: Path, repo_name: str, *, force: bool = False) -> None:
    installed, skipped = install_pin_hooks(repo_path, force=force)
    if installed:
        print(f"  {repo_name}: installed {', '.join(installed)}")
    for reason in skipped:
        print(f"  {repo_name}: skip {reason}")


def pin_entry(
    catalog: Catalog, entry: RepoEntry, *, quiet: bool = False
) -> bool:
    """Pin catalog entry to HEAD. Returns True when commit changed."""
    path = repo_abs_path(catalog, entry)
    if not path.exists():
        message = f"Missing: {entry.name} ({path})"
        if quiet:
            log_hook_pin(message)
            return False
        raise SystemExit(message)
    commit = current_commit(path)
    if not commit:
        message = f"Not a git repo: {path}"
        if quiet:
            log_hook_pin(message)
            return False
        raise SystemExit(message)
    branch = current_branch(path)
    if branch:
        entry.branch = branch
    old = entry.commit
    changed = old != commit
    entry.commit = commit
    if quiet:
        if changed:
            log_hook_pin(f"pinned {entry.name}: {old[:7]} -> {commit[:7]}")
    else:
        print(f"  pinned {entry.name}: {old[:7]} -> {commit[:7]}")
    return changed


def unique_name(catalog: Catalog, base: str) -> str:
    if not catalog.find(base):
        return base
    i = 2
    while catalog.find(f"{base}-{i}"):
        i += 1
    return f"{base}-{i}"


def discover_git_dirs(root: Path) -> list[Path]:
    found: list[Path] = []
    if not root.exists():
        return found
    for dirpath, dirnames, _ in os.walk(root):
        if ".git" in dirnames:
            found.append(Path(dirpath))
            dirnames[:] = [d for d in dirnames if d != ".git"]
    return sorted(found)


def catalog_paths(catalog: Catalog) -> set[str]:
    root = Path(catalog.root).resolve()
    return {(root / r.path).resolve().as_posix() for r in catalog.repos}


def lock_from_catalog(catalog: Catalog) -> dict[str, Any]:
    """Portable snapshot: relative paths only. Clone root is chosen at replicate time."""
    return {
        "version": LOCK_VERSION,
        "repos": [r.to_dict() for r in catalog.repos],
    }


def resolve_lock_root(lock_path: Path, data: dict[str, Any], override: str | None) -> Path:
    """Pick clone root for this machine. Never reuse another computer's absolute path."""
    if override:
        return Path(override).expanduser().resolve()
    hint = data.get("root")
    if hint:
        hinted = Path(str(hint).replace("\\", "/"))
        if not hinted.is_absolute() and not (len(str(hint)) >= 2 and str(hint)[1] == ":"):
            return (lock_path.parent / hinted).resolve()
    return lock_path.parent.resolve()


def load_lock(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != LOCK_VERSION:
        raise SystemExit(f"Unsupported vendor.lock version: {data.get('version')}")
    return data


def save_lock(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def render_vendor_md(catalog: Catalog, exported_at: str | None = None) -> str:
    ts = exported_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Vendor log",
        "",
        "Clone root is chosen at replicate time (`git-updater replicate --root <path>`).",
        f"Exported: {ts}",
        "",
        "| Name | Remote | Branch | Commit | Install |",
        "|------|--------|--------|--------|---------|",
    ]
    for repo in sorted(catalog.repos, key=lambda r: r.name):
        short = repo.commit[:7] if len(repo.commit) >= 7 else repo.commit
        install = repo.install or "-"
        remote_cell = repo.remote
        if repo.remote.count("/") == 1 and not repo.remote.startswith("local:"):
            remote_cell = f"[{repo.remote}](https://github.com/{repo.remote})"
        elif repo.remote.startswith("local:"):
            remote_cell = f"`{repo.remote.removeprefix('local:')}`"
        lines.append(
            f"| {repo.name} | {remote_cell} "
            f"| {repo.branch} | `{short}` | {install} |"
        )
    lines.append("")
    return "\n".join(lines)


def export_lock(catalog: Catalog, out: Path) -> None:
    save_lock(out, lock_from_catalog(catalog))
    md_path = out.parent / "VENDOR.md"
    md_path.write_text(render_vendor_md(catalog), encoding="utf-8")
    print(f"Wrote {out}")
    print(f"Wrote {md_path}")


@dataclass
class RepoManifest:
    install: str | None = None
    update: str | None = None
    verify: str | None = None
    source: str = ""


def commands_to_shell(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return " && ".join(parts) if parts else None
    if isinstance(value, dict):
        run = value.get("run") or value.get("script")
        if not run:
            return None
        shell = value.get("shell")
        run = str(run).strip()
        if shell:
            return f"{shell} {run}"
        return run
    return None


def parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse a tiny YAML subset: scalars, inline lists, and `-` list blocks."""
    root: dict[str, Any] = {}
    current_key: str | None = None
    list_items: list[str] = []

    def flush_list() -> None:
        nonlocal current_key, list_items
        if current_key is not None and list_items:
            root[current_key] = list_items
            list_items = []
            current_key = None

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and current_key is not None:
            list_items.append(stripped[2:].strip().strip('"').strip("'"))
            continue
        flush_list()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not value:
            current_key = key
            list_items = []
            continue
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if not inner:
                root[key] = []
            else:
                root[key] = [
                    part.strip().strip('"').strip("'")
                    for part in inner.split(",")
                    if part.strip()
                ]
        else:
            root[key] = value.strip('"').strip("'")
    flush_list()
    return root


def load_manifest_file(path: Path) -> RepoManifest | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if path.suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
    else:
        data = parse_simple_yaml(text)
    if not isinstance(data, dict):
        return None
    install = commands_to_shell(data.get("install"))
    update = commands_to_shell(data.get("update")) or install
    verify = commands_to_shell(data.get("verify"))
    if not any((install, update, verify)):
        return None
    return RepoManifest(
        install=install,
        update=update,
        verify=verify,
        source=path.name,
    )


def read_repo_manifest(repo_path: Path) -> RepoManifest | None:
    for name in MANIFEST_FILENAMES:
        manifest_path = repo_path / name
        if manifest_path.is_file():
            manifest = load_manifest_file(manifest_path)
            if manifest:
                return manifest
    return None


def detect_install_heuristic(repo_path: Path) -> RepoManifest | None:
    makefile = repo_path / "Makefile"
    if makefile.is_file():
        try:
            content = makefile.read_text(encoding="utf-8", errors="replace")
        except OSError:
            content = ""
        if re.search(r"^install\s*:", content, re.MULTILINE):
            return RepoManifest(install="make install", update="make install", source="Makefile")

    package_json = repo_path / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        scripts = data.get("scripts", {})
        if "install" in scripts:
            cmd = "npm ci" if (repo_path / "package-lock.json").exists() else "npm install"
            return RepoManifest(install=cmd, update=cmd, source="package.json")
        if "postinstall" in scripts:
            cmd = "npm run postinstall"
            return RepoManifest(install=cmd, update=cmd, source="package.json")

    pyproject = repo_path / "pyproject.toml"
    if pyproject.is_file() and (repo_path / "uv.lock").exists():
        return RepoManifest(install="uv sync", update="uv sync", source="pyproject.toml")
    if (repo_path / "requirements.txt").is_file():
        return RepoManifest(
            install="python -m pip install -r requirements.txt",
            update="python -m pip install -r requirements.txt",
            source="requirements.txt",
        )

    composer = repo_path / "composer.json"
    if composer.is_file():
        return RepoManifest(install="composer install", update="composer install", source="composer.json")

    go_mod = repo_path / "go.mod"
    if go_mod.is_file():
        return RepoManifest(install="go mod download", update="go mod download", source="go.mod")

    return None


def discover_repo_hooks(repo_path: Path) -> RepoManifest | None:
    manifest = read_repo_manifest(repo_path)
    if manifest:
        return manifest
    return detect_install_heuristic(repo_path)


def apply_manifest_to_entry(entry: RepoEntry, manifest: RepoManifest) -> None:
    if manifest.install:
        entry.install = manifest.install
    if manifest.update:
        entry.update = manifest.update


def resolve_hook_command(
    entry: RepoEntry,
    repo_path: Path,
    phase: str,
) -> tuple[str | None, str]:
    """Return (command, origin) where origin describes where the hook came from."""
    if phase == "update":
        if entry.update:
            return entry.update, "catalog"
        if entry.install:
            return entry.install, "catalog-install"
    elif entry.install:
        return entry.install, "catalog"

    manifest = discover_repo_hooks(repo_path)
    if manifest:
        if phase == "update":
            cmd = manifest.update or manifest.install
        else:
            cmd = manifest.install
        if cmd:
            return cmd, manifest.source or "manifest"

    return None, ""


def run_hook(entry: RepoEntry, path: Path, phase: str = "install") -> None:
    command, origin = resolve_hook_command(entry, path, phase)
    if not command:
        return
    label = "update" if phase == "update" else "install"
    origin_note = f" ({origin})" if origin else ""
    print(f"  {label}: {entry.name}{origin_note}")
    result = subprocess.run(
        command,
        shell=True,
        cwd=path,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"{label.capitalize()} failed for {entry.name} (exit {result.returncode})"
        )


def run_install(entry: RepoEntry, path: Path) -> None:
    run_hook(entry, path, phase="install")


def run_update_hook(entry: RepoEntry, path: Path) -> None:
    run_hook(entry, path, phase="update")


def clone_repo(url: str, dest: Path, branch: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise SystemExit(f"Path already exists: {dest}")
    clone_url = normalize_clone_url(url)
    print(f"  clone: {clone_url} -> {dest}")
    try:
        run_git(None, "clone", "--branch", branch, clone_url, str(dest))
    except subprocess.CalledProcessError:
        if is_local_remote(clone_url):
            run_git(None, "clone", clone_url, str(dest))
        else:
            raise


def checkout_pin(
    path: Path,
    commit: str,
    extra_urls: list[str] | None = None,
) -> None:
    if not commit_exists(path, commit):
        fetch_commit(path, commit, extra_urls)
    if not commit_exists(path, commit):
        raise SystemExit(
            f"Commit {commit[:7]} not found in {path}. "
            "The pin is that SHA. Fetched origin plus lock/catalog url and mirrors. "
            "Add a remote that actually has this commit (git remote add <name> <url>)."
        )
    try:
        run_git(path, "checkout", "--detach", commit)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"checkout --detach {commit[:7]} failed in {path}") from exc


def can_use_github_api(remote_id: str, url: str | None = None) -> bool:
    if remote_id.startswith("local:"):
        return False
    if url and parse_github_remote(url):
        return True
    parts = remote_id.split("/")
    return len(parts) == 2 and "." not in parts[0]


def merge_in_progress(path: Path) -> bool:
    return (path / ".git" / "MERGE_HEAD").exists()


def rebase_in_progress(path: Path) -> bool:
    git_dir = path / ".git"
    return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()


def list_conflicts(path: Path) -> list[str]:
    try:
        result = run_git(path, "diff", "--name-only", "--diff-filter=U")
    except subprocess.CalledProcessError:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def consolidate_repo(
    catalog: Catalog,
    entry: RepoEntry,
    *,
    strategy: str,
    mode: str,
) -> tuple[str, list[str]]:
    """Return (outcome, log_lines). outcome: ok|skipped|conflict|error"""
    path = repo_abs_path(catalog, entry)
    logs: list[str] = []
    if not path.exists() or not (path / ".git").exists():
        print(f"  missing - run install")
        return "error", [f"missing {entry.name}"]

    if mode == "abort":
        if merge_in_progress(path):
            run_git(path, "merge", "--abort", check=False)
            print(f"  aborted merge")
            return "ok", [f"aborted merge {entry.name}"]
        if rebase_in_progress(path):
            run_git(path, "rebase", "--abort", check=False)
            print(f"  aborted rebase")
            return "ok", [f"aborted rebase {entry.name}"]
        print(f"  no merge/rebase in progress")
        return "skipped", []

    if mode == "continue":
        if merge_in_progress(path):
            run_git(path, "merge", "--continue", check=False)
        elif rebase_in_progress(path):
            run_git(path, "rebase", "--continue", check=False)
        else:
            print(f"  no merge/rebase in progress")
            return "skipped", []
        conflicts = list_conflicts(path)
        if conflicts or merge_in_progress(path) or rebase_in_progress(path):
            print(f"  still has conflicts ({len(conflicts)} file(s))")
            for f in conflicts[:10]:
                print(f"    {f}")
            return "conflict", [f"conflict {entry.name}"]
        head = current_commit(path)
        if head:
            entry.commit = head
            print(f"  consolidated @ {head[:7]}")
        return "ok", [f"continued {entry.name}"]

    # default: pull / merge / rebase
    if merge_in_progress(path) or rebase_in_progress(path):
        conflicts = list_conflicts(path)
        print(f"  merge/rebase already in progress ({len(conflicts)} conflict(s))")
        for f in conflicts[:10]:
            print(f"    {f}")
        print(f"  fix files, then: git-updater consolidate --continue {entry.name}")
        return "conflict", [f"in-progress {entry.name}"]

    if is_dirty(path):
        print(f"  skipped (dirty working tree)")
        return "skipped", [f"skip dirty {entry.name}"]

    branch = current_branch(path) or entry.branch
    attach_entry_remotes(path, entry)
    fetch_all_remotes(path, entry_fetch_urls(entry))
    names = pin_fetch_remote_names(path, entry)
    ref, sync = pick_sync_ref(path, branch, names)
    merge_ref = ref or default_sync_ref(branch, names)
    old_commit = entry.commit

    if sync != "behind":
        head = current_commit(path)
        if head and head != entry.commit:
            entry.commit = head
        if sync == "diverged":
            print(f"  diverged - trying {strategy} onto {merge_ref}")
        else:
            print(f"  up to date")
            return "ok", []

    if sync == "behind" and ref:
        try:
            run_git(path, "merge", "--ff-only", ref)
            new_commit = current_commit(path)
            if new_commit:
                entry.commit = new_commit
                print(f"  fast-forward {old_commit[:7]} -> {new_commit[:7]} ({ref})")
                if new_commit and new_commit != old_commit and resolve_hook_command(
                    entry, path, "update"
                )[0]:
                    run_update_hook(entry, path)
            return "ok", [f"ff {entry.name}"]
        except subprocess.CalledProcessError:
            print(f"  fast-forward failed - trying {strategy}")

    try:
        if strategy == "rebase":
            run_git(path, "rebase", merge_ref)
        else:
            run_git(path, "merge", merge_ref)
    except subprocess.CalledProcessError:
        conflicts = list_conflicts(path)
        print(f"  conflict ({len(conflicts)} file(s))")
        for f in conflicts[:10]:
            print(f"    {f}")
        print(f"  fix files, then: git-updater consolidate --continue {entry.name}")
        print(f"  or abort: git-updater consolidate --abort {entry.name}")
        return "conflict", [f"conflict {entry.name}"]

    new_commit = current_commit(path)
    if new_commit:
        entry.commit = new_commit
        print(f"  consolidated {old_commit[:7]} -> {new_commit[:7]}")
        if new_commit and new_commit != old_commit and resolve_hook_command(
            entry, path, "update"
        )[0]:
            run_update_hook(entry, path)
    return "ok", [f"consolidated {entry.name}"]


def cmd_init(args: argparse.Namespace) -> None:
    ensure_config_dir()
    root_path = infer_clone_root(args.root)
    root = normalize_path(root_path)
    if CATALOG_PATH.exists() and not args.force:
        raise SystemExit(f"Catalog already exists at {CATALOG_PATH} (use --force)")
    catalog = Catalog(version=CATALOG_VERSION, root=root, repos=[])
    save_catalog(catalog)
    print(f"Initialized catalog at {CATALOG_PATH}")
    print(f"Clone root: {root}")

    if not args.no_pip:
        pip_editable_self()

    for path in install_user_skills():
        print(f"Wrote {path}")

    catalog = load_catalog()
    adopt_if_needed(catalog, install_root())

    lock_path: Path | None = None
    if args.lock:
        lock_path = Path(args.lock).expanduser()
    elif not args.no_lock:
        lock_path = default_stack_lock()
    if lock_path and lock_path.is_file():
        catalog = load_catalog()
        print(f"-> replicate {lock_path}")
        log_lines = replicate_lockfile(
            lock_path,
            Path(catalog.root),
            dry_run=False,
            update_existing=False,
        )
        if log_lines:
            log_path = write_log("init", log_lines)
            print(f"Log: {log_path}")
        catalog = load_catalog()
        data = load_lock(lock_path)
        for raw in data.get("repos", []):
            entry = RepoEntry.from_dict(raw)
            dest = Path(catalog.root) / entry.path
            adopt_if_needed(catalog, dest, name=entry.name)
            catalog = load_catalog()
    elif args.lock:
        raise SystemExit(f"Lock not found: {lock_path}")


def cmd_consolidate(args: argparse.Namespace) -> None:
    catalog = load_catalog()
    repos = select_repos(catalog, args.name)
    mode = "default"
    if args.continue_:
        mode = "continue"
    elif args.abort:
        mode = "abort"
    strategy = "rebase" if args.rebase else "merge"
    log_lines: list[str] = []
    conflicts = 0
    for entry in repos:
        print(f"-> {entry.name}")
        outcome, lines = consolidate_repo(
            catalog,
            entry,
            strategy=strategy,
            mode=mode,
        )
        log_lines.extend(lines)
        if outcome == "conflict":
            conflicts += 1
    save_catalog(catalog)
    if log_lines:
        log_path = write_log("consolidate", log_lines)
        print(f"Log: {log_path}")
    if conflicts:
        raise SystemExit(f"{conflicts} repo(s) need manual conflict resolution")


def cmd_scan(args: argparse.Namespace) -> None:
    catalog = load_catalog()
    root = Path(catalog.root)
    known = catalog_paths(catalog)
    unknown: list[Path] = []
    for git_dir in discover_git_dirs(root):
        if git_dir.resolve().as_posix() not in known:
            unknown.append(git_dir)
    if not unknown:
        print("No unscanned git repos under root.")
        return
    print(f"Git repos not in catalog ({len(unknown)}):")
    for path in unknown:
        rel = path.relative_to(root) if path.is_relative_to(root) else path
        origin = read_origin(path)
        slug = origin[0] if origin else "?"
        extra = ""
        if origin:
            commit = current_commit(path)
            aliases = mirrors_from_clone(path, origin[1], commit)
            if aliases:
                extra = " (+" + ", ".join(remote_id_from_url(url) for url in aliases) + ")"
        manifest = discover_repo_hooks(path)
        hook = f" [{manifest.source}]" if manifest else ""
        print(f"  {rel}\t{slug}{extra}{hook}")


def cmd_add(args: argparse.Namespace) -> None:
    catalog = load_catalog()
    try:
        default_name, remote_id, url = parse_repo_spec(args.repo)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    name = unique_name(catalog, args.name or default_name)
    path = args.path or name
    dest = Path(catalog.root) / path
    branch = args.branch or "main"

    if dest.exists():
        raise SystemExit(f"Path already exists: {dest} (use adopt instead)")

    clone_repo(url, dest, branch)
    commit = current_commit(dest)
    if not commit:
        raise SystemExit(f"Clone failed: {dest}")

    entry = RepoEntry(
        name=name,
        remote=remote_id,
        url=url,
        path=path.replace("\\", "/"),
        branch=branch,
        commit=commit,
    )
    if args.install:
        entry.install = args.install
    else:
        manifest = discover_repo_hooks(dest)
        if manifest:
            apply_manifest_to_entry(entry, manifest)
            print(f"  hooks from {manifest.source}")
    catalog.repos.append(entry)
    save_catalog(catalog)
    if resolve_hook_command(entry, dest, "install")[0]:
        run_install(entry, dest)
    sync_pin_hooks(dest, entry.name)
    print(f"Added {name} @ {commit[:7]}")


def entry_from_clone(
    catalog: Catalog,
    path: Path,
    *,
    name: str | None = None,
    branch: str | None = None,
    install: str | None = None,
) -> RepoEntry:
    path = path.resolve()
    root = Path(catalog.root).resolve()
    if not path.is_relative_to(root):
        raise SystemExit(f"Path must be under clone root {root}")
    origin = read_origin(path)
    remotes = list_remotes(path)
    if not origin:
        if remotes:
            origin = (remotes[0][2], remotes[0][1])
        else:
            raise SystemExit(f"Not a git repo with remotes: {path}")
    remote_id, url = origin
    resolved_branch = current_branch(path) or branch or "main"
    commit = current_commit(path)
    if not commit:
        raise SystemExit(f"Could not read HEAD: {path}")
    for remote_name, _url, _rid in remotes:
        run_git(path, "fetch", remote_name, check=False)
    mirrors = mirrors_from_clone(path, url, commit)
    rel = path.relative_to(root).as_posix()
    chosen = name or unique_name(catalog, path.name)
    entry = RepoEntry(
        name=chosen,
        remote=remote_id,
        url=url,
        path=rel,
        branch=resolved_branch,
        commit=commit,
        mirrors=mirrors,
    )
    if install:
        entry.install = install
    else:
        manifest = discover_repo_hooks(path)
        if manifest:
            apply_manifest_to_entry(entry, manifest)
            print(f"  hooks from {manifest.source}")
    return entry


def adopt_if_needed(
    catalog: Catalog,
    path: Path,
    *,
    name: str | None = None,
) -> RepoEntry | None:
    path = Path(path).resolve()
    root = Path(catalog.root).resolve()
    if not path.exists():
        return None
    if not path.is_relative_to(root):
        print(f"  skip adopt {path.name}: outside clone root {root}")
        return None
    for existing in catalog.repos:
        if (root / existing.path).resolve() == path:
            return existing
    try:
        entry = entry_from_clone(catalog, path, name=name)
    except SystemExit as exc:
        print(f"  skip adopt {path.name}: {exc}")
        return None
    if catalog.find(entry.name):
        print(f"  skip adopt {entry.name}: name already in catalog")
        return None
    catalog.repos.append(entry)
    save_catalog(catalog)
    print(f"Adopted {entry.name} @ {entry.commit[:7]} ({entry.remote})")
    sync_pin_hooks(path, entry.name)
    return entry


def cmd_adopt(args: argparse.Namespace) -> None:
    catalog = load_catalog()
    folder = args.folder
    path = Path(folder)
    if not path.is_absolute():
        path = Path(catalog.root) / folder
    path = path.resolve()
    name = args.name or unique_name(catalog, path.name)
    if catalog.find(name):
        raise SystemExit(f"Name already in catalog: {name}")
    entry = entry_from_clone(
        catalog,
        path,
        name=name,
        branch=args.branch,
        install=args.install,
    )
    catalog.repos.append(entry)
    save_catalog(catalog)
    print(f"Adopted {entry.name} @ {entry.commit[:7]} ({entry.remote})")
    if entry.mirrors:
        print("  fetch remotes: " + ", ".join(remote_id_from_url(m) for m in entry.mirrors))
    sync_pin_hooks(path, entry.name)


def cmd_sync_hooks(args: argparse.Namespace) -> None:
    catalog = load_catalog()
    repos = select_repos(catalog, args.name)
    changed = 0
    for entry in repos:
        path = repo_abs_path(catalog, entry)
        if not path.exists():
            print(f"  skip {entry.name}: missing")
            continue
        manifest = discover_repo_hooks(path)
        if not manifest:
            print(f"  skip {entry.name}: no manifest/heuristic")
            continue
        before = (entry.install, entry.update)
        apply_manifest_to_entry(entry, manifest)
        after = (entry.install, entry.update)
        if after != before:
            changed += 1
            print(f"  synced {entry.name} from {manifest.source}")
            if entry.install:
                print(f"    install: {entry.install}")
            if entry.update and entry.update != entry.install:
                print(f"    update:  {entry.update}")
        else:
            print(f"  unchanged {entry.name} ({manifest.source})")
    save_catalog(catalog)
    print(f"Synced {changed} repo(s)")


def cmd_rm(args: argparse.Namespace) -> None:
    catalog = load_catalog()
    if not catalog.remove(args.name):
        raise SystemExit(f"Unknown repo: {args.name}")
    save_catalog(catalog)
    print(f"Removed {args.name} from catalog (folder untouched)")


def print_status_table(rows: list[tuple[str, str, str, str]]) -> None:
    name_w = max(len("Name"), *(len(r[0]) for r in rows)) if rows else 4
    stat_w = max(len("Status"), *(len(r[1]) for r in rows)) if rows else 6
    print(f"{'Name'.ljust(name_w)}  {'Status'.ljust(stat_w)}  Commit   Remote")
    print(f"{'-' * name_w}  {'-' * stat_w}  -------  ------")
    for name, status, short, remote in rows:
        print(f"{name.ljust(name_w)}  {status.ljust(stat_w)}  {short}  {remote}")


def cmd_status(args: argparse.Namespace) -> None:
    catalog = load_catalog()
    repos = select_repos(catalog, args.name)
    rows: list[tuple[str, str, str, str]] = []
    for entry in repos:
        status = classify_repo(catalog, entry, fetch=args.fetch)
        short = entry.commit[:7]
        rows.append((entry.name, status, short, entry.remote))
    print_status_table(rows)


def cmd_pin(args: argparse.Namespace) -> None:
    catalog = load_catalog()
    if args.here:
        if args.name:
            raise SystemExit("Use either NAME or --here, not both")
        entry = catalog_entry_for_path(catalog, Path.cwd())
        if not entry:
            message = f"No catalog entry for {Path.cwd()}"
            if args.quiet:
                log_hook_pin(message)
                return
            raise SystemExit(message)
        pin_entry(catalog, entry, quiet=args.quiet)
        save_catalog(catalog)
        if args.export:
            out = Path(catalog.root) / "vendor.lock"
            export_lock(catalog, out)
        return
    repos = select_repos(catalog, args.name)
    for entry in repos:
        pin_entry(catalog, entry, quiet=False)
    save_catalog(catalog)
    if args.export:
        out = Path(catalog.root) / "vendor.lock"
        export_lock(catalog, out)


def cmd_hook_sync(args: argparse.Namespace) -> None:
    catalog = load_catalog()
    repos = select_repos(catalog, args.name)
    for entry in repos:
        path = repo_abs_path(catalog, entry)
        if not path.exists():
            print(f"  skip {entry.name}: missing")
            continue
        sync_pin_hooks(path, entry.name, force=args.force)


def cmd_install(args: argparse.Namespace) -> None:
    catalog = load_catalog()
    repos = select_repos(catalog, args.name)
    log_lines: list[str] = []
    for entry in repos:
        path = repo_abs_path(catalog, entry)
        if not path.exists() or not (path / ".git").exists():
            clone_repo(entry.url, path, entry.branch)
            checkout_pin(path, entry.commit, entry_fetch_urls(entry))
            log_lines.append(f"cloned {entry.name}")
        else:
            attach_entry_remotes(path, entry)
            head = current_commit(path)
            if head != entry.commit:
                checkout_pin(path, entry.commit, entry_fetch_urls(entry))
                log_lines.append(f"checked out {entry.name} @ {entry.commit[:7]}")
        if resolve_hook_command(entry, path, "install")[0]:
            run_install(entry, path)
            log_lines.append(f"installed {entry.name}")
    if log_lines:
        log_path = write_log("install", log_lines)
        print(f"Log: {log_path}")


def cmd_update(args: argparse.Namespace) -> None:
    catalog = load_catalog()
    repos = select_repos(catalog, args.name)
    log_lines: list[str] = []
    for entry in repos:
        path = repo_abs_path(catalog, entry)
        if should_defer_self(args, catalog, entry):
            print(f"-> {entry.name} (self-update at end)")
            continue
        print(f"-> {entry.name}")
        if not path.exists():
            print(f"  missing - run install")
            continue
        if is_dirty(path):
            print(f"  skipped (dirty working tree)")
            log_lines.append(f"skip dirty {entry.name}")
            continue
        branch = current_branch(path) or entry.branch
        attach_entry_remotes(path, entry)
        fetch_all_remotes(path, entry_fetch_urls(entry))
        names = pin_fetch_remote_names(path, entry)
        ref, sync = pick_sync_ref(path, branch, names)
        old_commit = entry.commit
        if sync == "behind" and ref:
            run_git(path, "merge", "--ff-only", ref)
            new_commit = current_commit(path)
            if new_commit:
                entry.commit = new_commit
                print(f"  fast-forward {old_commit[:7]} -> {new_commit[:7]} ({ref})")
                log_lines.append(f"updated {entry.name} {old_commit[:7]}->{new_commit[:7]}")
                if new_commit and new_commit != old_commit and resolve_hook_command(
                    entry, path, "update"
                )[0]:
                    run_update_hook(entry, path)
        elif sync == "diverged":
            print(f"  diverged - manual merge required (consolidate)")
            log_lines.append(f"diverged {entry.name}")
        else:
            head = current_commit(path)
            if head and head != entry.commit:
                entry.commit = head
                print(f"  re-pinned to {head[:7]}")
            else:
                print(f"  up to date")
    save_catalog(catalog)
    if log_lines:
        log_path = write_log("update", log_lines)
        print(f"Log: {log_path}")


def cmd_push(args: argparse.Namespace) -> None:
    catalog = load_catalog()
    repos = select_repos(catalog, args.name)
    log_lines: list[str] = []
    for entry in repos:
        path = repo_abs_path(catalog, entry)
        print(f"-> {entry.name}")
        if not path.exists():
            print(f"  missing")
            continue
        if is_dirty(path):
            print(f"  skipped (dirty working tree)")
            continue
        branch = current_branch(path) or entry.branch
        run_git(path, "push", "origin", branch)
        head = current_commit(path)
        if head:
            entry.commit = head
            entry.branch = branch
            print(f"  pushed @ {head[:7]}")
            log_lines.append(f"pushed {entry.name} @ {head[:7]}")
    save_catalog(catalog)
    if log_lines:
        log_path = write_log("push", log_lines)
        print(f"Log: {log_path}")


def cmd_export(args: argparse.Namespace) -> None:
    catalog = load_catalog()
    out = Path(args.out) if args.out else Path(catalog.root) / "vendor.lock"
    export_lock(catalog, out)


def replicate_lockfile(
    lock_path: Path,
    root_path: Path,
    *,
    dry_run: bool = False,
    update_existing: bool = True,
) -> list[str]:
    data = load_lock(lock_path)
    log_lines: list[str] = []
    for raw in data.get("repos", []):
        entry = RepoEntry.from_dict(raw)
        dest = root_path / entry.path
        print(f"-> {entry.name}")

        if dry_run:
            action = "clone+checkout" if not dest.exists() else "checkout"
            print(f"  [dry-run] would {action} @ {entry.commit[:7]}")
            if entry.install:
                print(f"  [dry-run] would run install: {entry.install}")
            else:
                cmd, origin = resolve_hook_command(entry, dest, "install")
                if cmd:
                    print(f"  [dry-run] would run install ({origin}): {cmd}")
            continue

        cloned = False
        if not dest.exists():
            clone_repo(entry.url, dest, entry.branch)
            checkout_pin(dest, entry.commit, entry_fetch_urls(entry))
            cloned = True
            log_lines.append(f"cloned {entry.name}")
        elif update_existing:
            attach_entry_remotes(dest, entry)
            head = current_commit(dest)
            if head != entry.commit:
                checkout_pin(dest, entry.commit, entry_fetch_urls(entry))
                log_lines.append(f"checked out {entry.name} @ {entry.commit[:7]}")
            else:
                print(f"  already at {entry.commit[:7]}")
        else:
            attach_entry_remotes(dest, entry)
            print(f"  exists, leaving tree")

        if cloned or update_existing:
            if resolve_hook_command(entry, dest, "install")[0]:
                run_install(entry, dest)
                log_lines.append(f"installed {entry.name}")
    return log_lines


def cmd_replicate(args: argparse.Namespace) -> None:
    lock_path = Path(args.lockfile)
    data = load_lock(lock_path)
    root_path = resolve_lock_root(lock_path, data, args.root)
    print(f"Replicate root: {root_path}")
    log_lines = replicate_lockfile(
        lock_path,
        root_path,
        dry_run=args.dry_run,
        update_existing=True,
    )
    if log_lines:
        log_path = write_log("replicate", log_lines)
        print(f"Log: {log_path}")
    elif args.dry_run:
        print("Dry run complete.")


def cmd_self_check(args: argparse.Namespace) -> None:
    result = check_self(fetch=args.fetch, use_cache=not args.fetch)
    if args.json:
        payload = asdict(result)
        payload["install_path"] = normalize_path(result.install_path)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(format_self_check(result))
    if result.status in ("behind", "diverged", "dirty"):
        raise SystemExit(1)
    if result.status == "unknown":
        raise SystemExit(2)


def cmd_self_update(args: argparse.Namespace) -> None:
    apply_self_update(interactive=True)


def cmd_verify(args: argparse.Namespace) -> None:
    catalog = load_catalog()
    lock_path = Path(args.lock) if args.lock else Path(catalog.root) / "vendor.lock"
    if lock_path.exists():
        data = load_lock(lock_path)
        entries = [RepoEntry.from_dict(r) for r in data.get("repos", [])]
    else:
        entries = catalog.repos
    if args.root:
        root = Path(args.root).expanduser().resolve()
    else:
        root = Path(catalog.root)
        entries = catalog.repos

    failed = 0
    for entry in entries:
        path = root / entry.path
        if not path.exists():
            print(f"FAIL {entry.name}: missing ({path})")
            failed += 1
            continue
        head = current_commit(path)
        if head != entry.commit:
            short = entry.commit[:7]
            got = head[:7] if head else "?"
            print(f"FAIL {entry.name}: want {short}, got {got}")
            failed += 1
        else:
            print(f"OK   {entry.name} @ {entry.commit[:7]}")

    if failed:
        raise SystemExit(1)
    print(f"All {len(entries)} repos match lock.")


def package_version() -> str:
    try:
        from importlib.metadata import version

        return version("git-updater")
    except Exception:
        return "0.1.0"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


def _action_spec(action: argparse.Action) -> dict[str, Any] | None:
    if action.help is argparse.SUPPRESS:
        return None
    if action.option_strings and action.option_strings[0] in ("-h", "--help"):
        return None
    if action.dest in ("help", "func"):
        return None
    if isinstance(action, argparse._SubParsersAction):
        return None
    flags = list(action.option_strings)
    is_option = bool(flags)
    is_flag = action.nargs == 0 or isinstance(action, argparse._StoreTrueAction)
    spec: dict[str, Any] = {
        "name": action.metavar or action.dest,
        "dest": action.dest,
        "help": action.help or "",
        "required": bool(action.required),
    }
    if is_option:
        spec["flags"] = flags
        spec["kind"] = "flag" if is_flag else "option"
    else:
        spec["kind"] = "argument"
        if action.nargs is not None:
            spec["nargs"] = str(action.nargs)
    default = action.default
    if default is not argparse.SUPPRESS and default is not None:
        spec["default"] = _json_safe(default)
    if action.choices:
        spec["choices"] = [_json_safe(c) for c in action.choices]
    return spec


def cli_spec(parser: argparse.ArgumentParser | None = None) -> dict[str, Any]:
    """Machine-readable CLI description derived from argparse (source of truth)."""
    parser = parser or build_parser()
    options: list[dict[str, Any]] = []
    arguments: list[dict[str, Any]] = []
    commands: list[dict[str, Any]] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            helps = {
                choice.dest: (choice.help or "")
                for choice in getattr(action, "_choices_actions", [])
            }
            for name, sub in action.choices.items():
                commands.append(cli_spec_command(name, sub, helps.get(name, "")))
            continue
        item = _action_spec(action)
        if not item:
            continue
        if item["kind"] == "argument":
            arguments.append(item)
        else:
            options.append(item)
    return {
        "name": parser.prog,
        "version": package_version(),
        "description": parser.description or "",
        "usage": parser.format_usage().strip(),
        "options": options,
        "arguments": arguments,
        "commands": commands,
    }


def cli_spec_command(
    name: str, parser: argparse.ArgumentParser, help_text: str = ""
) -> dict[str, Any]:
    options: list[dict[str, Any]] = []
    arguments: list[dict[str, Any]] = []
    for action in parser._actions:
        item = _action_spec(action)
        if not item:
            continue
        if item["kind"] == "argument":
            arguments.append(item)
        else:
            options.append(item)
    return {
        "name": name,
        "description": (parser.description or help_text or "").strip(),
        "help": help_text or (parser.description or ""),
        "usage": parser.format_usage().strip(),
        "arguments": arguments,
        "options": options,
    }


def render_man(spec: dict[str, Any] | None = None) -> str:
    spec = spec or cli_spec()
    name = spec["name"]
    lines = [
        f'.TH {name.upper()} 1 "{datetime.now(timezone.utc).strftime("%B %Y")}" "{spec["version"]}" "User Commands"',
        ".SH NAME",
        f'{name} \\- {spec["description"]}',
        ".SH SYNOPSIS",
        ".B " + name,
        "[\\fIOPTIONS\\fR]",
        "\\fICOMMAND\\fR",
        "[\\fIARGS\\fR]",
        ".SH DESCRIPTION",
        spec["description"],
        "",
        "Machine-readable help:",
        ".P",
        f".B {name} --help-json",
        ".SH OPTIONS",
    ]
    for opt in spec.get("options", []):
        flags = ", ".join(opt.get("flags", []))
        lines.append(".TP")
        lines.append(f".B {flags}")
        extra = opt.get("help") or ""
        if "default" in opt:
            extra += f" (default: {opt['default']})"
        lines.append(extra or ".")
    lines.append(".SH COMMANDS")
    for cmd in spec.get("commands", []):
        lines.append(".TP")
        lines.append(f".B {cmd['name']}")
        lines.append(cmd.get("help") or cmd.get("description") or ".")
        for arg in cmd.get("arguments", []):
            lines.append(".br")
            req = "required" if arg.get("required") else "optional"
            lines.append(f"{arg['name']} ({req}) {arg.get('help') or ''}".strip())
        for opt in cmd.get("options", []):
            lines.append(".br")
            flags = ", ".join(opt.get("flags", []))
            lines.append(f"{flags}  {opt.get('help') or ''}".strip())
    lines.extend(
        [
            ".SH FILES",
            "~/.git-updater/catalog.json",
            ".br",
            "Machine-local catalog (clone root, pins).",
            ".br",
            "<clone-root>/vendor.lock",
            ".br",
            "Shareable SHA pins (relative paths only).",
            ".SH EXIT STATUS",
            "0 on success. Non-zero on error, verify drift, or self-check behind/dirty/diverged.",
            ".SH SEE ALSO",
            f"{name} --help, {name} --help-json, {name} man",
        ]
    )
    return "\n".join(lines) + "\n"


def emit_help_json(argv: list[str], parser: argparse.ArgumentParser) -> None:
    spec = cli_spec(parser)
    topic = None
    for token in argv:
        if token.startswith("-"):
            continue
        topic = token
        break
    if topic:
        match = next((c for c in spec["commands"] if c["name"] == topic), None)
        if match is None:
            raise SystemExit(f"unknown command: {topic}")
        payload: dict[str, Any] = {
            "name": spec["name"],
            "version": spec["version"],
            "command": match,
        }
    else:
        payload = spec
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def cmd_help(args: argparse.Namespace) -> None:
    parser = build_parser()
    spec = cli_spec(parser)
    if args.json:
        if args.topic:
            match = next((c for c in spec["commands"] if c["name"] == args.topic), None)
            if match is None:
                raise SystemExit(f"unknown command: {args.topic}")
            print(
                json.dumps(
                    {
                        "name": spec["name"],
                        "version": spec["version"],
                        "command": match,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            print(json.dumps(spec, indent=2, ensure_ascii=False))
        return
    if args.topic:
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction) and args.topic in action.choices:
                action.choices[args.topic].print_help()
                return
        raise SystemExit(f"unknown command: {args.topic}")
    parser.print_help()


def cmd_man(args: argparse.Namespace) -> None:
    text = render_man()
    if args.write:
        path = Path(args.write)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"Wrote {path}")
        return
    sys.stdout.write(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="git-updater",
        description="Track git repos, pin commits, export vendor.lock, replicate stacks.",
        epilog="Machine-readable: git-updater --help-json [COMMAND]. Man page: git-updater man.",
    )
    parser.add_argument(
        "--no-self-check",
        action="store_true",
        help="Skip residual self-update (24h observe + safe ff when behind)",
    )
    parser.add_argument(
        "--help-json",
        action="store_true",
        help="Print machine-readable CLI spec as JSON and exit",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "init",
        help="First-run setup: catalog, pip -e this clone, skills, adopt self, replicate shared.lock",
    )
    p.add_argument(
        "--root",
        default=".",
        help="Clone root (default: cwd; if cwd is this checkout, its parent)",
    )
    p.add_argument("--force", action="store_true", help="Overwrite existing catalog")
    p.add_argument(
        "--lock",
        help="Replicate this lock after init (default: examples/shared.lock if present)",
    )
    p.add_argument("--no-lock", action="store_true", help="Skip stack lock replicate")
    p.add_argument(
        "--no-pip",
        action="store_true",
        help="Skip pip install -e of this checkout",
    )
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("scan", help="List git repos under root not in catalog")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("add", help="Clone and register a repo")
    p.add_argument(
        "repo",
        help="owner/repo, any git URL (https/ssh), file:// path, or local folder",
    )
    p.add_argument("--name", help="Catalog name (default: repo name)")
    p.add_argument("--path", help="Relative path under root")
    p.add_argument("--branch", default="main", help="Branch to clone (default: main)")
    p.add_argument(
        "--install",
        help="Override install hook (default: read .git-updater.yaml from repo)",
    )
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("adopt", help="Register an existing clone")
    p.add_argument("folder", help="Folder name or path under root")
    p.add_argument("--name", help="Catalog name")
    p.add_argument("--branch", help="Override branch")
    p.add_argument(
        "--install",
        help="Override install hook (default: read .git-updater.yaml from repo)",
    )
    p.set_defaults(func=cmd_adopt)

    p = sub.add_parser(
        "sync-hooks",
        help="Refresh install/update hooks from each repo's .git-updater manifest",
    )
    p.add_argument("name", nargs="?", help="Single repo")
    p.set_defaults(func=cmd_sync_hooks)

    p = sub.add_parser("rm", help="Remove from catalog (keeps folder)")
    p.add_argument("name", help="Catalog name")
    p.set_defaults(func=cmd_rm)

    p = sub.add_parser("status", help="Show pin status")
    p.add_argument("name", nargs="?", help="Single repo")
    p.add_argument("--fetch", action="store_true", help="Fetch before comparing remote")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("pin", help="Pin catalog to current HEAD")
    p.add_argument("name", nargs="?", help="Single repo")
    p.add_argument(
        "--here",
        action="store_true",
        help="Pin the catalog entry for the current directory (for git hooks)",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="No stdout; failures log to ~/.git-updater/logs/hook-pin.log",
    )
    p.add_argument("--export", action="store_true", help="Also write vendor.lock")
    p.set_defaults(func=cmd_pin)

    p = sub.add_parser(
        "hook-sync",
        help="Install git hooks that run pin --here after commit/pull/rebase/checkout",
    )
    p.add_argument("name", nargs="?", help="Single repo")
    p.add_argument(
        "--force",
        action="store_true",
        help="Append pin hooks even when a foreign hook file exists",
    )
    p.set_defaults(func=cmd_hook_sync)

    p = sub.add_parser("install", help="Clone missing repos and run install hooks")
    p.add_argument("name", nargs="?", help="Single repo")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("update", help="Fetch and fast-forward clean repos")
    p.add_argument("name", nargs="?", help="Single repo")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser(
        "consolidate",
        help="Fetch and merge/rebase when update would fail; resolve conflicts stepwise",
    )
    p.add_argument("name", nargs="?", help="Single repo")
    p.add_argument(
        "--rebase",
        action="store_true",
        help="Use rebase instead of merge when not fast-forwardable",
    )
    p.add_argument(
        "--continue",
        dest="continue_",
        action="store_true",
        help="Continue after you fixed merge/rebase conflicts",
    )
    p.add_argument(
        "--abort",
        action="store_true",
        help="Abort an in-progress merge or rebase",
    )
    p.set_defaults(func=cmd_consolidate)

    p = sub.add_parser("push", help="Push tracking branch")
    p.add_argument("name", nargs="?", help="Single repo")
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("export", help="Write vendor.lock and VENDOR.md")
    p.add_argument("--out", help="Lock file path (default: <root>/vendor.lock)")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("replicate", help="Bootstrap stack from vendor.lock")
    p.add_argument("lockfile", help="Path to vendor.lock")
    p.add_argument(
        "--root",
        help="Clone root on this machine (default: directory that contains the lockfile)",
    )
    p.add_argument("--dry-run", action="store_true", help="Print actions without cloning")
    p.set_defaults(func=cmd_replicate)

    p = sub.add_parser("verify", help="Check clones match lock (exit 1 on drift)")
    p.add_argument("--lock", help="Lock file (default: <root>/vendor.lock)")
    p.add_argument("--root", help="Override root")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("self-check", help="Check whether git-updater itself is up to date")
    p.add_argument(
        "--fetch",
        action="store_true",
        help="Fetch from origin / GitHub before comparing (bypasses 24h cache)",
    )
    p.add_argument("--json", action="store_true", help="Also print machine-readable result")
    p.set_defaults(func=cmd_self_check)

    p = sub.add_parser(
        "self-update",
        help="Fast-forward git-updater from descendant remotes and reinstall",
    )
    p.set_defaults(func=cmd_self_update)

    p = sub.add_parser(
        "install-skills",
        help="Copy the git-updater agent skill to ~/.cursor/skills and ~/.agents/skills",
    )
    p.set_defaults(func=cmd_install_skills)

    p = sub.add_parser("help", help="Show help; --json for the machine-readable spec")
    p.add_argument("topic", nargs="?", help="Command name")
    p.add_argument("--json", action="store_true", help="Print CLI spec as JSON")
    p.set_defaults(func=cmd_help)

    p = sub.add_parser("man", help="Print man page (roff) to stdout")
    p.add_argument("--write", help="Write roff to this file instead of stdout")
    p.set_defaults(func=cmd_man)

    return parser


def main(argv: list[str] | None = None) -> None:
    argv_list = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if "--help-json" in argv_list:
        emit_help_json([t for t in argv_list if t != "--help-json"], parser)
        return
    args = parser.parse_args(argv_list)
    args.func(args)
    if args.no_self_check or args.command in SKIP_SELF_CHECK_COMMANDS:
        return
    maybe_apply_self_update()


if __name__ == "__main__":
    main()
