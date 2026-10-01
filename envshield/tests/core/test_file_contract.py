# envshield/tests/core/test_file_contract.py
"""
Shared system schema, Phase 3: the physical-file contract. When several
services' local_file (or example_file) resolve to the same physical file,
operations on that file use the union of their projections -- a file-level
contract that never changes what any one service is granted.
"""

import pytest
from typer.testing import CliRunner

from envshield.cli import app
from envshield.config import manager as config_manager
from envshield.core import doctor, schema_manager, setup_manager
from envshield.core.exceptions import FileContractConflictError, ServiceConfigError

runner = CliRunner()

SCHEMA = """
[LOG_LEVEL]
defaultValue = "info"

[API_TOKEN]
secret = true
services = ["api"]

[QUEUE_URL]
services = ["worker"]
"""


def _project(tmp_path, monkeypatch, yml, schema=SCHEMA):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "envshield.yml").write_text(yml)
    (tmp_path / "env.schema.toml").write_text(schema)
    for d in ("api", "worker", "app"):
        (tmp_path / d).mkdir(exist_ok=True)


# api and worker share '.env' (explicit local_file), but not '.env.example'.
SHARED_LOCAL = (
    "services:\n"
    "  api:\n    schema: env.schema.toml\n    dir: api\n    local_file: .env\n"
    "  worker:\n    schema: env.schema.toml\n    dir: worker\n    local_file: .env\n"
)
# Same schema, separate directories -> separate files.
SEPARATE = (
    "services:\n"
    "  api:\n    schema: env.schema.toml\n    dir: api\n"
    "  worker:\n    schema: env.schema.toml\n    dir: worker\n"
)
# Both files shared, through explicit overrides. (Before Phase 6 this was
# expressed as two services with the same `dir`; that topology is now
# invalid -- BL-137 -- so sharing a physical file is always explicit.)
SAME_DIR = (
    "services:\n"
    "  api:\n    schema: env.schema.toml\n    dir: api\n"
    "    local_file: app/.env\n    example_file: app/.env.example\n"
    "  worker:\n    schema: env.schema.toml\n    dir: worker\n"
    "    local_file: app/.env\n    example_file: app/.env.example\n"
)


class TestPeersAndUnion:
    def test_shared_local_file_is_the_union_of_both_projections(
        self, tmp_path, monkeypatch
    ):
        _project(tmp_path, monkeypatch, SHARED_LOCAL)
        assert config_manager.get_file_peers("api", "local_file") == ["api", "worker"]
        contract = config_manager.load_file_contract("api", "local_file")
        assert set(contract) == {"LOG_LEVEL", "API_TOKEN", "QUEUE_URL"}
        assert contract == config_manager.load_file_contract("worker", "local_file")

    def test_an_override_can_share_another_services_default_file(
        self, tmp_path, monkeypatch
    ):
        # Coverage SAME_DIR used to give implicitly: a default-path file
        # (api's app/.env) takes part in peer matching like an override.
        _project(
            tmp_path,
            monkeypatch,
            "services:\n"
            "  api:\n    schema: env.schema.toml\n    dir: app\n"
            "  worker:\n    schema: env.schema.toml\n    dir: worker\n"
            "    local_file: ./app/.env\n    example_file: app/.env.example\n",
        )
        for key in ("local_file", "example_file"):
            assert config_manager.get_file_peers("api", key) == ["api", "worker"]
            assert set(config_manager.load_file_contract("api", key)) == {
                "LOG_LEVEL",
                "API_TOKEN",
                "QUEUE_URL",
            }

    def test_separate_files_are_not_unioned(self, tmp_path, monkeypatch):
        _project(tmp_path, monkeypatch, SEPARATE)
        for key in ("local_file", "example_file"):
            assert config_manager.get_file_peers("api", key) == ["api"]
            assert config_manager.load_file_contract(
                "api", key
            ) == config_manager.load_schema("api")

    def test_local_and_example_peers_resolve_independently(self, tmp_path, monkeypatch):
        _project(tmp_path, monkeypatch, SHARED_LOCAL)
        assert config_manager.get_file_peers("api", "example_file") == ["api"]
        assert config_manager.load_file_contract(
            "worker", "example_file"
        ) == config_manager.load_schema("worker")

    def test_single_service_contract_is_its_schema(self, tmp_path, monkeypatch):
        _project(
            tmp_path,
            monkeypatch,
            "services:\n  app:\n    schema: env.schema.toml\n",
            schema='[A]\ndefaultValue = "1"\n[B]\nsecret = true\n',
        )
        assert config_manager.get_file_peers("app", "local_file") == ["app"]
        assert config_manager.load_file_contract(
            "app", "local_file"
        ) == config_manager.load_schema("app")

    def test_compatible_duplicate_definitions_merge_to_one(self, tmp_path, monkeypatch):
        _project(tmp_path, monkeypatch, SAME_DIR)
        contract = config_manager.load_file_contract("api", "local_file")
        assert contract["LOG_LEVEL"] == {"defaultValue": "info"}

    def test_conflicting_service_defaults_fail_clearly(self, tmp_path, monkeypatch):
        _project(
            tmp_path,
            monkeypatch,
            SAME_DIR,
            schema=(
                '[PORT]\n[PORT.services.api]\ndefaultValue = "8000"\n'
                '[PORT.services.worker]\ndefaultValue = "9000"\n'
            ),
        )
        # Each service's own view is still valid...
        assert config_manager.load_schema("api")["PORT"]["defaultValue"] == "8000"
        # ...but one shared file can't hold both defaults.
        with pytest.raises(FileContractConflictError) as exc:
            config_manager.load_file_contract("api", "local_file")
        message = str(exc.value)
        assert "PORT" in message and "api" in message and "worker" in message
        assert "defaultValue" in message
        assert "8000" not in message and "9000" not in message

    def test_sharing_a_file_grants_no_secret(self, tmp_path, monkeypatch):
        _project(tmp_path, monkeypatch, SHARED_LOCAL)
        # The union is built first, so any leak into the views would show below.
        contract = config_manager.load_file_contract("worker", "local_file")
        assert "API_TOKEN" in contract  # the shared file holds it
        view = config_manager.load_schema_view("worker")
        assert "API_TOKEN" not in view.fields
        assert "API_TOKEN" not in config_manager.load_schema("worker")
        assert view.status("API_TOKEN") == "out_of_scope"
        assert view.granted_to("API_TOKEN") == frozenset({"api"})
        assert config_manager.load_schema_view("api").granted_to(
            "API_TOKEN"
        ) == frozenset({"api"})

    def test_differing_descriptions_do_not_conflict(self, tmp_path, monkeypatch):
        _project(
            tmp_path,
            monkeypatch,
            SAME_DIR,
            schema=(
                '[PORT]\ndefaultValue = "8000"\n'
                '[PORT.services.api]\ndescription = "API port"\n'
                '[PORT.services.worker]\ndescription = "Worker port"\n'
            ),
        )
        contract = config_manager.load_file_contract("api", "local_file")
        # Neither description is picked for the shared file...
        assert contract["PORT"] == {"defaultValue": "8000"}
        # ...and each service keeps its own.
        assert config_manager.load_schema("api")["PORT"]["description"] == "API port"
        assert schema_manager.sync_schema("worker") is True
        body = (tmp_path / "app" / ".env.example").read_text()
        assert "PORT=8000" in body
        assert "API port" not in body and "Worker port" not in body

    def test_service_order_does_not_change_the_union(self, tmp_path, monkeypatch):
        _project(tmp_path, monkeypatch, SHARED_LOCAL)
        forward = config_manager.load_file_contract("api", "local_file")
        reversed_yml = (
            "services:\n"
            "  worker:\n    schema: env.schema.toml\n    dir: worker\n    local_file: .env\n"
            "  api:\n    schema: env.schema.toml\n    dir: api\n    local_file: .env\n"
        )
        (tmp_path / "envshield.yml").write_text(reversed_yml)
        backward = config_manager.load_file_contract("api", "local_file")
        assert list(forward.items()) == list(backward.items())


class TestCommandsUseTheFileContract:
    def test_schema_sync_writes_every_sharing_services_variables(
        self, tmp_path, monkeypatch
    ):
        _project(tmp_path, monkeypatch, SAME_DIR)
        assert schema_manager.sync_schema("api") is True
        body = (tmp_path / "app" / ".env.example").read_text()
        for var in ("LOG_LEVEL", "API_TOKEN", "QUEUE_URL"):
            assert f"{var}=" in body
        # Syncing the other peer is a no-op, not a rewrite down to its view.
        assert schema_manager.sync_schema("worker") is False

    def test_schema_sync_for_separate_files_keeps_each_projection(
        self, tmp_path, monkeypatch
    ):
        _project(tmp_path, monkeypatch, SEPARATE)
        schema_manager.sync_schema("worker")
        body = (tmp_path / "worker" / ".env.example").read_text()
        assert "QUEUE_URL=" in body and "API_TOKEN" not in body

    def test_schema_sync_refuses_conflicting_defaults(self, tmp_path, monkeypatch):
        _project(
            tmp_path,
            monkeypatch,
            SAME_DIR,
            schema=(
                '[PORT]\n[PORT.services.api]\ndefaultValue = "8000"\n'
                '[PORT.services.worker]\ndefaultValue = "9000"\n'
            ),
        )
        result = runner.invoke(app, ["schema", "sync", "--service", "api"])
        assert result.exit_code != 0
        assert "PORT" in result.stdout
        assert not (tmp_path / "app" / ".env.example").exists()

    def test_check_does_not_flag_a_peers_variables_as_extra(
        self, tmp_path, monkeypatch
    ):
        _project(tmp_path, monkeypatch, SHARED_LOCAL)
        (tmp_path / ".env").write_text("LOG_LEVEL=info\nAPI_TOKEN=t\nQUEUE_URL=q\n")
        for service in ("api", "worker"):
            result = schema_manager.check_result(".env", service_name=service)
            assert result["clean"], result
        ok, _ = doctor._check_local_env_sync("worker")
        assert ok

    def test_check_still_reports_a_services_own_missing_variable(
        self, tmp_path, monkeypatch
    ):
        _project(tmp_path, monkeypatch, SHARED_LOCAL)
        (tmp_path / ".env").write_text("LOG_LEVEL=info\nAPI_TOKEN=t\n")
        assert schema_manager.check_result(".env", service_name="api")["clean"]
        result = schema_manager.check_result(".env", service_name="worker")
        assert result["missing"] == ["QUEUE_URL"] and result["extra"] == []

    def test_check_of_unshared_file_still_reports_extras(self, tmp_path, monkeypatch):
        _project(tmp_path, monkeypatch, SEPARATE)
        (tmp_path / "worker" / ".env").write_text("QUEUE_URL=q\nAPI_TOKEN=t\n")
        result = schema_manager.check_result("worker/.env", service_name="worker")
        assert result["extra"] == ["API_TOKEN"]

    def test_doctor_template_sync_uses_the_shared_example(self, tmp_path, monkeypatch):
        _project(tmp_path, monkeypatch, SAME_DIR)
        schema_manager.sync_schema("api")
        for service in ("api", "worker"):
            ok, message = doctor._check_example_file_sync(service)
            assert ok, message

    def test_setup_materializes_the_shared_file_for_every_peer(
        self, mocker, tmp_path, monkeypatch
    ):
        _project(tmp_path, monkeypatch, SAME_DIR)
        schema_manager.sync_schema("api")
        mocker.patch(
            "envshield.core.setup_manager.Prompt.ask", side_effect=lambda *a, **k: "v"
        )
        result = setup_manager.run_setup("api")
        assert result
        written = (tmp_path / "app" / ".env").read_text()
        for var in ("LOG_LEVEL", "API_TOKEN", "QUEUE_URL"):
            assert f"{var}=" in written

    def test_setup_refuses_conflicting_defaults(self, mocker, tmp_path, monkeypatch):
        _project(
            tmp_path,
            monkeypatch,
            SAME_DIR,
            schema=(
                '[PORT]\n[PORT.services.api]\ndefaultValue = "8000"\n'
                '[PORT.services.worker]\ndefaultValue = "9000"\n'
            ),
        )
        (tmp_path / "app" / ".env.example").write_text("PORT=\n")
        with pytest.raises(FileContractConflictError):
            setup_manager.run_setup("api")
        assert not (tmp_path / "app" / ".env").exists()


# worker shares the root schema with web but has no `dir` -- the Phase 2
# state where its own commands fail until one is set.
BROKEN_WORKER = (
    "services:\n"
    "  api:\n    schema: api/env.schema.toml\n"
    "  web:\n    schema: env.schema.toml\n    dir: {web_dir}\n"
    "  worker:\n    schema: env.schema.toml\n{worker_extra}"
)


class TestPeerResolutionBoundary:
    def _broken(self, tmp_path, monkeypatch, web_dir="web", worker_extra=""):
        _project(
            tmp_path,
            monkeypatch,
            BROKEN_WORKER.format(web_dir=web_dir, worker_extra=worker_extra),
            schema='[LOG_LEVEL]\ndefaultValue = "info"\n',
        )
        (tmp_path / "web").mkdir(exist_ok=True)
        (tmp_path / "api" / "env.schema.toml").write_text("[API_ONLY]\n")
        with pytest.raises(ServiceConfigError):
            config_manager.get_service_dir("worker")

    def test_unrelated_broken_service_does_not_block_another_file(
        self, tmp_path, monkeypatch
    ):
        self._broken(tmp_path, monkeypatch)
        assert config_manager.get_file_peers("api", "example_file") == ["api"]
        assert schema_manager.sync_schema("api") is True
        assert schema_manager.sync_schema("web") is True
        (tmp_path / "api" / ".env").write_text("API_ONLY=x\nSTRAY=1\n")
        result = schema_manager.check_result("api/.env", service_name="api")
        assert result["extra"] == ["STRAY"]

    def test_broken_service_that_may_share_the_file_fails_closed(
        self, tmp_path, monkeypatch
    ):
        # worker's file would be './.env.example' -- exactly web's.
        self._broken(tmp_path, monkeypatch, web_dir=".")
        with pytest.raises(ServiceConfigError, match="worker"):
            config_manager.get_file_peers("web", "example_file")
        with pytest.raises(ServiceConfigError, match="worker"):
            schema_manager.sync_schema("web")
        assert not (tmp_path / ".env.example").exists()

    def test_broken_service_with_its_own_override_is_bounded_by_it(
        self, tmp_path, monkeypatch
    ):
        self._broken(
            tmp_path,
            monkeypatch,
            web_dir=".",
            worker_extra="    local_file: worker/.env\n",
        )
        assert config_manager.get_file_peers("web", "local_file") == ["web"]
        # Its example_file has no override, so that one still fails closed.
        with pytest.raises(ServiceConfigError):
            config_manager.get_file_peers("web", "example_file")


# BL-138 reproduction: every candidate path by which 'setup' for one service
# could drop another service's values from a shared dotenv file. Each seeds
# the shared '.env' with the peer's values first and asserts they survive
# byte-for-byte after the other service's setup rewrites the file.
PEER_VALUES = {"QUEUE_URL": "amqp://peer-only", "PEER_EXTRA": "kept-by-hand"}
SEPARATE_SCHEMAS_SHARED_LOCAL = (
    "services:\n"
    "  api:\n    schema: api/env.schema.toml\n    local_file: .env\n"
    "  worker:\n    schema: worker/env.schema.toml\n    local_file: .env\n"
)


class TestSetupPreservesPeerValues:
    @pytest.fixture(autouse=True)
    def _answers(self, mocker):
        mocker.patch(
            "envshield.core.setup_manager.Prompt.ask", side_effect=lambda *a, **k: "v"
        )
        mocker.patch("questionary.confirm").return_value.ask.return_value = True

    def _seed_shared_env(self, tmp_path, extra=""):
        (tmp_path / ".env").write_text(
            "LOG_LEVEL=debug\n"
            + "".join(f"{k}={v}\n" for k, v in PEER_VALUES.items())
            + extra
        )

    def _assert_peer_values_survive(self, tmp_path):
        from envshield.parsers.factory import get_parser

        written = get_parser(".env").get_vars(".env", get_values=True)
        for key, value in PEER_VALUES.items():
            assert written.get(key) == value, (key, written)
        assert written["LOG_LEVEL"] == "debug"

    def test_shared_schema_existing_shared_file(self, tmp_path, monkeypatch):
        _project(tmp_path, monkeypatch, SHARED_LOCAL)
        self._seed_shared_env(tmp_path)
        assert setup_manager.run_setup("api")
        self._assert_peer_values_survive(tmp_path)

    def test_separate_schemas_sharing_one_file(self, tmp_path, monkeypatch):
        _project(tmp_path, monkeypatch, SEPARATE_SCHEMAS_SHARED_LOCAL)
        (tmp_path / "api" / "env.schema.toml").write_text(
            '[LOG_LEVEL]\ndefaultValue = "info"\n[API_TOKEN]\nsecret = true\n'
        )
        (tmp_path / "worker" / "env.schema.toml").write_text("[QUEUE_URL]\n")
        self._seed_shared_env(tmp_path)
        assert setup_manager.run_setup("api")
        self._assert_peer_values_survive(tmp_path)

    def test_explicit_output_path_spelled_differently(self, tmp_path, monkeypatch):
        _project(tmp_path, monkeypatch, SHARED_LOCAL)
        self._seed_shared_env(tmp_path)
        assert setup_manager.run_setup("api", output_file="./api/../.env")
        self._assert_peer_values_survive(tmp_path)

    def test_this_services_schema_is_unloadable(self, tmp_path, monkeypatch):
        # A schema that exists but fails to load is an error, not a silent
        # unvalidated setup (evaluator migration, step 8) -- and nothing is
        # written, so every peer's values survive untouched.
        from envshield.core.exceptions import SchemaParseError

        _project(tmp_path, monkeypatch, SEPARATE_SCHEMAS_SHARED_LOCAL)
        (tmp_path / "api" / "env.schema.toml").write_text("[BROKEN\n")
        (tmp_path / "worker" / "env.schema.toml").write_text("[QUEUE_URL]\n")
        self._seed_shared_env(tmp_path)
        before = (tmp_path / ".env").read_text()

        with pytest.raises(SchemaParseError):
            setup_manager.run_setup("api")

        assert (tmp_path / ".env").read_text() == before
        self._assert_peer_values_survive(tmp_path)

    def test_peer_key_with_an_empty_value_is_not_dropped(self, tmp_path, monkeypatch):
        _project(tmp_path, monkeypatch, SHARED_LOCAL)
        self._seed_shared_env(tmp_path, extra="PEER_EMPTY=\n")
        assert setup_manager.run_setup("api")
        self._assert_peer_values_survive(tmp_path)
        assert "PEER_EMPTY=" in (tmp_path / ".env").read_text()

    def test_union_completeness_service(self, tmp_path, monkeypatch):
        _project(
            tmp_path,
            monkeypatch,
            SHARED_LOCAL.replace(
                "    dir: api\n", "    dir: api\n    completeness: union\n"
            ),
        )
        self._seed_shared_env(tmp_path)
        assert setup_manager.run_setup("api")
        self._assert_peer_values_survive(tmp_path)

    def test_absent_shared_file_is_seeded_with_every_peers_variables(
        self, tmp_path, monkeypatch
    ):
        # Nothing to lose, but the file must not come out as api's view only.
        _project(tmp_path, monkeypatch, SHARED_LOCAL)
        (tmp_path / "api" / ".env.example").write_text("LOG_LEVEL=\nAPI_TOKEN=\n")
        assert setup_manager.run_setup("api")
        body = (tmp_path / ".env").read_text()
        for var in ("LOG_LEVEL", "API_TOKEN", "QUEUE_URL"):
            assert f"{var}=" in body
