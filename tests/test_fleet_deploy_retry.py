import ast
import base64
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO / "configs" / "old-macbook.deploy.json"
sys.path.insert(0, str(REPO))

import fleet_deploy_retry as retry  # noqa: E402


def remote_helper_namespace():
    namespace = {}
    exec(compile(retry.REMOTE_HELPER, "<remote-helper>", "exec"), namespace)
    return namespace


def launchctl_loaded(command, target):
    return subprocess.CompletedProcess(command, 0, f"{target} = {{\n}}\n", "")


def launchctl_missing(command, target):
    domain, label = target.rsplit("/", 1)
    uid = domain.rsplit("/", 1)[-1]
    stderr = (
        f'Bad request.\nCould not find service "{label}" '
        f"in domain for user gui: {uid}\n"
    )
    return subprocess.CompletedProcess(command, 113, "", stderr)


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
        self.config = retry.load_deploy_config(CONFIG_PATH)
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
                "stage_path": retry.remote_stage_path(
                    self.config,
                    values["deployment_id"],
                ),
            }
        if action in {"verify_stage", "install_files", "verify_final"}:
            hashes = dict(values["expected_sha256"])
            if self.mismatch_at == action:
                first = next(iter(hashes))
                hashes[first] = "0" * 64
            response = {"schema_version": 1, "ok": True, "sha256": hashes}
            if action == "install_files":
                self.archive_installs.append(values)
                response["launchd"] = {
                    "label": "com.mattrotundo.ai-chat-archive.old-macbook",
                    "loaded": True,
                    "plist_path": (
                        f"{retry.EXPECTED_REMOTE_HOME}/Library/LaunchAgents/"
                        "com.mattrotundo.ai-chat-archive.old-macbook.plist"
                    ),
                }
            return response
        if action == "finish":
            return {"schema_version": 1, "ok": True, "finished": True}
        raise AssertionError(f"unexpected helper action: {action}")

    def copy_file(self, local_path, **values):
        self.copies.append((Path(local_path), values))


class FleetDeployRetryTests(unittest.TestCase):
    def setUp(self):
        self.config = retry.load_deploy_config(CONFIG_PATH)

    def prepare_remote_transaction(
        self,
        remote,
        root: Path,
        deployment_id: str,
        old_payloads: dict[str, bytes],
        new_payloads: dict[str, bytes],
    ):
        remote["identity"] = lambda _request: {
            "user": "test",
            "home": "test",
            "platform": "Darwin",
            "python_major": 3,
        }
        home = root / "home"
        repo = home / "repo"
        home.mkdir(mode=0o700, exist_ok=True)
        repo.mkdir(mode=0o700, exist_ok=True)
        for relative, payload in old_payloads.items():
            target = repo / relative
            target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            target.write_bytes(payload)
        request = {
            "expected_remote_user": "test",
            "expected_remote_home": str(home),
            "remote_repo_root": str(repo),
            "deployment_id": deployment_id,
            "relative_paths": list(new_payloads),
            "host_id": "old-macbook",
            "script_relative_path": "fleet_chat_archive.py",
            "config_relative_path": "configs/old-macbook.json",
            "interval_seconds": 21_600,
        }
        prepared = remote["prepare"](request)
        stage_path = prepared["stage_path"]
        expected_hashes = {
            relative: hashlib.sha256(payload).hexdigest()
            for relative, payload in new_payloads.items()
        }
        for relative, payload in new_payloads.items():
            remote["upload_file"](
                dict(
                    request,
                    stage_path=stage_path,
                    relative_path=relative,
                    expected_sha256=expected_hashes[relative],
                    content_b64=base64.b64encode(payload).decode("ascii"),
                )
            )
        activate_request = dict(
            request,
            stage_path=stage_path,
            expected_sha256=expected_hashes,
        )
        return home, repo, activate_request

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

        copied_relative = [values["relative_path"] for _, values in transport.copies]
        self.assertEqual(tuple(copied_relative), retry.DEPLOYED_FILES)
        self.assertEqual(set(result["files"]["sha256"]), set(retry.DEPLOYED_FILES))
        self.assertEqual(result["files"]["count"], 6)
        self.assertTrue(result["files"]["verified"])
        self.assertNotIn("--delete", json.dumps(transport.copies, default=str))
        self.assertNotIn("scp", json.dumps(transport.copies, default=str))

        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary).resolve() / "fleet_chat_archive.py"
            source.write_text("runtime")
            ssh = retry.SshTransport(self.config, runner=runner)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            deployment_id = "1" * 32
            stage_path = retry.remote_stage_path(self.config, deployment_id)
            with mock.patch.object(
                ssh,
                "helper",
                return_value={
                    "schema_version": 1,
                    "ok": True,
                    "sha256": {"fleet_chat_archive.py": digest},
                },
            ) as helper:
                ssh.copy_file(
                    source,
                    deployment_id=deployment_id,
                    stage_path=stage_path,
                    relative_path="fleet_chat_archive.py",
                    expected_sha256=digest,
                )
        action, values = helper.call_args.args
        self.assertEqual(action, "upload_file")
        self.assertEqual(base64.b64decode(values["content_b64"]), b"runtime")
        self.assertEqual(values["stage_path"], stage_path)
        self.assertEqual(calls, [])

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

    def test_every_upload_connection_reverifies_identity_and_uses_fixed_ssh_argv(self):
        commands = []

        def runner(command, **kwargs):
            commands.append((command, kwargs))
            encoded_literal = kwargs["input"].split("REQUEST_B64 = ", 1)[1].splitlines()[0]
            request = json.loads(base64.b64decode(ast.literal_eval(encoded_literal)))
            self.assertEqual(request["expected_remote_user"], retry.EXPECTED_REMOTE_USER)
            self.assertEqual(request["expected_remote_home"], retry.EXPECTED_REMOTE_HOME)
            self.assertEqual(request["action"], "upload_file")
            payload = {
                "schema_version": 1,
                "ok": True,
                "sha256": {request["relative_path"]: request["expected_sha256"]},
            }
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps(payload),
                "",
            )

        transport = retry.SshTransport(self.config, runner=runner)
        deployment_id = "2" * 32
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "fleet_chat_archive.py"
            source.write_bytes(b"runtime")
            digest = hashlib.sha256(b"runtime").hexdigest()
            transport.copy_file(
                source,
                deployment_id=deployment_id,
                stage_path=retry.remote_stage_path(self.config, deployment_id),
                relative_path="fleet_chat_archive.py",
                expected_sha256=digest,
            )

        command, kwargs = commands[0]
        self.assertEqual(command[-3:], ["oldmac", "python3", "-"])
        self.assertNotIn("scp", command)
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

    def test_final_remote_hash_mismatch_fails_after_atomic_activation(self):
        transport = FakeTransport(mismatch_at="verify_final")
        result = retry.deploy_once(self.config, transport, repo_root=REPO)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stage"], "verify_remote_hashes")
        self.assertEqual(result["errors"], [{"code": "hash_mismatch"}])
        self.assertEqual(len(transport.archive_installs), 1)

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

    def test_atomic_activation_is_verified_before_remote_cleanup(self):
        transport = FakeTransport()
        result = retry.deploy_once(self.config, transport, repo_root=REPO)

        actions = [action for action, _ in transport.actions]
        self.assertEqual(
            actions,
            [
                "inventory",
                "prepare",
                "verify_stage",
                "install_files",
                "verify_final",
                "finish",
            ],
        )
        self.assertLess(actions.index("verify_final"), len(actions))
        self.assertEqual(result["status"], "remote_deployed")
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

    def test_prepare_uses_exact_dedicated_stage_and_cleans_bounded_stale_stage(self):
        remote = remote_helper_namespace()
        remote["identity"] = lambda _request: {
            "user": "test",
            "home": "test",
            "platform": "Darwin",
            "python_major": 3,
        }
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve() / "home"
            home.mkdir(mode=0o700)
            deploy_root = home / retry.REMOTE_DEPLOY_ROOT_RELATIVE
            stages = deploy_root / "stages"
            stages.mkdir(parents=True, mode=0o700)
            stale = stages / (".fleet-deploy-" + "a" * 32)
            (stale / "configs").mkdir(parents=True, mode=0o700)
            (stale / "configs" / "old.json").write_bytes(b"stale")
            request = {
                "expected_remote_user": "test",
                "expected_remote_home": str(home),
                "remote_repo_root": str(home / "repo"),
                "deployment_id": "b" * 32,
                "relative_paths": ["one.py", "configs/old.json"],
            }

            prepared = remote["prepare"](request)
            expected_stage = stages / (".fleet-deploy-" + "b" * 32)

            self.assertEqual(prepared["stage_path"], str(expected_stage))
            self.assertFalse(stale.exists())
            self.assertTrue(expected_stage.is_dir())
            self.assertEqual(expected_stage.stat().st_mode & 0o777, 0o700)
            self.assertFalse(str(expected_stage).startswith(str(home / "repo")))

            competing_request = dict(request, deployment_id="d" * 32)
            with self.assertRaisesRegex(
                remote["ControlledFailure"], "deployment_locked"
            ):
                remote["prepare"](competing_request)
            self.assertFalse(
                (stages / (".fleet-deploy-" + "d" * 32)).exists()
            )

            staged_config_parent = expected_stage / "configs"
            staged_config_parent.rmdir()
            staged_config_parent.symlink_to(home / "repo", target_is_directory=True)
            with self.assertRaises(OSError):
                remote["upload_file"](
                    dict(
                        request,
                        stage_path=str(expected_stage),
                        relative_path="configs/old.json",
                        expected_sha256=hashlib.sha256(b"escape").hexdigest(),
                        content_b64=base64.b64encode(b"escape").decode("ascii"),
                    )
                )
            self.assertFalse((home / "repo" / "old.json").exists())
            staged_config_parent.unlink()
            staged_config_parent.mkdir(mode=0o700)

            unsafe_request = dict(request, stage_path=str(home / "repo"))
            with self.assertRaisesRegex(remote["ControlledFailure"], "unsafe_path"):
                remote["verify_stage"](
                    dict(unsafe_request, expected_sha256={"one.py": "0" * 64})
                )

            finished = remote["finish"](
                dict(request, stage_path=str(expected_stage), abort=True)
            )
            self.assertTrue(finished["finished"])
            self.assertFalse(expected_stage.exists())
            self.assertFalse((deploy_root / "active.json").exists())

            for index in range(remote["MAX_STALE_STAGES"] + 1):
                stale_id = f"{index:032x}"
                (stages / f".fleet-deploy-{stale_id}").mkdir(mode=0o700)
            stages_descriptor = os.open(stages, remote["DIRECTORY_FLAGS"])
            try:
                with self.assertRaisesRegex(
                    remote["ControlledFailure"], "stale_stage_limit"
                ):
                    remote["cleanup_stages"](stages_descriptor)
            finally:
                os.close(stages_descriptor)
            self.assertEqual(
                len(list(stages.iterdir())),
                remote["MAX_STALE_STAGES"] + 1,
            )

    def test_remote_install_rolls_back_every_replacement_and_restores_job(self):
        remote = remote_helper_namespace()
        remote["identity"] = lambda _request: {
            "user": "test",
            "home": "test",
            "platform": "Darwin",
            "python_major": 3,
        }
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve() / "home"
            repo = home / "repo"
            home.mkdir(mode=0o700)
            repo.mkdir(mode=0o700)
            old_payloads = {"a.py": b"old-a", "b.py": b"old-b"}
            new_payloads = {"a.py": b"new-a", "b.py": b"new-b"}
            for relative, payload in old_payloads.items():
                (repo / relative).write_bytes(payload)
            deployment_id = "c" * 32
            request = {
                "expected_remote_user": "test",
                "expected_remote_home": str(home),
                "remote_repo_root": str(repo),
                "deployment_id": deployment_id,
                "relative_paths": list(new_payloads),
            }
            prepared = remote["prepare"](request)
            stage_path = prepared["stage_path"]
            expected_hashes = {
                relative: hashlib.sha256(payload).hexdigest()
                for relative, payload in new_payloads.items()
            }
            for relative, payload in new_payloads.items():
                remote["upload_file"](
                    dict(
                        request,
                        stage_path=stage_path,
                        relative_path=relative,
                        expected_sha256=expected_hashes[relative],
                        content_b64=base64.b64encode(payload).decode("ascii"),
                    )
                )

            remote["snapshot_archive_state"] = lambda _request: {
                "enabled": True,
                "loaded": True,
            }
            remote["quiesce_archive"] = lambda _request: None
            restored_jobs = []
            remote["restore_archive_state"] = (
                lambda _request, state: restored_jobs.append(dict(state))
            )
            remote["install_archive_job"] = lambda _request, _repo: {
                "label": "com.mattrotundo.ai-chat-archive.old-macbook",
                "loaded": True,
                "plist_path": str(home / "Library/LaunchAgents/archive.plist"),
            }
            real_replace = os.replace
            target_replacements = 0

            def fail_second_replacement(source, target, *args, **kwargs):
                nonlocal target_replacements
                result = real_replace(source, target, *args, **kwargs)
                destination_descriptor = kwargs.get("dst_dir_fd")
                if (
                    target in old_payloads
                    and destination_descriptor is not None
                    and os.fstat(destination_descriptor).st_ino == repo.stat().st_ino
                ):
                    target_replacements += 1
                    if target_replacements == 2:
                        raise OSError("injected replacement failure")
                return result

            activate_request = dict(
                request,
                stage_path=stage_path,
                expected_sha256=expected_hashes,
                host_id="old-macbook",
                script_relative_path="fleet_chat_archive.py",
                config_relative_path="configs/old-macbook.json",
                interval_seconds=21_600,
            )
            with mock.patch.object(remote["os"], "replace", fail_second_replacement):
                with self.assertRaisesRegex(
                    remote["ControlledFailure"], "install_failed"
                ):
                    remote["install_files"](activate_request)

            self.assertEqual(
                {relative: (repo / relative).read_bytes() for relative in old_payloads},
                old_payloads,
            )
            self.assertEqual(
                restored_jobs,
                [{"enabled": True, "loaded": True}],
            )
            remote["finish"](dict(activate_request, abort=True))
            self.assertFalse(Path(stage_path).exists())

    def test_archive_activation_boots_out_before_mutation_and_print_verifies_loaded(self):
        remote = remote_helper_namespace()
        calls = []

        def fake_launchctl(arguments):
            calls.append(arguments)
            if arguments[0] == "print":
                return launchctl_missing(arguments, arguments[1])
            return subprocess.CompletedProcess(arguments, 0, "", "")

        remote["launchctl"] = fake_launchctl
        with mock.patch.object(remote["os"], "getuid", return_value=501):
            self.assertIsNone(remote["quiesce_archive"]({"host_id": "old-macbook"}))
        target = "gui/501/com.mattrotundo.ai-chat-archive.old-macbook"
        self.assertEqual(
            calls,
            [
                ["disable", target],
                ["bootout", target],
                ["print", target],
            ],
        )

        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            repo = home / "repo"
            repo.mkdir()
            (repo / "fleet_chat_archive.py").write_bytes(b"runtime")
            (repo / "configs").mkdir()
            (repo / "configs" / "old-macbook.json").write_bytes(b"{}")
            label = "com.mattrotundo.ai-chat-archive.old-macbook"
            plist = home / "Library/LaunchAgents" / f"{label}.plist"
            subprocess_calls = []

            def fake_run(command, **_kwargs):
                subprocess_calls.append(command)
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps(
                        {"label": label, "loaded": True, "plist_path": str(plist)}
                    ),
                    "",
                )

            print_calls = []
            remote["subprocess"] = types.SimpleNamespace(run=fake_run)
            remote["launchctl"] = lambda arguments: print_calls.append(
                arguments
            ) or launchctl_loaded(arguments, arguments[1])
            request = {
                "expected_remote_home": str(home),
                "host_id": "old-macbook",
                "script_relative_path": "fleet_chat_archive.py",
                "config_relative_path": "configs/old-macbook.json",
                "interval_seconds": 21_600,
            }
            launchd = remote["install_archive_job"](request, repo)
            self.assertTrue(launchd["loaded"])
            self.assertEqual(print_calls, [["print", f"gui/{os.getuid()}/{label}"]])
            self.assertEqual(len(subprocess_calls), 1)
            remote["launchctl"] = lambda arguments: launchctl_missing(
                arguments, arguments[1]
            )
            with self.assertRaisesRegex(
                remote["ControlledFailure"], "archive_launchd_failed"
            ):
                remote["install_archive_job"](request, repo)

    def test_remote_launchctl_print_classification_is_strict_and_accepts_canonical_absence(self):
        request = {"host_id": "old-macbook"}
        label = "com.mattrotundo.ai-chat-archive.old-macbook"
        target = f"gui/501/{label}"
        cases = (
            (
                "unexpected_rc",
                lambda command: subprocess.CompletedProcess(
                    command, 70, "", "I/O error\n"
                ),
            ),
            (
                "malformed_success",
                lambda command: subprocess.CompletedProcess(
                    command, 0, "gui/501/wrong-label = {\n}\n", ""
                ),
            ),
            (
                "timeout",
                lambda command: (_ for _ in ()).throw(
                    subprocess.TimeoutExpired(command, 120)
                ),
            ),
        )
        for name, result in cases:
            with self.subTest(name=name):
                remote = remote_helper_namespace()
                calls = []

                def launchctl(arguments):
                    calls.append(list(arguments))
                    return result(arguments)

                remote["launchctl"] = launchctl
                with mock.patch.object(remote["os"], "getuid", return_value=501):
                    with self.assertRaisesRegex(
                        remote["ControlledFailure"], "archive_launchd_failed"
                    ):
                        remote["snapshot_archive_state"](request)
                self.assertEqual(calls, [["print", target]])

        remote = remote_helper_namespace()
        calls = []

        def canonically_missing(arguments):
            calls.append(list(arguments))
            if arguments[0] == "print":
                return launchctl_missing(arguments, arguments[1])
            return subprocess.CompletedProcess(
                arguments, 0, f'"{label}" => false\n', ""
            )

        remote["launchctl"] = canonically_missing
        with mock.patch.object(remote["os"], "getuid", return_value=501):
            self.assertEqual(
                remote["snapshot_archive_state"](request),
                {"enabled": True, "loaded": False},
            )
        self.assertEqual(
            calls,
            [["print", target], ["print-disabled", "gui/501"]],
        )

    def test_hard_exit_after_first_rename_is_recovered_from_persistent_journal(self):
        remote = remote_helper_namespace()
        old_payloads = {"a.py": b"old-a", "b.py": b"old-b"}
        new_payloads = {"a.py": b"new-a", "b.py": b"new-b"}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            home, repo, activate_request = self.prepare_remote_transaction(
                remote,
                root,
                "1" * 32,
                old_payloads,
                new_payloads,
            )
            child = f"""
import os
import sys
sys.path.insert(0, {str(REPO)!r})
import fleet_deploy_retry as retry
remote = {{}}
exec(compile(retry.REMOTE_HELPER, '<remote-helper>', 'exec'), remote)
remote['identity'] = lambda _request: {{'user': 'test', 'home': 'test', 'platform': 'Darwin', 'python_major': 3}}
remote['snapshot_archive_state'] = lambda _request: {{'enabled': True, 'loaded': True}}
remote['quiesce_archive'] = lambda _request: None
request = {activate_request!r}
repo_inode = os.stat(request['remote_repo_root']).st_ino
real_replace = remote['os'].replace
def crash_after_runtime_rename(source, target, *args, **kwargs):
    result = real_replace(source, target, *args, **kwargs)
    destination_descriptor = kwargs.get('dst_dir_fd')
    if target == 'a.py' and destination_descriptor is not None and os.fstat(destination_descriptor).st_ino == repo_inode:
        os._exit(73)
    return result
remote['os'].replace = crash_after_runtime_rename
remote['install_files'](request)
"""
            completed = subprocess.run(
                [sys.executable, "-c", child],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 73, completed.stderr)
            self.assertEqual((repo / "a.py").read_bytes(), b"new-a")
            self.assertEqual((repo / "b.py").read_bytes(), b"old-b")

            stage_path = Path(activate_request["stage_path"])
            journal_path = stage_path / ".transaction" / "journal.json"
            backups = stage_path / ".transaction" / "backups"
            self.assertEqual(json.loads(journal_path.read_text())["phase"], "activating")
            self.assertEqual((backups / "a.py").read_bytes(), b"old-a")
            self.assertEqual((backups / "b.py").read_bytes(), b"old-b")

            lock_path = home / retry.REMOTE_DEPLOY_ROOT_RELATIVE / "active.json"
            stale_time = time.time() - remote["STALE_LOCK_SECONDS"] - 60
            os.utime(lock_path, (stale_time, stale_time))
            restored_states = []
            remote["quiesce_archive"] = lambda _request: None
            remote["restore_archive_state"] = (
                lambda _request, state: restored_states.append(dict(state))
            )
            next_request = dict(
                activate_request,
                deployment_id="2" * 32,
            )
            next_request.pop("stage_path")
            next_request.pop("expected_sha256")
            prepared = remote["prepare"](next_request)

            self.assertEqual(
                {relative: (repo / relative).read_bytes() for relative in old_payloads},
                old_payloads,
            )
            self.assertEqual(restored_states, [{"enabled": True, "loaded": True}])
            self.assertFalse(stage_path.exists())
            remote["finish"](
                dict(next_request, stage_path=prepared["stage_path"], abort=True)
            )

    def test_stale_ambiguous_and_rollback_failed_transactions_remain_blocked(self):
        for state, expected_error in (
            ("prepared", "recovery_required"),
            ("rollback_failed", "rollback_failed"),
        ):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temporary:
                remote = remote_helper_namespace()
                old_payloads = {"a.py": b"old"}
                new_payloads = {"a.py": b"new"}
                root = Path(temporary).resolve()
                home, _repo, activate_request = self.prepare_remote_transaction(
                    remote,
                    root,
                    "3" * 32,
                    old_payloads,
                    new_payloads,
                )
                stage_path = Path(activate_request["stage_path"])
                (stage_path / ".transaction" / "backups").mkdir(
                    parents=True, mode=0o700
                )
                lock_path = home / retry.REMOTE_DEPLOY_ROOT_RELATIVE / "active.json"
                lock = json.loads(lock_path.read_text())
                lock["state"] = state
                lock_path.write_text(json.dumps(lock))
                os.chmod(lock_path, 0o600)
                stale_time = time.time() - remote["STALE_LOCK_SECONDS"] - 60
                os.utime(lock_path, (stale_time, stale_time))
                next_request = dict(
                    activate_request,
                    deployment_id="4" * 32,
                )
                next_request.pop("stage_path")
                next_request.pop("expected_sha256")
                with self.assertRaisesRegex(
                    remote["ControlledFailure"], expected_error
                ):
                    remote["prepare"](next_request)
                self.assertTrue(stage_path.exists())
                self.assertTrue(lock_path.exists())

    def test_post_activation_tamper_rolls_back_before_abort_cleanup(self):
        remote = remote_helper_namespace()
        old_payloads = {"a.py": b"old-a", "b.py": b"old-b"}
        new_payloads = {"a.py": b"new-a", "b.py": b"new-b"}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _home, repo, request = self.prepare_remote_transaction(
                remote,
                root,
                "5" * 32,
                old_payloads,
                new_payloads,
            )
            prior_state = {"enabled": False, "loaded": True}
            states = iter((prior_state, {"enabled": True, "loaded": True}))
            remote["snapshot_archive_state"] = lambda _request: dict(next(states))
            remote["quiesce_archive"] = lambda _request: None
            restored = []
            remote["restore_archive_state"] = (
                lambda _request, state: restored.append(dict(state))
            )
            remote["install_archive_job"] = lambda _request, _repo: {
                "label": "com.mattrotundo.ai-chat-archive.old-macbook",
                "loaded": True,
                "plist_path": str(root / "archive.plist"),
            }
            installed = remote["install_files"](request)
            self.assertEqual(installed["sha256"], request["expected_sha256"])
            journal_path = Path(request["stage_path"]) / ".transaction" / "journal.json"
            self.assertEqual(json.loads(journal_path.read_text())["phase"], "launched")

            (repo / "a.py").write_bytes(b"tampered")
            with self.assertRaisesRegex(remote["ControlledFailure"], "hash_mismatch"):
                remote["verify_final"](request)
            self.assertEqual(
                {relative: (repo / relative).read_bytes() for relative in old_payloads},
                old_payloads,
            )
            self.assertEqual(json.loads(journal_path.read_text())["phase"], "rolled_back")
            self.assertEqual(restored, [prior_state])
            remote["finish"](dict(request, abort=True))
            self.assertFalse(Path(request["stage_path"]).exists())

    def test_verified_backups_survive_until_commit_and_abort_restores_before_cleanup(self):
        for finish_mode in ("commit", "abort"):
            with self.subTest(finish_mode=finish_mode), tempfile.TemporaryDirectory() as temporary:
                remote = remote_helper_namespace()
                root = Path(temporary).resolve()
                old_payloads = {"nested/a.py": b"old-a"}
                new_payloads = {"nested/a.py": b"new-a"}
                home, repo, request = self.prepare_remote_transaction(
                    remote,
                    root,
                    ("7" if finish_mode == "commit" else "8") * 32,
                    old_payloads,
                    new_payloads,
                )
                prior_state = {"enabled": True, "loaded": False}
                observed_states = iter(
                    (prior_state, {"enabled": True, "loaded": True})
                )
                remote["snapshot_archive_state"] = (
                    lambda _request: dict(next(observed_states))
                )
                remote["quiesce_archive"] = lambda _request: None
                restored = []
                remote["restore_archive_state"] = (
                    lambda _request, state: restored.append(dict(state))
                )
                remote["install_archive_job"] = lambda _request, _repo: {
                    "label": "com.mattrotundo.ai-chat-archive.old-macbook",
                    "loaded": True,
                    "plist_path": str(root / "archive.plist"),
                }
                remote["install_files"](request)
                if finish_mode == "commit":
                    remote["verify_final"](request)
                    transaction = Path(request["stage_path"]) / ".transaction"
                    self.assertEqual(
                        json.loads((transaction / "journal.json").read_text())["phase"],
                        "verified",
                    )
                    self.assertEqual(
                        (transaction / "backups" / "nested" / "a.py").read_bytes(),
                        b"old-a",
                    )
                    remote["finish"](dict(request, commit=True))
                    self.assertEqual((repo / "nested/a.py").read_bytes(), b"new-a")
                    self.assertEqual(restored, [])
                else:
                    remote["finish"](dict(request, abort=True))
                    self.assertEqual((repo / "nested/a.py").read_bytes(), b"old-a")
                    self.assertEqual(restored, [prior_state])
                self.assertFalse(Path(request["stage_path"]).exists())
                self.assertFalse(
                    (home / retry.REMOTE_DEPLOY_ROOT_RELATIVE / "active.json").exists()
                )

    def test_abort_removes_target_parent_directories_created_by_activation(self):
        remote = remote_helper_namespace()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _home, repo, request = self.prepare_remote_transaction(
                remote,
                root,
                "9" * 32,
                {},
                {"created/deep/a.py": b"new-a"},
            )
            remote["snapshot_archive_state"] = lambda _request: {
                "enabled": True,
                "loaded": False,
            }
            remote["quiesce_archive"] = lambda _request: None
            remote["restore_archive_state"] = lambda _request, _state: None
            remote["install_archive_job"] = lambda _request, _repo: {
                "label": "com.mattrotundo.ai-chat-archive.old-macbook",
                "loaded": True,
                "plist_path": str(root / "archive.plist"),
            }
            remote["install_files"](request)
            self.assertTrue((repo / "created/deep/a.py").exists())
            remote["finish"](dict(request, abort=True))
            self.assertFalse((repo / "created").exists())

    def test_keyboard_interrupt_after_archive_bootout_restores_state_and_print_proves_it(self):
        remote = remote_helper_namespace()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            old_payloads = {"a.py": b"old-a"}
            new_payloads = {"a.py": b"new-a"}
            _home, repo, request = self.prepare_remote_transaction(
                remote,
                root,
                "6" * 32,
                old_payloads,
                new_payloads,
            )
            state = {"enabled": True, "loaded": True}
            calls = []
            bootouts = 0
            label = "com.mattrotundo.ai-chat-archive.old-macbook"

            def launchctl(arguments):
                nonlocal bootouts
                calls.append(list(arguments))
                action = arguments[0]
                if action == "print":
                    return (
                        launchctl_loaded(arguments, arguments[1])
                        if state["loaded"]
                        else launchctl_missing(arguments, arguments[1])
                    )
                if action == "print-disabled":
                    return subprocess.CompletedProcess(
                        arguments,
                        0,
                        f'"{label}" => {str(not state["enabled"]).lower()}\n',
                        "",
                    )
                if action == "disable":
                    state["enabled"] = False
                elif action == "enable":
                    state["enabled"] = True
                elif action == "bootout":
                    state["loaded"] = False
                    bootouts += 1
                    if bootouts == 1:
                        raise KeyboardInterrupt("after bootout")
                return subprocess.CompletedProcess(arguments, 0, "", "")

            remote["launchctl"] = launchctl

            def install_archive_job(_request, _repo):
                state.update(enabled=True, loaded=True)
                return {
                    "label": label,
                    "loaded": True,
                    "plist_path": str(root / "archive.plist"),
                }

            remote["install_archive_job"] = install_archive_job
            with self.assertRaises(KeyboardInterrupt):
                remote["install_files"](request)
            self.assertEqual((repo / "a.py").read_bytes(), b"old-a")
            self.assertEqual(state, {"enabled": True, "loaded": True})
            self.assertEqual(calls[-2][0], "print")
            self.assertEqual(calls[-1][0], "print-disabled")
            journal_path = Path(request["stage_path"]) / ".transaction" / "journal.json"
            self.assertEqual(json.loads(journal_path.read_text())["phase"], "rolled_back")

    def test_retry_launchd_install_failures_restore_prior_plist_and_state(self):
        for failure in ("bootstrap", "print", "print-disabled"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                agents = root / "LaunchAgents"
                agents.mkdir(mode=0o700)
                plist_path = agents / f"{retry.RETRY_LAUNCHD_LABEL}.plist"
                old_plist = b"old retry plist"
                plist_path.write_bytes(old_plist)
                os.chmod(plist_path, 0o600)
                state = {"enabled": False, "loaded": True}
                counts = {"bootstrap": 0, "print": 0, "print-disabled": 0}

                def runner(command, **_kwargs):
                    action = command[1]
                    if action in counts:
                        counts[action] += 1
                    if action == "print":
                        if failure == "print" and counts[action] == 2:
                            return subprocess.CompletedProcess(command, 70, "", "proof failed")
                        return (
                            launchctl_loaded(command, command[2])
                            if state["loaded"]
                            else launchctl_missing(command, command[2])
                        )
                    if action == "print-disabled":
                        if failure == "print-disabled" and counts[action] == 2:
                            return subprocess.CompletedProcess(command, 70, "", "proof failed")
                        stdout = (
                            f'"{retry.RETRY_LAUNCHD_LABEL}" => '
                            f'{str(not state["enabled"]).lower()}\n'
                        )
                        return subprocess.CompletedProcess(command, 0, stdout, "")
                    if action == "bootout":
                        state["loaded"] = False
                    elif action == "enable":
                        state["enabled"] = True
                    elif action == "disable":
                        state["enabled"] = False
                    elif action == "bootstrap":
                        if failure == "bootstrap" and counts[action] == 1:
                            return subprocess.CompletedProcess(command, 70, "", "failed")
                        state["loaded"] = True
                    return subprocess.CompletedProcess(command, 0, "", "")

                with mock.patch.object(retry.platform, "system", return_value="Darwin"):
                    with self.assertRaisesRegex(
                        retry.DeployError, "retry_launchd_failed"
                    ):
                        retry.install_retry_launchd(
                            CONFIG_PATH,
                            self.config,
                            launch_agents_dir=agents,
                            logs_dir=root / "Logs",
                            load=True,
                            runner=runner,
                        )
                self.assertEqual(plist_path.read_bytes(), old_plist)
                self.assertEqual(plist_path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(state, {"enabled": False, "loaded": True})
                self.assertGreaterEqual(counts["print"], 3)
                self.assertGreaterEqual(counts["print-disabled"], 2)

    def test_local_initial_print_failure_precedes_all_mutation(self):
        for operation in ("install", "disable"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                agents = root / "LaunchAgents"
                agents.mkdir(mode=0o700)
                plist_path = agents / f"{retry.RETRY_LAUNCHD_LABEL}.plist"
                prior_plist = b"prior retry plist"
                plist_path.write_bytes(prior_plist)
                os.chmod(plist_path, 0o600)
                logs = root / "Logs"
                commands = []

                def runner(command, **_kwargs):
                    commands.append(command)
                    return subprocess.CompletedProcess(
                        command, 70, "", "I/O error\n"
                    )

                with mock.patch.object(retry.platform, "system", return_value="Darwin"):
                    with self.assertRaisesRegex(
                        retry.DeployError, "retry_launchd_failed"
                    ):
                        if operation == "install":
                            retry.install_retry_launchd(
                                CONFIG_PATH,
                                self.config,
                                launch_agents_dir=agents,
                                logs_dir=logs,
                                load=True,
                                runner=runner,
                            )
                        else:
                            retry.disable_retry_launchd(
                                launch_agents_dir=agents,
                                runner=runner,
                            )
                self.assertEqual([command[1] for command in commands], ["print"])
                self.assertEqual(plist_path.read_bytes(), prior_plist)
                self.assertFalse(logs.exists())

    def test_local_launchctl_print_classification_rejects_malformed_and_accepts_absence(self):
        domain = f"gui/{os.getuid()}"
        label = retry.RETRY_LAUNCHD_LABEL
        target = f"{domain}/{label}"
        for name, result in (
            (
                "malformed_success",
                subprocess.CompletedProcess(
                    ["launchctl", "print", target],
                    0,
                    f"{domain}/wrong-label = {{\n}}\n",
                    "",
                ),
            ),
            (
                "unexpected_rc",
                subprocess.CompletedProcess(
                    ["launchctl", "print", target], 70, "", "I/O error\n"
                ),
            ),
        ):
            with self.subTest(name=name):
                commands = []

                def runner(command, **_kwargs):
                    commands.append(command)
                    return result

                with self.assertRaisesRegex(retry.DeployError, "retry_launchd_failed"):
                    retry.retry_launchd_state(runner, domain, label)
                self.assertEqual([command[1] for command in commands], ["print"])

        commands = []

        def missing_runner(command, **_kwargs):
            commands.append(command)
            if command[1] == "print":
                return launchctl_missing(command, command[2])
            return subprocess.CompletedProcess(
                command, 0, f'"{label}" => false\n', ""
            )

        self.assertEqual(
            retry.retry_launchd_state(missing_runner, domain, label),
            {"enabled": True, "loaded": False},
        )
        self.assertEqual(
            [command[1] for command in commands],
            ["print", "print-disabled"],
        )

        timeout_commands = []

        def timeout_runner(command, **_kwargs):
            timeout_commands.append(command)
            raise subprocess.TimeoutExpired(command, 120)

        with self.assertRaisesRegex(retry.DeployError, "retry_launchd_failed"):
            retry.retry_launchd_state(timeout_runner, domain, label)
        self.assertEqual([command[1] for command in timeout_commands], ["print"])

    def test_retry_launchd_self_disable_print_proof_failure_restores_prior_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            agents = root / "LaunchAgents"
            agents.mkdir(mode=0o700)
            plist_path = agents / f"{retry.RETRY_LAUNCHD_LABEL}.plist"
            old_plist = b"old retry plist"
            plist_path.write_bytes(old_plist)
            os.chmod(plist_path, 0o600)
            state = {"enabled": True, "loaded": True}
            print_disabled_count = 0

            def runner(command, **_kwargs):
                nonlocal print_disabled_count
                action = command[1]
                if action == "print":
                    return (
                        launchctl_loaded(command, command[2])
                        if state["loaded"]
                        else launchctl_missing(command, command[2])
                    )
                if action == "print-disabled":
                    print_disabled_count += 1
                    if print_disabled_count == 2:
                        return subprocess.CompletedProcess(command, 70, "", "proof failed")
                    stdout = (
                        f'"{retry.RETRY_LAUNCHD_LABEL}" => '
                        f'{str(not state["enabled"]).lower()}\n'
                    )
                    return subprocess.CompletedProcess(command, 0, stdout, "")
                if action == "disable":
                    state["enabled"] = False
                elif action == "enable":
                    state["enabled"] = True
                elif action == "bootout":
                    state["loaded"] = False
                elif action == "bootstrap":
                    state["loaded"] = True
                return subprocess.CompletedProcess(command, 0, "", "")

            with mock.patch.object(retry.platform, "system", return_value="Darwin"):
                with self.assertRaisesRegex(retry.DeployError, "retry_launchd_failed"):
                    retry.disable_retry_launchd(
                        launch_agents_dir=agents,
                        runner=runner,
                    )
            self.assertEqual(plist_path.read_bytes(), old_plist)
            self.assertEqual(state, {"enabled": True, "loaded": True})
            self.assertEqual(print_disabled_count, 3)

    def test_retry_launchd_bootstrap_and_self_disable_are_both_print_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            commands = []
            state = {"enabled": True, "loaded": False}

            def install_runner(command, **_kwargs):
                commands.append(command)
                action = command[1]
                if action == "print":
                    return (
                        launchctl_loaded(command, command[2])
                        if state["loaded"]
                        else launchctl_missing(command, command[2])
                    )
                if action == "print-disabled":
                    stdout = (
                        f'"{retry.RETRY_LAUNCHD_LABEL}" => '
                        f'{str(not state["enabled"]).lower()}\n'
                    )
                    return subprocess.CompletedProcess(command, 0, stdout, "")
                if action == "enable":
                    state["enabled"] = True
                elif action == "bootout":
                    state["loaded"] = False
                elif action == "bootstrap":
                    state["loaded"] = True
                return subprocess.CompletedProcess(command, 0, "", "")

            with mock.patch.object(retry.platform, "system", return_value="Darwin"):
                result = retry.install_retry_launchd(
                    CONFIG_PATH,
                    self.config,
                    launch_agents_dir=root / "LaunchAgents",
                    logs_dir=root / "Logs",
                    load=True,
                    runner=install_runner,
                )
            self.assertTrue(result["loaded"])
            self.assertEqual(
                [command[1] for command in commands],
                [
                    "print",
                    "print-disabled",
                    "bootout",
                    "enable",
                    "bootstrap",
                    "print",
                    "print-disabled",
                ],
            )

            unverified_state = {"enabled": True, "loaded": False}

            def unverified_runner(command, **_kwargs):
                action = command[1]
                if action == "print":
                    return launchctl_missing(command, command[2])
                if action == "print-disabled":
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        f'"{retry.RETRY_LAUNCHD_LABEL}" => false\n',
                        "",
                    )
                if action == "enable":
                    unverified_state["enabled"] = True
                return subprocess.CompletedProcess(command, 0, "", "")

            with mock.patch.object(retry.platform, "system", return_value="Darwin"):
                with self.assertRaisesRegex(retry.DeployError, "retry_launchd_failed"):
                    retry.install_retry_launchd(
                        CONFIG_PATH,
                        self.config,
                        launch_agents_dir=root / "OtherLaunchAgents",
                        logs_dir=root / "OtherLogs",
                        load=True,
                        runner=unverified_runner,
                    )

        with tempfile.TemporaryDirectory() as temporary:
            disable_commands = []
            disable_state = {"enabled": True, "loaded": True}

            def disable_runner(command, **_kwargs):
                disable_commands.append(command)
                action = command[1]
                if action == "print":
                    return (
                        launchctl_loaded(command, command[2])
                        if disable_state["loaded"]
                        else launchctl_missing(command, command[2])
                    )
                if action == "print-disabled":
                    stdout = (
                        f'"{retry.RETRY_LAUNCHD_LABEL}" => '
                        f'{str(not disable_state["enabled"]).lower()}\n'
                    )
                    return subprocess.CompletedProcess(command, 0, stdout, "")
                if action == "disable":
                    disable_state["enabled"] = False
                elif action == "bootout":
                    disable_state["loaded"] = False
                return subprocess.CompletedProcess(command, 0, "", "")

            with mock.patch.object(retry.platform, "system", return_value="Darwin"):
                disabled = retry.disable_retry_launchd(
                    launch_agents_dir=Path(temporary).resolve() / "LaunchAgents",
                    runner=disable_runner,
                )
        self.assertEqual(
            [command[1] for command in disable_commands],
            [
                "print",
                "print-disabled",
                "disable",
                "bootout",
                "print",
                "print-disabled",
            ],
        )
        self.assertEqual(
            disabled,
            {
                "label": retry.RETRY_LAUNCHD_LABEL,
                "disabled": True,
                "loaded": False,
                "recurring": False,
            },
        )

    def test_cli_reports_deployed_only_after_verified_retry_unload(self):
        remote_result = {
            "schema_version": 1,
            "host_id": "old-macbook",
            "status": "remote_deployed",
            "retryable": False,
            "stage": "completed",
            "inventory": {key: False for key in retry.INVENTORY_HARNESSES},
            "files": {"count": 6, "verified": True, "sha256": {}},
            "archive_launchd": {
                "installed": True,
                "loaded": True,
                "interval_seconds": 21_600,
            },
            "errors": [],
        }
        disabled = {
            "label": retry.RETRY_LAUNCHD_LABEL,
            "disabled": True,
            "loaded": False,
            "recurring": False,
        }
        with (
            mock.patch.object(
                sys,
                "argv",
                ["fleet_deploy_retry.py", "run", "--config", str(CONFIG_PATH)],
            ),
            mock.patch.object(retry, "deploy_once", return_value=dict(remote_result)),
            mock.patch.object(retry, "disable_retry_launchd", return_value=disabled),
            mock.patch("builtins.print") as printed,
        ):
            self.assertEqual(retry.main(), 0)
        receipt = json.loads(printed.call_args.args[0])
        self.assertEqual(receipt["status"], "deployed")
        self.assertEqual(receipt["retry_launchd"], disabled)

        with (
            mock.patch.object(
                sys,
                "argv",
                ["fleet_deploy_retry.py", "run", "--config", str(CONFIG_PATH)],
            ),
            mock.patch.object(retry, "deploy_once", return_value=dict(remote_result)),
            mock.patch.object(
                retry,
                "disable_retry_launchd",
                side_effect=retry.DeployError("retry_launchd_failed"),
            ),
            mock.patch("builtins.print") as printed,
        ):
            self.assertEqual(retry.main(), 1)
        receipt = json.loads(printed.call_args.args[0])
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["stage"], "disable_retry_launchd")

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
