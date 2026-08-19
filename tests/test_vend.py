"""Tests for vend."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import vend as gu


class ParseRepoSpecTests(unittest.TestCase):
    def test_owner_repo(self) -> None:
        name, slug, url = gu.parse_repo_spec("acme/example-app")
        self.assertEqual(name, "example-app")
        self.assertEqual(slug, "acme/example-app")
        self.assertEqual(url, "https://github.com/acme/example-app.git")

    def test_https_url(self) -> None:
        _, slug, url = gu.parse_repo_spec("https://github.com/acme/example-client.git")
        self.assertEqual(slug, "acme/example-client")
        self.assertIn("github.com", url)

    def test_gitlab_url(self) -> None:
        _, slug, url = gu.parse_repo_spec("https://gitlab.com/group/subgroup/project.git")
        self.assertEqual(slug, "gitlab.com/group/subgroup/project")
        self.assertIn("gitlab.com", url)

    def test_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, slug, url = gu.parse_repo_spec(tmp)
            self.assertTrue(slug.startswith("local:"))
            self.assertEqual(url, Path(tmp).resolve().as_posix())

    def test_ssh_url(self) -> None:
        parsed = gu.parse_github_remote("git@github.com:acme/example-app.git")
        self.assertEqual(parsed, ("acme", "example-app"))
        rid = gu.remote_id_from_url("git@gitlab.com:group/project.git")
        self.assertEqual(rid, "gitlab.com/group/project")

    def test_invalid(self) -> None:
        with self.assertRaises(ValueError):
            gu.parse_repo_spec("not-a-valid-spec")


class LockRoundTripTests(unittest.TestCase):
    def test_catalog_to_lock_and_back(self) -> None:
        catalog = gu.Catalog(
            version=1,
            root="C:/Users/test/repos",
            repos=[
                gu.RepoEntry(
                    name="demo",
                    remote="acme/demo",
                    url="https://github.com/acme/demo.git",
                    path="demo",
                    branch="main",
                    commit="abc123def456",
                    install="echo hi",
                )
            ],
        )
        lock = gu.lock_from_catalog(catalog)
        self.assertEqual(lock["version"], 1)
        self.assertNotIn("root", lock)
        self.assertEqual(len(lock["repos"]), 1)
        self.assertEqual(lock["repos"][0]["github"], "acme/demo")
        entry = gu.RepoEntry.from_dict(lock["repos"][0])
        self.assertEqual(entry.name, "demo")
        self.assertEqual(entry.remote, "acme/demo")
        self.assertEqual(entry.commit, "abc123def456")
        self.assertEqual(entry.install, "echo hi")
        self.assertEqual(entry.mirrors, [])

    def test_mirrors_roundtrip(self) -> None:
        catalog = gu.Catalog(
            version=1,
            root="/repos",
            repos=[
                gu.RepoEntry(
                    name="demo",
                    remote="me/demo",
                    url="https://github.com/me/demo.git",
                    path="demo",
                    branch="main",
                    commit="abc123",
                    mirrors=["https://github.com/acme/demo.git"],
                )
            ],
        )
        lock = gu.lock_from_catalog(catalog)
        self.assertEqual(
            lock["repos"][0]["mirrors"],
            ["https://github.com/acme/demo.git"],
        )
        entry = gu.RepoEntry.from_dict(lock["repos"][0])
        self.assertEqual(entry.mirrors, ["https://github.com/acme/demo.git"])

    def test_mirrors_round_trip(self) -> None:
        entry = gu.RepoEntry(
            name="agent-memory",
            remote="Klix927/agent-memory",
            url="https://github.com/Klix927/agent-memory.git",
            path="agent-memory",
            branch="main",
            commit="abc",
            mirrors=["https://github.com/Lolaplex/agent-memory.git"],
        )
        loaded = gu.RepoEntry.from_dict(entry.to_dict())
        self.assertEqual(
            loaded.mirrors,
            ["https://github.com/Lolaplex/agent-memory.git"],
        )

    def test_resolve_lock_root_ignores_foreign_absolute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "vendor.lock"
            lock_path.write_text("{}", encoding="utf-8")
            foreign = "C:/definitely/not/on/this/machine/repos"
            data = {"version": 1, "root": foreign}
            resolved = gu.resolve_lock_root(lock_path, data, None)
            self.assertEqual(resolved, lock_path.parent.resolve())

            override = Path(tmp) / "elsewhere"
            override.mkdir()
            resolved_override = gu.resolve_lock_root(lock_path, data, str(override))
            self.assertEqual(resolved_override, override.resolve())

    def test_save_and_load_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vendor.lock"
            data = {
                "version": 1,
                "root": str(Path(tmp).as_posix()),
                "repos": [
                    {
                        "name": "x",
                        "github": "o/x",
                        "url": "https://github.com/o/x.git",
                        "path": "x",
                        "branch": "main",
                        "commit": "deadbeef" * 5,
                    }
                ],
            }
            gu.save_lock(path, data)
            loaded = gu.load_lock(path)
            entry = gu.RepoEntry.from_dict(loaded["repos"][0])
            self.assertEqual(entry.remote, "o/x")


class StatusClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = gu.Catalog(version=1, root="/tmp/repos", repos=[])
        self.entry = gu.RepoEntry(
            name="t",
            remote="o/t",
            url="https://github.com/o/t.git",
            path="t",
            branch="main",
            commit="a" * 40,
        )
        self.catalog.repos.append(self.entry)

    @mock.patch("vend.repo_abs_path")
    @mock.patch("vend.is_dirty", return_value=False)
    @mock.patch("vend.current_commit", return_value="a" * 40)
    @mock.patch("vend.current_branch", return_value="main")
    @mock.patch("vend.pin_fetch_remote_names", return_value=["origin"])
    @mock.patch("vend.pick_sync_ref", return_value=(None, "current"))
    @mock.patch("vend.remote_ahead_behind", return_value=(0, 0))
    def test_pinned(self, *_m: mock.Mock) -> None:
        with mock.patch("pathlib.Path.exists", return_value=True):
            with mock.patch("pathlib.Path.__truediv__", return_value=mock.Mock(exists=lambda: True)):
                status = gu.classify_repo(self.catalog, self.entry, fetch=True)
        self.assertEqual(status, "pinned")

    @mock.patch("vend.repo_abs_path")
    @mock.patch("vend.is_dirty", return_value=True)
    def test_dirty(self, *_m: mock.Mock) -> None:
        with mock.patch("pathlib.Path.exists", return_value=True):
            status = gu.classify_repo(self.catalog, self.entry)
        self.assertEqual(status, "dirty")

    @mock.patch("vend.repo_abs_path")
    def test_missing(self, mock_path: mock.Mock) -> None:
        mock_path.return_value = Path("/nonexistent/t")
        status = gu.classify_repo(self.catalog, self.entry)
        self.assertEqual(status, "missing")


class ReplicateDryRunTests(unittest.TestCase):
    def test_dry_run_does_not_clone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "vendor.lock"
            lock.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "root": tmp.replace("\\", "/"),
                        "repos": [
                            {
                                "name": "new-repo",
                                "github": "o/new",
                                "url": "https://github.com/o/new.git",
                                "path": "new-repo",
                                "branch": "main",
                                "commit": "b" * 40,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch("vend.clone_repo") as clone:
                with mock.patch("builtins.print"):
                    gu.cmd_replicate(
                        argparse_namespace(lockfile=str(lock), root=tmp, dry_run=True)
                    )
                clone.assert_not_called()


class ConsolidateTests(unittest.TestCase):
    @mock.patch("vend.run_install")
    @mock.patch("vend.run_git")
    @mock.patch("vend.attach_entry_remotes")
    @mock.patch("vend.fetch_all_remotes")
    @mock.patch("vend.pin_fetch_remote_names", return_value=["origin"])
    @mock.patch("vend.pick_sync_ref", return_value=(None, "diverged"))
    @mock.patch("vend.is_dirty", return_value=False)
    @mock.patch("vend.current_branch", return_value="main")
    @mock.patch("vend.current_commit", return_value="c" * 40)
    @mock.patch("vend.merge_in_progress", return_value=False)
    @mock.patch("vend.rebase_in_progress", return_value=False)
    @mock.patch("vend.list_conflicts", return_value=["conflicted.txt"])
    @mock.patch("vend.repo_abs_path")
    def test_merge_conflict(
        self,
        mock_path: mock.Mock,
        *_m: mock.Mock,
    ) -> None:
        catalog = gu.Catalog(version=1, root="/tmp", repos=[])
        entry = gu.RepoEntry(
            name="t",
            remote="o/t",
            url="https://github.com/o/t.git",
            path="t",
            branch="main",
            commit="a" * 40,
        )
        mock_path.return_value = Path("/tmp/t")

        def run_git_side_effect(path: Path | None, *args: str, **kwargs: object):
            if args[:2] == ("merge", "--ff-only"):
                raise subprocess.CalledProcessError(1, "git")
            if args[:2] == ("merge", "origin/main"):
                raise subprocess.CalledProcessError(1, "git")
            return mock.Mock(stdout="", returncode=0)

        with mock.patch("vend.run_git", side_effect=run_git_side_effect):
            with mock.patch("pathlib.Path.exists", return_value=True):
                outcome, _ = gu.consolidate_repo(
                    catalog, entry, strategy="merge", mode="default"
                )
        self.assertEqual(outcome, "conflict")


class ManifestTests(unittest.TestCase):
    def test_parse_simple_yaml_list(self) -> None:
        data = gu.parse_simple_yaml(
            "version: 1\ninstall:\n  - npm ci\n  - npm run build\nupdate: npm ci\n"
        )
        self.assertEqual(data["version"], "1")
        self.assertEqual(gu.commands_to_shell(data["install"]), "npm ci && npm run build")
        self.assertEqual(gu.commands_to_shell(data["update"]), "npm ci")

    def test_load_manifest_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vend.json"
            path.write_text(
                json.dumps({"install": "make setup", "update": "make setup"}),
                encoding="utf-8",
            )
            manifest = gu.load_manifest_file(path)
            assert manifest is not None
            self.assertEqual(manifest.install, "make setup")

    def test_makefile_heuristic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile").write_text("install:\n\techo ok\n", encoding="utf-8")
            manifest = gu.detect_install_heuristic(root)
            assert manifest is not None
            self.assertEqual(manifest.install, "make install")

    def test_resolve_prefers_catalog_over_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "vend.yml").write_text("install: from-manifest\n", encoding="utf-8")
            entry = gu.RepoEntry(
                name="t",
                remote="o/t",
                url="https://github.com/o/t.git",
                path="t",
                branch="main",
                commit="a" * 40,
                install="from-catalog",
            )
            cmd, origin = gu.resolve_hook_command(entry, root, "install")
            self.assertEqual(cmd, "from-catalog")
            self.assertEqual(origin, "catalog")


def argparse_namespace(**kwargs: object) -> mock.Mock:
    ns = mock.Mock()
    for key, value in kwargs.items():
        setattr(ns, key, value)
    return ns


class VendorMdTests(unittest.TestCase):
    def test_render_contains_table(self) -> None:
        catalog = gu.Catalog(
            version=1,
            root="/repos",
            repos=[
                gu.RepoEntry(
                    name="app",
                    remote="me/app",
                    url="https://github.com/me/app.git",
                    path="app",
                    branch="main",
                    commit="1234567890ab",
                )
            ],
        )
        md = gu.render_vendor_md(catalog, exported_at="2026-01-01 00:00 UTC")
        self.assertIn("| app |", md)
        self.assertIn("1234567", md)
        self.assertIn("me/app", md)
        self.assertNotIn("/repos", md)
        self.assertIn("replicate --root", md)


class SelfCheckTests(unittest.TestCase):
    def test_cache_is_fresh_respects_ttl(self) -> None:
        recent = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        self.assertTrue(gu.cache_is_fresh(recent, fetch=False))
        self.assertFalse(gu.cache_is_fresh(recent, fetch=True))

    def test_format_self_check_behind(self) -> None:
        result = gu.SelfCheckResult(
            install_path=Path("C:/tools/vend"),
            remote="acme/vend",
            branch="main",
            local_commit="a" * 40,
            remote_commit="b" * 40,
            status="behind",
            behind=2,
        )
        text = gu.format_self_check(result)
        self.assertIn("self-update", text)

    @mock.patch("vend.save_self_check_cache")
    @mock.patch("vend.parse_rev", return_value="b" * 40)
    @mock.patch("vend.remote_ahead_behind", return_value=(0, 3))
    @mock.patch("vend.pick_self_ff_ref", return_value=("origin/main", "behind"))
    @mock.patch("vend.self_remote_names", return_value=["origin"])
    @mock.patch("vend.fetch_all_remotes")
    @mock.patch("vend.current_commit", return_value="a" * 40)
    @mock.patch("vend.current_branch", return_value="main")
    @mock.patch("vend.is_dirty", return_value=False)
    @mock.patch("vend.install_root")
    def test_check_self_git_behind(
        self,
        mock_root: mock.Mock,
        *_m: mock.Mock,
    ) -> None:
        root = Path("/tmp/vend")
        mock_root.return_value = root
        with mock.patch.object(Path, "exists", return_value=True):
            with mock.patch(
                "vend.read_origin", return_value=("acme/vend", "url")
            ):
                with mock.patch("vend.load_self_check_cache", return_value=None):
                    result = gu.check_self(fetch=True, use_cache=False)
        self.assertEqual(result.status, "behind")
        self.assertEqual(result.behind, 3)
        self.assertEqual(result.sync_ref, "origin/main")

    @mock.patch.dict(os.environ, {"VEND_SKIP_SELF_CHECK": "1"})
    @mock.patch("vend.check_self")
    def test_maybe_warn_respects_skip_env(self, mock_check: mock.Mock) -> None:
        gu.maybe_warn_self_update()
        mock_check.assert_not_called()

    def test_self_remote_names_includes_org_mirror(self) -> None:
        with mock.patch(
            "vend.list_remotes",
            return_value=[
                (
                    "origin",
                    "https://github.com/Klix927/vend.git",
                    "Klix927/vend",
                ),
                (
                    "lolaplex",
                    "https://github.com/Lolaplex/vend.git",
                    "Lolaplex/vend",
                ),
            ],
        ):
            with mock.patch(
                "vend.read_origin",
                return_value=(
                    "Klix927/vend",
                    "https://github.com/Klix927/vend.git",
                ),
            ):
                names = gu.self_remote_names(Path("/tmp/vend"))
        self.assertEqual(names, ["origin", "lolaplex"])

    def test_remote_from_sync_ref(self) -> None:
        self.assertEqual(gu.remote_from_sync_ref("lolaplex/main", "main"), "lolaplex")
        self.assertEqual(
            gu.remote_from_sync_ref("origin/feat/x", "feat/x"), "origin"
        )

    @mock.patch("vend.check_self")
    @mock.patch("vend.run_update_hook")
    @mock.patch(
        "vend.resolve_hook_command",
        return_value=("python -m pip install -e .", "manifest"),
    )
    @mock.patch("vend.catalog_self_entry", return_value=None)
    @mock.patch("vend.self_hook_entry")
    @mock.patch("vend.current_commit", return_value="b" * 40)
    @mock.patch("vend.run_git")
    @mock.patch("vend.is_dirty", return_value=False)
    @mock.patch("vend.install_root")
    def test_apply_self_update_ff_and_hook(
        self,
        mock_root: mock.Mock,
        _dirty: mock.Mock,
        mock_run: mock.Mock,
        _head: mock.Mock,
        mock_entry: mock.Mock,
        _catalog: mock.Mock,
        _resolve: mock.Mock,
        mock_hook: mock.Mock,
        mock_check: mock.Mock,
    ) -> None:
        root = Path("/tmp/vend")
        mock_root.return_value = root
        mock_entry.return_value = gu.RepoEntry(
            name="vend",
            remote="acme/vend",
            url="https://github.com/acme/vend.git",
            path=str(root),
            branch="main",
            commit="a" * 40,
        )
        behind = gu.SelfCheckResult(
            install_path=root,
            remote="acme/vend",
            branch="main",
            local_commit="a" * 40,
            remote_commit="b" * 40,
            status="behind",
            behind=1,
            sync_ref="lolaplex/main",
        )
        done = gu.SelfCheckResult(
            install_path=root,
            remote="acme/vend",
            branch="main",
            local_commit="b" * 40,
            remote_commit="b" * 40,
            status="up-to-date",
        )
        mock_check.side_effect = [behind, done]
        with mock.patch.object(Path, "exists", return_value=True):
            result = gu.apply_self_update(interactive=True)
        mock_run.assert_called_with(root, "merge", "--ff-only", "lolaplex/main")
        mock_hook.assert_called_once()
        self.assertEqual(result.status, "up-to-date")

    @mock.patch.dict(os.environ, {"VEND_SKIP_SELF_CHECK": "1"})
    @mock.patch("vend.apply_self_update")
    def test_maybe_apply_respects_skip_env(self, mock_apply: mock.Mock) -> None:
        gu.maybe_apply_self_update()
        mock_apply.assert_not_called()

    @mock.patch("vend.apply_self_update")
    @mock.patch("vend.check_self")
    @mock.patch("vend.maybe_warn_self_update")
    def test_maybe_apply_skips_patch_when_cache_fresh(
        self,
        mock_warn: mock.Mock,
        mock_check: mock.Mock,
        mock_apply: mock.Mock,
    ) -> None:
        recent = {"checked_at": datetime.now(timezone.utc).isoformat()}
        with mock.patch("vend.load_self_check_cache", return_value=recent):
            gu.maybe_apply_self_update()
        mock_apply.assert_not_called()
        mock_check.assert_not_called()
        mock_warn.assert_called_once()

    @mock.patch("vend.apply_self_update")
    @mock.patch("vend.check_self")
    def test_maybe_apply_patches_when_cache_stale_and_behind(
        self,
        mock_check: mock.Mock,
        mock_apply: mock.Mock,
    ) -> None:
        behind = gu.SelfCheckResult(
            install_path=Path("/tmp/vend"),
            remote="acme/vend",
            branch="main",
            local_commit="a" * 40,
            remote_commit="b" * 40,
            status="behind",
            sync_ref="origin/main",
        )
        mock_check.return_value = behind
        with mock.patch("vend.load_self_check_cache", return_value=None):
            gu.maybe_apply_self_update()
        mock_check.assert_called_once_with(fetch=True, use_cache=False)
        mock_apply.assert_called_once_with(interactive=False, observed=behind)


class GithubProjectIdentityTests(unittest.TestCase):
    def test_same_repo_name_different_owner_is_not_the_same_clone(self) -> None:
        self.assertFalse(
            gu.same_github_project(
                "Klix927/agent-memory",
                "lolaplex/agent-memory",
                "https://github.com/Klix927/agent-memory.git",
                "https://github.com/Lolaplex/agent-memory.git",
            )
        )

    def test_same_url_is_equivalent(self) -> None:
        self.assertTrue(
            gu.urls_equivalent(
                "https://github.com/Lolaplex/agent-memory.git",
                "https://github.com/Lolaplex/agent-memory.git",
            )
        )

    def test_different_repo_name(self) -> None:
        self.assertFalse(
            gu.same_github_project("me/foo", "org/bar")
        )

    def test_gitlab_keeps_full_path(self) -> None:
        self.assertFalse(
            gu.same_github_project(
                "gitlab.com/group/project",
                "gitlab.com/other/project",
                "https://gitlab.com/group/project.git",
                "https://gitlab.com/other/project.git",
            )
        )

    def test_github_owner_repo_from_id(self) -> None:
        self.assertEqual(
            gu.github_owner_repo("Lolaplex/agent-memory"),
            ("lolaplex", "agent-memory"),
        )

    def test_suggested_remote_name_uses_owner(self) -> None:
        self.assertEqual(
            gu.suggested_remote_name("https://github.com/Lolaplex/agent-memory.git"),
            "lolaplex",
        )


class SyncRefTests(unittest.TestCase):
    def test_origin_only_even_if_other_remote_is_ahead(self) -> None:
        def fake_ahead(_path: Path, _branch: str, remote: str) -> tuple[int, int]:
            if remote == "origin":
                return (0, 0)
            if remote == "lolaplex":
                return (0, 1)
            return (0, 0)

        with mock.patch("vend.remote_ahead_behind", side_effect=fake_ahead):
            ref, kind = gu.pick_sync_ref(Path("/tmp/r"), "main", ["origin", "lolaplex"])
        self.assertEqual(kind, "current")
        self.assertIsNone(ref)

    def test_origin_diverged_wins(self) -> None:
        def fake_ahead(_path: Path, _branch: str, remote: str) -> tuple[int, int]:
            if remote == "origin":
                return (1, 1)
            return (0, 3)

        with mock.patch("vend.remote_ahead_behind", side_effect=fake_ahead):
            ref, kind = gu.pick_sync_ref(Path("/tmp/r"), "main", ["origin", "lolaplex"])
        self.assertEqual(kind, "diverged")
        self.assertIsNone(ref)

    def test_origin_behind_ffs_origin(self) -> None:
        with mock.patch("vend.remote_ahead_behind", return_value=(0, 2)):
            ref, kind = gu.pick_sync_ref(Path("/tmp/r"), "main", ["origin", "lolaplex"])
        self.assertEqual(kind, "behind")
        self.assertEqual(ref, "origin/main")


class SelfFfRefTests(unittest.TestCase):
    def test_ff_descendant_mirror_when_origin_is_current(self) -> None:
        def fake_ahead(_path: Path, _branch: str, remote: str) -> tuple[int, int]:
            if remote == "origin":
                return (0, 0)
            if remote == "lolaplex":
                return (0, 1)
            return (0, 0)

        with mock.patch("vend.remote_ahead_behind", side_effect=fake_ahead):
            with mock.patch("vend.is_ancestor", return_value=True):
                ref, kind = gu.pick_self_ff_ref(
                    Path("/tmp/r"), "main", ["origin", "lolaplex"]
                )
        self.assertEqual(kind, "behind")
        self.assertEqual(ref, "lolaplex/main")

    def test_skips_non_ancestor_even_if_ahead(self) -> None:
        def fake_ahead(_path: Path, _branch: str, remote: str) -> tuple[int, int]:
            if remote == "origin":
                return (0, 0)
            return (0, 2)

        with mock.patch("vend.remote_ahead_behind", side_effect=fake_ahead):
            with mock.patch("vend.is_ancestor", return_value=False):
                ref, kind = gu.pick_self_ff_ref(
                    Path("/tmp/r"), "main", ["origin", "other"]
                )
        self.assertEqual(kind, "current")
        self.assertIsNone(ref)

    def test_origin_diverged_blocks_self_ff(self) -> None:
        def fake_ahead(_path: Path, _branch: str, remote: str) -> tuple[int, int]:
            if remote == "origin":
                return (1, 1)
            return (0, 3)

        with mock.patch("vend.remote_ahead_behind", side_effect=fake_ahead):
            ref, kind = gu.pick_self_ff_ref(
                Path("/tmp/r"), "main", ["origin", "lolaplex"]
            )
        self.assertEqual(kind, "diverged")
        self.assertIsNone(ref)


class MirrorShaTests(unittest.TestCase):
    def test_extra_remote_without_sha_is_not_a_mirror(self) -> None:
        remotes = [
            ("origin", "https://github.com/me/app.git", "me/app"),
            ("lolaplex", "https://github.com/Lolaplex/app.git", "lolaplex/app"),
        ]
        with mock.patch("vend.list_remotes", return_value=remotes):
            with mock.patch("vend.remotes_containing_commit", return_value={"origin"}):
                mirrors = gu.mirrors_from_clone(
                    Path("/tmp/app"),
                    "https://github.com/me/app.git",
                    "a" * 40,
                )
        self.assertEqual(mirrors, [])

    def test_extra_remote_with_sha_is_a_fetch_source(self) -> None:
        remotes = [
            ("origin", "https://github.com/me/app.git", "me/app"),
            ("lolaplex", "https://github.com/Lolaplex/app.git", "lolaplex/app"),
        ]
        with mock.patch("vend.list_remotes", return_value=remotes):
            with mock.patch(
                "vend.remotes_containing_commit",
                return_value={"origin", "lolaplex"},
            ):
                mirrors = gu.mirrors_from_clone(
                    Path("/tmp/app"),
                    "https://github.com/me/app.git",
                    "a" * 40,
                )
        self.assertEqual(mirrors, ["https://github.com/Lolaplex/app.git"])


class CheckoutPinTests(unittest.TestCase):
    @mock.patch("vend.run_git")
    @mock.patch("vend.fetch_commit")
    @mock.patch("vend.commit_exists", side_effect=[False, True])
    def test_fetches_then_checkouts(
        self,
        _exists: mock.Mock,
        mock_fetch: mock.Mock,
        mock_run: mock.Mock,
    ) -> None:
        dest = Path("/tmp/agent-memory")
        urls = ["https://github.com/Lolaplex/agent-memory.git"]
        gu.checkout_pin(dest, "a" * 40, extra_urls=urls)
        mock_fetch.assert_called_once_with(dest, "a" * 40, urls)
        mock_run.assert_called_once_with(dest, "checkout", "--detach", "a" * 40)

    @mock.patch("vend.fetch_commit")
    @mock.patch("vend.commit_exists", return_value=False)
    def test_errors_when_commit_missing(self, _exists: mock.Mock, mock_fetch: mock.Mock) -> None:
        with self.assertRaises(SystemExit) as raised:
            gu.checkout_pin(Path("/tmp/r"), "deadbeef")
        self.assertIn("not found", str(raised.exception))
        mock_fetch.assert_called_once()


class HelpSpecTests(unittest.TestCase):
    def test_cli_spec_lists_commands_and_global_flags(self) -> None:
        spec = gu.cli_spec()
        names = {c["name"] for c in spec["commands"]}
        self.assertIn("init", names)
        self.assertIn("replicate", names)
        self.assertIn("help", names)
        self.assertIn("man", names)
        self.assertIn("install-skills", names)
        self.assertIn("hook-sync", names)
        self.assertIn("pin", names)
        flags = {f for opt in spec["options"] for f in opt.get("flags", [])}
        self.assertIn("--help-json", flags)
        init = next(c for c in spec["commands"] if c["name"] == "init")
        self.assertTrue(init["help"])
        self.assertTrue(any("--root" in o.get("flags", []) for o in init["options"]))

    def test_help_json_filters_command(self) -> None:
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            gu.main(["--help-json", "replicate"])
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["command"]["name"], "replicate")
        self.assertTrue(payload["command"]["arguments"])

    def test_man_page_is_roff(self) -> None:
        man = gu.render_man()
        self.assertIn(".TH VEND 1", man)
        self.assertIn("vend", man)
        self.assertIn(".B init", man)
        self.assertIn("hook-sync", man)
        self.assertIn("pin --here", man)


class SkillInstallTests(unittest.TestCase):
    def test_template_has_frontmatter(self) -> None:
        text = gu.skill_template_path().read_text(encoding="utf-8")
        self.assertIn("name: vend", text)
        self.assertIn("vend --help-json", text)
        self.assertIn(gu.SKILL_PATHS_BEGIN, text)

    def test_install_user_skills_writes_cursor_and_agents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            written = gu.install_user_skills(home=home)
            self.assertEqual(len(written), 2)
            root = gu.normalize_path(gu.install_root())
            for path in written:
                self.assertTrue(path.is_file())
                body = path.read_text(encoding="utf-8")
                self.assertIn("name: vend", body)
                self.assertIn(root, body)
            cursor = home / ".cursor" / "skills" / "vend" / "SKILL.md"
            agents = home / ".agents" / "skills" / "vend" / "SKILL.md"
            self.assertTrue(cursor.is_file())
            self.assertTrue(agents.is_file())


class PinHookTests(unittest.TestCase):
    def test_catalog_entry_for_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "app"
            repo.mkdir()
            catalog = gu.Catalog(
                version=1,
                root=str(root),
                repos=[
                    gu.RepoEntry(
                        name="app",
                        remote="o/app",
                        url="https://github.com/o/app.git",
                        path="app",
                        branch="main",
                        commit="a" * 40,
                    )
                ],
            )
            self.assertIs(gu.catalog_entry_for_path(catalog, repo), catalog.repos[0])
            self.assertIsNone(gu.catalog_entry_for_path(catalog, root / "other"))

    def test_pin_hook_script_post_checkout(self) -> None:
        text = gu.pin_hook_script("post-checkout", "vend")
        self.assertIn(gu.PIN_HOOK_MARKER, text)
        self.assertIn('if [ "$1" = "$2" ]; then exit 0; fi', text)
        self.assertIn("pin --here --quiet", text)

    def test_install_pin_hooks_writes_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            hooks = repo / ".git" / "hooks"
            hooks.mkdir(parents=True)
            installed, skipped = gu.install_pin_hooks(repo)
            self.assertEqual(installed, list(gu.PIN_HOOK_NAMES))
            self.assertEqual(skipped, [])
            for name in gu.PIN_HOOK_NAMES:
                content = (hooks / name).read_text(encoding="utf-8")
                self.assertIn(gu.PIN_HOOK_MARKER, content)

    def test_install_pin_hooks_skips_foreign_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            hooks = repo / ".git" / "hooks"
            hooks.mkdir(parents=True)
            (hooks / "post-commit").write_text("#!/bin/sh\necho custom\n", encoding="utf-8")
            installed, skipped = gu.install_pin_hooks(repo)
            self.assertNotIn("post-commit", installed)
            self.assertIn("post-commit (foreign hook)", skipped)
            self.assertEqual((hooks / "post-commit").read_text(encoding="utf-8"), "#!/bin/sh\necho custom\n")
            self.assertTrue(installed)

    @mock.patch("vend.save_catalog")
    @mock.patch("vend.pin_entry", return_value=True)
    @mock.patch("vend.catalog_entry_for_path")
    def test_pin_here_quiet_on_missing(
        self,
        mock_lookup: mock.Mock,
        mock_pin: mock.Mock,
        _save: mock.Mock,
    ) -> None:
        mock_lookup.return_value = None
        with mock.patch("vend.load_catalog", return_value=gu.Catalog(version=1, root="/tmp", repos=[])):
            with mock.patch("vend.log_hook_pin") as log:
                gu.cmd_pin(argparse_namespace(here=True, quiet=True, name=None, export=False))
        log.assert_called_once()
        mock_pin.assert_not_called()


class InitBootstrapTests(unittest.TestCase):
    def test_infer_clone_root_uses_parent_of_this_checkout(self) -> None:
        self_root = gu.install_root()
        self.assertEqual(gu.infer_clone_root(str(self_root)), self_root.parent.resolve())

    def test_init_writes_catalog_without_pip_or_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            catalog_path = tmp_p / "catalog.json"
            config = tmp_p / "cfg"
            config.mkdir()
            root = tmp_p / "repos"
            root.mkdir()
            ns = argparse_namespace(
                root=str(root),
                force=False,
                no_pip=True,
                no_lock=True,
                lock=None,
            )
            with mock.patch.object(gu, "CATALOG_PATH", catalog_path):
                with mock.patch.object(gu, "CONFIG_DIR", config):
                    with mock.patch.object(gu, "LOG_DIR", config / "logs"):
                        with mock.patch.object(
                            gu, "install_user_skills", return_value=[config / "skill.md"]
                        ) as skills:
                            with mock.patch.object(gu, "pip_editable_self") as pip:
                                with mock.patch.object(gu, "replicate_lockfile") as repl:
                                    gu.cmd_init(ns)
            pip.assert_not_called()
            repl.assert_not_called()
            skills.assert_called_once()
            data = json.loads(catalog_path.read_text(encoding="utf-8"))
            self.assertEqual(Path(data["root"]).resolve(), root.resolve())
            self.assertEqual(data["repos"], [])

    def test_cli_init_flags(self) -> None:
        spec = gu.cli_spec()
        init = next(c for c in spec["commands"] if c["name"] == "init")
        flags = {f for opt in init["options"] for f in opt.get("flags", [])}
        self.assertIn("--no-lock", flags)
        self.assertIn("--no-pip", flags)
        self.assertIn("--lock", flags)


if __name__ == "__main__":
    unittest.main()
