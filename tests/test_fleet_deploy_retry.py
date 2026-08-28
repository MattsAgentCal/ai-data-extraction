import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO / "configs" / "old-macbook.deploy.json"
sys.path.insert(0, str(REPO))

import fleet_deploy_retry as retry  # noqa: E402


def inventory_response(
    *,
    claude=True,
    codex=True,
    openclaw=False,
    hermes=False,
    hermes_instances=None,
):
    home = retry.EXPECTED_REMOTE_HOME
    instances = hermes_instances
    if instances is None:
        instances = (
            [{"home": f"{home}/.hermes", "binary": f"{home}/.local/bin/hermes"}]
            if hermes
            else []
        )
    return {
        "schema_version": 1,
        "ok": True,
        "identity": {
            "user": retry.EXPECTED_REMOTE_USER,
            "home": home,
            "platform": "Darwin",
            "python_major": 3,
        },
        "inventory": {
            "claude": {
                "present": claude,
                "roots": [f"{home}/.claude"] if claude else [],
            },
            "codex": {
                "present": codex,
                "roots": [f"{home}/.codex"] if codex else [],
            },
            "openclaw": {
                "present": openclaw,
                "roots": [f"{home}/.openclaw/agents/main/sessions"] if openclaw else [],
            },
            "hermes": {"present": bool(instances), "instances": instances},
        },
    }


class FakeTransport:
    def __init__(
        self,
        *,
        inventory=None,
        offline=False,
        fail=None,
        mismatch_at=None,
    ):
        self.inventory = inventory or inventory_response()
        self.offline = offline
        self.fail = fail
        self.mismatch_at = mismatch_at
        self.actions = []
        self.copies = []
        self.archive_installs = []

    def helper(self, action, values):
        self.actions.append((action, values))
        if action == "inventory":
            if self.offline:
                raise retry.OfflineError()
            if self.fail:
                raise retry.DeployError(self.fail)
            return self.inventory
        if action == "prepare":
            return {
                "schema_version": 1,
                "ok": True,
                "stage_path": f"{retry.EXPECTED_REMOTE_REPO}/.fleet-deploy-test",
            }
        if action in {"verify_stage", "install_files", "verify_final"}:
            hashes = dict(values["expected_sha256"])
            if self.mismatch_at == action:
                first = next(iter(hashes))
                hashes[first] = "0" * 64
            return {"schema_version": 1, "ok": True, "sha256": hashes}
        raise AssertionError(f"unexpected helper action: {action}")

    def copy_file(self, local_path, remote_path):
        self.copies.append((Path(local_path), remote_path))

    def install_archive_launchd(self, remote_script, remote_config):
        self.archive_installs.append((remote_script, remote_config))
        return {
            "label": "com.mattrotundo.ai-chat-archive.old-macbook",
            "loaded": True,
            "plist_path": (
                f"{retry.EXPECTED_REMOTE_HOME}/Library/LaunchAgents/"
                "com.mattrotundo.ai-chat-archive.old-macbook.plist"
            ),
        }


class FleetDeployRetryTests(unittest.TestCase):
    def setUp(self):
        self.config = retry.load_deploy_config(CONFIG_PATH)

    def test_offline_is_body_free_normal_retryable_status(self):
        transport = FakeTransport(offline=True)
        result = retry.deploy_once(self.config, transport, repo_root=REPO)

        self.assertEqual(result["status"], "offline_retryable")
        self.assertTrue(result["retryable"])
        self.assertEqual(result["errors"], [{"code": "ssh_unreachable"}])
        self.assertEqual(result["files"], {"count": 0, "verified": False})
        self.assertEqual(transport.copies, [])
        self.assertEqual(transport.archive_installs, [])
        self.assertNotIn(retry.EXPECTED_REMOTE_HOME, json.dumps(result))

    def test_identity_mismatch_fails_before_any_remote_mutation(self):
        transport = FakeTransport(fail="identity_mismatch")
        result = retry.deploy_once(self.config, transport, repo_root=REPO)

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["retryable"])
        self.assertEqual(result["stage"], "inventory")
        self.assertEqual(result["errors"], [{"code": "identity_mismatch"}])
        self.assertEqual([action for action, _ in transport.actions], ["inventory"])
        self.assertEqual(transport.copies, [])

    def test_inventory_validation_fails_closed_on_home_platform_and_unsafe_root(self):
        wrong_home = inventory_response()
        wrong_home["identity"]["home"] = "/Users/someone-else"
        non_darwin = inventory_response()
        non_darwin["identity"]["platform"] = "Linux"
        unsafe_root = inventory_response()
        unsafe_root["inventory"]["claude"]["roots"] = ["/tmp/claude"]
        cases = (
            (wrong_home, "home_mismatch"),
            (non_darwin, "non_darwin"),
            (unsafe_root, "unsafe_path"),
        )
        for response, code in cases:
            with self.subTest(code=code):
                with self.assertRaisesRegex(retry.DeployError, code):
                    retry.validate_inventory_response(self.config, response)

    def test_ssh_transport_distinguishes_retryable_offline_from_missing_python(self):
        cases = (
            (255, "ssh: connect to host oldmac port 22: Operation timed out", "ssh_unreachable"),
            (127, "python3: command not found", "missing_python"),
        )
        for returncode, stderr, code in cases:
            with self.subTest(code=code):
                def runner(command, **kwargs):
                    return subprocess.CompletedProcess(command, returncode, "", stderr)

                transport = retry.SshTransport(self.config, runner=runner)
                with self.assertRaisesRegex(retry.DeployError, code):
                    transport.helper("inventory", {})

    def test_deploys_exact_allowlist_without_delete_semantics(self):
        transport = FakeTransport()
        result = retry.deploy_once(self.config, transport, repo_root=REPO)

        copied_relative = [
            remote.split("/.fleet-deploy-test/", 1)[1]
            for _, remote in transport.copies
        ]
        self.assertEqual(tuple(copied_relative), retry.DEPLOYED_FILES)
        self.assertEqual(set(result["files"]["sha256"]), set(retry.DEPLOYED_FILES))
        self.assertEqual(result["files"]["count"], 6)
        self.assertTrue(result["files"]["verified"])
        self.assertNotIn("--delete", json.dumps(transport.copies, default=str))

        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary).resolve() / "fleet_chat_archive.py"
            source.write_text("runtime")
            ssh = retry.SshTransport(self.config, runner=runner)
            ssh.copy_file(
                source,
                f"{retry.EXPECTED_REMOTE_REPO}/.fleet-deploy-test/fleet_chat_archive.py",
            )
        self.assertEqual(calls[0][0], "scp")
        self.assertNotIn("--delete", calls[0])
        self.assertEqual(calls[0].count(str(source)), 1)

    def test_inventory_derives_only_present_sources_but_keeps_four_harness_inventory(self):
        response = inventory_response(
            claude=True,
            codex=False,
            openclaw=True,
            hermes=False,
        )
        inventory = retry.validate_inventory_response(self.config, response)
        derived = retry.derive_archive_config(self.config, inventory)

        self.assertEqual(
            set(derived["sources"]), {"claude_roots", "openclaw_roots"}
        )
        self.assertEqual(
            derived["sources"]["openclaw_roots"],
            [f"{retry.EXPECTED_REMOTE_HOME}/.openclaw/agents/main/sessions"],
        )
        self.assertEqual(
            derived["inventory_harnesses"],
            ["claude", "codex", "openclaw", "hermes"],
        )
        self.assertEqual(derived["drive_root"], None)

    def test_remote_launchd_install_uses_fixed_ssh_argv_and_stdin_helper(self):
        commands = []

        def runner(command, **kwargs):
            commands.append((command, kwargs))
            payload = {
                "schema_version": 1,
                "ok": True,
                "launchd": {
                    "label": "com.mattrotundo.ai-chat-archive.old-macbook",
                    "loaded": True,
                    "plist_path": (
                        f"{retry.EXPECTED_REMOTE_HOME}/Library/LaunchAgents/"
                        "com.mattrotundo.ai-chat-archive.old-macbook.plist"
                    ),
                },
            }
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps(payload),
                "",
            )

        transport = retry.SshTransport(self.config, runner=runner)
        transport.install_archive_launchd(
            f"{retry.EXPECTED_REMOTE_REPO}/fleet_chat_archive.py",
            f"{retry.EXPECTED_REMOTE_REPO}/configs/old-macbook.json",
        )

        command, kwargs = commands[0]
        self.assertEqual(command[-3:], ["oldmac", "python3", "-"])
        self.assertNotIn(retry.EXPECTED_REMOTE_REPO, command)
        self.assertIn("REQUEST_B64", kwargs["input"])
        self.assertNotIn("shell", kwargs)

    def test_ambiguous_hermes_fails_closed(self):
        home = retry.EXPECTED_REMOTE_HOME
        response = inventory_response(
            hermes_instances=[
                {"home": f"{home}/.hermes", "binary": f"{home}/.local/bin/hermes"},
                {
                    "home": f"{home}/.hermes/profiles/cal",
                    "binary": f"{home}/.hermes/bin/hermes",
                },
            ]
        )
        with self.assertRaisesRegex(retry.DeployError, "ambiguous_hermes"):
            retry.validate_inventory_response(self.config, response)

    def test_remote_hash_mismatch_stops_before_install(self):
        transport = FakeTransport(mismatch_at="verify_stage")
        result = retry.deploy_once(self.config, transport, repo_root=REPO)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stage"], "verify_staged_hashes")
        self.assertEqual(result["errors"], [{"code": "hash_mismatch"}])
        self.assertNotIn("install_files", [action for action, _ in transport.actions])
        self.assertEqual(transport.archive_installs, [])

    def test_final_remote_hash_mismatch_stops_before_launchd(self):
        transport = FakeTransport(mismatch_at="verify_final")
        result = retry.deploy_once(self.config, transport, repo_root=REPO)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stage"], "verify_remote_hashes")
        self.assertEqual(result["errors"], [{"code": "hash_mismatch"}])
        self.assertEqual(transport.archive_installs, [])

    def test_unreviewed_runtime_hash_stops_before_remote_prepare(self):
        with tempfile.TemporaryDirectory() as temporary:
            source_root = Path(temporary).resolve() / "repo"
            source_root.mkdir()
            for relative in retry.RUNTIME_FILES:
                shutil.copyfile(REPO / relative, source_root / relative)
            (source_root / "extract_codex.py").write_text("unreviewed runtime")

            transport = FakeTransport()
            result = retry.deploy_once(
                self.config,
                transport,
                repo_root=source_root,
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stage"], "snapshot_runtime")
        self.assertEqual(
            result["errors"],
            [{"code": "local_runtime_hash_mismatch"}],
        )
        self.assertEqual([action for action, _ in transport.actions], ["inventory"])
        self.assertEqual(transport.copies, [])

    def test_remote_final_hashes_are_verified_before_launchd_install(self):
        transport = FakeTransport()
        result = retry.deploy_once(self.config, transport, repo_root=REPO)

        actions = [action for action, _ in transport.actions]
        self.assertEqual(
            actions,
            ["inventory", "prepare", "verify_stage", "install_files", "verify_final"],
        )
        self.assertLess(actions.index("verify_final"), len(actions))
        self.assertEqual(result["status"], "deployed")
        self.assertEqual(len(transport.archive_installs), 1)

    def test_retry_launchd_job_and_logs_are_owner_only_and_six_hourly(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            agents = root / "LaunchAgents"
            logs = root / "Logs"
            with mock.patch.object(retry.platform, "system", return_value="Darwin"):
                result = retry.install_retry_launchd(
                    CONFIG_PATH,
                    self.config,
                    launch_agents_dir=agents,
                    logs_dir=logs,
                    load=False,
                )

            plist_path = Path(result["plist_path"])
            job = plistlib.loads(plist_path.read_bytes())
            self.assertEqual(result["interval_seconds"], 21_600)
            self.assertFalse(result["loaded"])
            self.assertTrue(job["RunAtLoad"])
            self.assertEqual(job["StartInterval"], 21_600)
            self.assertEqual(job["Umask"], 0o077)
            self.assertEqual(
                job["ProgramArguments"][-2:],
                ["--config", str(CONFIG_PATH)],
            )
            self.assertEqual(plist_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(agents.stat().st_mode & 0o777, 0o700)
            self.assertEqual(logs.stat().st_mode & 0o777, 0o700)
            for key in ("StandardOutPath", "StandardErrorPath"):
                self.assertEqual(Path(job[key]).stat().st_mode & 0o777, 0o600)

    def test_config_schema_drift_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "deploy.json"
            value = json.loads(CONFIG_PATH.read_text())
            value["runtime_files"].append("extract_all.sh")
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(retry.DeployError, "config_schema_drift"):
                retry.load_deploy_config(path)


if __name__ == "__main__":
    unittest.main()
