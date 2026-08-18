"""Tests for git_updater."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import git_updater as gu


class ParseRepoSpecTests(unittest.TestCase):
    def test_owner_repo(self) -> None:
        name, slug, url = gu.parse_repo_spec("Cypoe/living-software")
        self.assertEqual(name, "living-software")
        self.assertEqual(slug, "Cypoe/living-software")
        self.assertEqual(url, "https://github.com/Cypoe/living-software.git")

    def test_https_url(self) -> None:
        _, slug, url = gu.parse_repo_spec("https://github.com/eaxum/clustta-client.git")
        self.assertEqual(slug, "eaxum/clustta-client")
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
        parsed = gu.parse_github_remote("git@github.com:Cypoe/isa-physics.git")
        self.assertEqual(parsed, ("Cypoe", "isa-physics"))
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
        self.assertEqual(len(lock["repos"]), 1)
        self.assertEqual(lock["repos"][0]["github"], "acme/demo")
        entry = gu.RepoEntry.from_dict(lock["repos"][0])
        self.assertEqual(entry.name, "demo")
        self.assertEqual(entry.remote, "acme/demo")
        self.assertEqual(entry.commit, "abc123def456")
        self.assertEqual(entry.install, "echo hi")

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

    @mock.patch("git_updater.repo_abs_path")
    @mock.patch("git_updater.is_dirty", return_value=False)
    @mock.patch("git_updater.current_commit", return_value="a" * 40)
    @mock.patch("git_updater.current_branch", return_value="main")
    @mock.patch("git_updater.ahead_behind", return_value=(0, 0))
    def test_pinned(self, *_m: mock.Mock) -> None:
        with mock.patch("pathlib.Path.exists", return_value=True):
            with mock.patch("pathlib.Path.__truediv__", return_value=mock.Mock(exists=lambda: True)):
                status = gu.classify_repo(self.catalog, self.entry, fetch=True)
        self.assertEqual(status, "pinned")

    @mock.patch("git_updater.repo_abs_path")
    @mock.patch("git_updater.is_dirty", return_value=True)
    def test_dirty(self, *_m: mock.Mock) -> None:
        with mock.patch("pathlib.Path.exists", return_value=True):
            status = gu.classify_repo(self.catalog, self.entry)
        self.assertEqual(status, "dirty")

    @mock.patch("git_updater.repo_abs_path")
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
            with mock.patch("git_updater.clone_repo") as clone:
                with mock.patch("builtins.print"):
                    gu.cmd_replicate(
                        argparse_namespace(lockfile=str(lock), root=tmp, dry_run=True)
                    )
                clone.assert_not_called()


class ConsolidateTests(unittest.TestCase):
    @mock.patch("git_updater.run_install")
    @mock.patch("git_updater.run_git")
    @mock.patch("git_updater.ahead_behind", return_value=(1, 2))
    @mock.patch("git_updater.is_dirty", return_value=False)
    @mock.patch("git_updater.current_branch", return_value="main")
    @mock.patch("git_updater.current_commit", return_value="c" * 40)
    @mock.patch("git_updater.merge_in_progress", return_value=False)
    @mock.patch("git_updater.rebase_in_progress", return_value=False)
    @mock.patch("git_updater.list_conflicts", return_value=["conflicted.txt"])
    @mock.patch("git_updater.repo_abs_path")
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

        with mock.patch("git_updater.run_git", side_effect=run_git_side_effect):
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
            path = Path(tmp) / ".git-updater.json"
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
            (root / ".git-updater.yaml").write_text("install: from-manifest\n", encoding="utf-8")
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


class SelfCheckTests(unittest.TestCase):
    def test_cache_is_fresh_respects_ttl(self) -> None:
        recent = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        self.assertTrue(gu.cache_is_fresh(recent, fetch=False))
        self.assertFalse(gu.cache_is_fresh(recent, fetch=True))

    def test_format_self_check_behind(self) -> None:
        result = gu.SelfCheckResult(
            install_path=Path("C:/tools/git-updater"),
            remote="acme/git-updater",
            branch="main",
            local_commit="a" * 40,
            remote_commit="b" * 40,
            status="behind",
            behind=2,
        )
        text = gu.format_self_check(result)
        self.assertIn("self-update", text)

    @mock.patch("git_updater.save_self_check_cache")
    @mock.patch("git_updater.github_remote_tip", return_value=("main", "b" * 40))
    @mock.patch("git_updater.current_commit", return_value="a" * 40)
    @mock.patch("git_updater.current_branch", return_value="main")
    @mock.patch("git_updater.is_dirty", return_value=False)
    @mock.patch("git_updater.run_git")
    @mock.patch("git_updater.install_root")
    def test_check_self_git_behind(
        self,
        mock_root: mock.Mock,
        mock_run_git: mock.Mock,
        *_m: mock.Mock,
    ) -> None:
        root = Path("/tmp/git-updater")
        mock_root.return_value = root
        with mock.patch.object(Path, "exists", return_value=True):
            def git_side_effect(path: Path | None, *args: str, **kwargs: object):
                cmd = mock.Mock()
                if args == ("rev-parse", "origin/main"):
                    cmd.stdout = "b" * 40 + "\n"
                elif args == ("rev-list", "--left-right", "--count", "HEAD...origin/main"):
                    cmd.stdout = "0\t3\n"
                else:
                    cmd.stdout = ""
                return cmd

            mock_run_git.side_effect = git_side_effect
            with mock.patch("git_updater.read_origin", return_value=("acme/git-updater", "url")):
                with mock.patch("git_updater.load_self_check_cache", return_value=None):
                    result = gu.check_self(fetch=True, use_cache=False)
        self.assertEqual(result.status, "behind")
        self.assertEqual(result.behind, 3)

    @mock.patch.dict(os.environ, {"GIT_UPDATER_SKIP_SELF_CHECK": "1"})
    @mock.patch("git_updater.check_self")
    def test_maybe_warn_respects_skip_env(self, mock_check: mock.Mock) -> None:
        gu.maybe_warn_self_update()
        mock_check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
