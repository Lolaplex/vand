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

    @mock.patch("git_updater.repo_abs_path")
    @mock.patch("git_updater.is_dirty", return_value=False)
    @mock.patch("git_updater.current_commit", return_value="a" * 40)
    @mock.patch("git_updater.current_branch", return_value="main")
    @mock.patch("git_updater.pin_fetch_remote_names", return_value=["origin"])
    @mock.patch("git_updater.pick_sync_ref", return_value=(None, "current"))
    @mock.patch("git_updater.remote_ahead_behind", return_value=(0, 0))
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
    @mock.patch("git_updater.attach_entry_remotes")
    @mock.patch("git_updater.fetch_all_remotes")
    @mock.patch("git_updater.pin_fetch_remote_names", return_value=["origin"])
    @mock.patch("git_updater.pick_sync_ref", return_value=(None, "diverged"))
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

        with mock.patch("git_updater.remote_ahead_behind", side_effect=fake_ahead):
            ref, kind = gu.pick_sync_ref(Path("/tmp/r"), "main", ["origin", "lolaplex"])
        self.assertEqual(kind, "current")
        self.assertIsNone(ref)

    def test_origin_diverged_wins(self) -> None:
        def fake_ahead(_path: Path, _branch: str, remote: str) -> tuple[int, int]:
            if remote == "origin":
                return (1, 1)
            return (0, 3)

        with mock.patch("git_updater.remote_ahead_behind", side_effect=fake_ahead):
            ref, kind = gu.pick_sync_ref(Path("/tmp/r"), "main", ["origin", "lolaplex"])
        self.assertEqual(kind, "diverged")
        self.assertIsNone(ref)

    def test_origin_behind_ffs_origin(self) -> None:
        with mock.patch("git_updater.remote_ahead_behind", return_value=(0, 2)):
            ref, kind = gu.pick_sync_ref(Path("/tmp/r"), "main", ["origin", "lolaplex"])
        self.assertEqual(kind, "behind")
        self.assertEqual(ref, "origin/main")


class MirrorShaTests(unittest.TestCase):
    def test_extra_remote_without_sha_is_not_a_mirror(self) -> None:
        remotes = [
            ("origin", "https://github.com/me/app.git", "me/app"),
            ("lolaplex", "https://github.com/Lolaplex/app.git", "lolaplex/app"),
        ]
        with mock.patch("git_updater.list_remotes", return_value=remotes):
            with mock.patch("git_updater.remotes_containing_commit", return_value={"origin"}):
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
        with mock.patch("git_updater.list_remotes", return_value=remotes):
            with mock.patch(
                "git_updater.remotes_containing_commit",
                return_value={"origin", "lolaplex"},
            ):
                mirrors = gu.mirrors_from_clone(
                    Path("/tmp/app"),
                    "https://github.com/me/app.git",
                    "a" * 40,
                )
        self.assertEqual(mirrors, ["https://github.com/Lolaplex/app.git"])


class CheckoutPinTests(unittest.TestCase):
    @mock.patch("git_updater.run_git")
    @mock.patch("git_updater.fetch_commit")
    @mock.patch("git_updater.commit_exists", side_effect=[False, True])
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

    @mock.patch("git_updater.fetch_commit")
    @mock.patch("git_updater.commit_exists", return_value=False)
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
        self.assertIn(".TH GIT-UPDATER 1", man)
        self.assertIn("git-updater", man)
        self.assertIn(".B init", man)
        self.assertIn("--help-json", man)


class SkillInstallTests(unittest.TestCase):
    def test_template_has_frontmatter(self) -> None:
        text = gu.skill_template_path().read_text(encoding="utf-8")
        self.assertIn("name: git-updater", text)
        self.assertIn("git-updater --help-json", text)
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
                self.assertIn("name: git-updater", body)
                self.assertIn(root, body)
            cursor = home / ".cursor" / "skills" / "git-updater" / "SKILL.md"
            agents = home / ".agents" / "skills" / "git-updater" / "SKILL.md"
            self.assertTrue(cursor.is_file())
            self.assertTrue(agents.is_file())


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
