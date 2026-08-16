# envshield/tests/core/test_explain.py
import pytest

from envshield.config import manager as config_manager
from envshield.config.manager import SCHEMA_FILE_NAME
from envshield.core import explain
from envshield.core.exceptions import VariableNotFoundError


def _write_root_service(name="app", schema_path=SCHEMA_FILE_NAME):
    config_manager.add_service(name, schema_path)


class TestVariableFound:
    def test_reports_schema_metadata(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write(
                '[DATABASE_URL]\n'
                'description = "Primary DB"\n'
                'type = "url"\n'
                'secret = true\n'
            )

        report = explain.build_report("DATABASE_URL", "app")

        assert report.variable == "DATABASE_URL"
        assert report.service == "app"
        assert report.schema["type"] == "url"
        assert report.schema["requiredness"] == "required"
        assert report.schema["secret"] is True
        assert report.schema["description"] == "Primary DB"

    def test_optional_field_with_default_is_reported_as_optional(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[LOG_LEVEL]\ndescription = "x"\ndefaultValue = "info"\n')

        report = explain.build_report("LOG_LEVEL", "app")

        assert report.schema["requiredness"] == "optional"
        assert report.schema["default"] == "info"


class TestVariableNotFound:
    def test_raises_a_clear_error_not_an_empty_report(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[X]\ndescription = "x"\n')

        with pytest.raises(VariableNotFoundError) as exc_info:
            explain.build_report("DOES_NOT_EXIST", "app")

        assert "DOES_NOT_EXIST" in str(exc_info.value)
        assert "app" in str(exc_info.value)


class TestSourceUsages:
    def test_a_single_usage_is_reported_with_file_and_line(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[DATABASE_URL]\ndescription = "x"\n')
        with open("config.py", "w") as f:
            f.write("import os\nx = 1\nDB = os.environ['DATABASE_URL']\n")

        report = explain.build_report("DATABASE_URL", "app")

        assert len(report.source_usages) == 1
        assert report.source_usages[0].file_path == "config.py"
        assert report.source_usages[0].line == 3

    def test_multiple_usages_across_files_are_all_reported(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[DATABASE_URL]\ndescription = "x"\n')
        with open("a.py", "w") as f:
            f.write("import os\nos.getenv('DATABASE_URL')\n")
        with open("b.py", "w") as f:
            f.write("import os\nos.environ['DATABASE_URL']\n")

        report = explain.build_report("DATABASE_URL", "app")

        files = {u.file_path for u in report.source_usages}
        assert files == {"a.py", "b.py"}

    def test_no_usages_is_reported_as_none_discovered_not_unused(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[DATABASE_URL]\ndescription = "x"\n')
        with open("a.py", "w") as f:
            f.write("x = 1\n")

        report = explain.build_report("DATABASE_URL", "app")

        # The absence check belongs to the caller/renderer (see
        # test_explain_cli.py's "None discovered" assertion) -- this layer
        # simply must not fabricate a usage that doesn't exist.
        assert report.source_usages == []

    def test_only_usages_of_the_requested_variable_are_reported(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[DATABASE_URL]\ndescription = "x"\n\n[OTHER]\ndescription = "x"\n')
        with open("a.py", "w") as f:
            f.write("import os\nos.getenv('OTHER')\n")

        report = explain.build_report("DATABASE_URL", "app")

        assert report.source_usages == []


class TestRequiredIfDependencies:
    def test_forward_requiredif_condition_is_preserved(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write(
                '[DATABASE_URL]\ndescription = "x"\n\n'
                '[REPLICA_URL]\ndescription = "x"\n'
                'requiredIf = { var = "DATABASE_URL", equals = "postgres" }\n'
            )

        report = explain.build_report("REPLICA_URL", "app")

        assert report.schema["requiredness"] == "conditional"
        assert report.schema["requiredIf"] == {
            "var": "DATABASE_URL",
            "equals": "postgres",
        }

    def test_reverse_requiredif_dependents_are_reported(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write(
                '[DATABASE_URL]\ndescription = "x"\n\n'
                '[REPLICA_URL]\ndescription = "x"\n'
                'requiredIf = { var = "DATABASE_URL", equals = "postgres" }\n'
            )

        report = explain.build_report("DATABASE_URL", "app")

        assert report.required_by == [
            {
                "variable": "REPLICA_URL",
                "condition": {"var": "DATABASE_URL", "equals": "postgres"},
            }
        ]

    def test_a_variable_with_no_dependents_reports_an_empty_list(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[DATABASE_URL]\ndescription = "x"\n')

        report = explain.build_report("DATABASE_URL", "app")

        assert report.required_by == []


class TestManifestReferences:
    def test_a_declaring_manifest_is_reported(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_root_service()
        config_manager.add_manifest("docker-compose.yml", {"app": "app"})
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription = "x"\n')
        with open("docker-compose.yml", "w") as f:
            f.write("services:\n  app:\n    environment:\n      - API_KEY=secret\n")

        report = explain.build_report("API_KEY", "app")

        assert len(report.manifest_references) == 1
        ref = report.manifest_references[0]
        assert ref.path == "docker-compose.yml"
        assert ref.container == "app"
        assert ref.status == "declared"

    def test_a_registered_manifest_that_does_not_declare_it_is_not_declared(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _write_root_service()
        config_manager.add_manifest("docker-compose.yml", {"app": "app"})
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription = "x"\n')
        with open("docker-compose.yml", "w") as f:
            f.write("services:\n  app:\n    image: x\n")

        report = explain.build_report("API_KEY", "app")

        assert report.manifest_references[0].status == "not_declared"

    def test_no_manifests_registered_reports_an_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription = "x"\n')

        report = explain.build_report("API_KEY", "app")

        # Empty, not absent -- the CLI layer is what turns "zero registered
        # manifests" and "manifests registered but none declare it" into
        # two different, honestly-worded messages (see test_explain_cli.py).
        assert report.manifest_references == []


class TestExtendsProvenance:
    def test_a_field_declared_only_locally_reports_no_inheritance(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[DATABASE_URL]\ndescription = "x"\n')

        report = explain.build_report("DATABASE_URL", "app")

        assert report.provenance["schema_path"] == SCHEMA_FILE_NAME
        assert report.provenance["declared_in"] == SCHEMA_FILE_NAME
        assert report.provenance["inherited"] is False

    def test_a_field_inherited_through_extends_reports_the_base_file(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "shared").mkdir()
        (tmp_path / "api").mkdir()
        _write_root_service(name="api", schema_path="api/env.schema.toml")
        (tmp_path / "shared" / "base.schema.toml").write_text(
            '[DATABASE_URL]\ndescription = "x"\n'
        )
        (tmp_path / "api" / "env.schema.toml").write_text(
            'extends = "../shared/base.schema.toml"\n'
        )

        report = explain.build_report("DATABASE_URL", "api")

        assert report.provenance["schema_path"] == "api/env.schema.toml"
        assert report.provenance["declared_in"] == "shared/base.schema.toml"
        assert report.provenance["inherited"] is True

    def test_a_local_override_reports_the_local_file_not_the_base(
        self, tmp_path, monkeypatch
    ):
        """The LOG_LEVEL-style gotcha: redeclaring a field locally for an
        unrelated reason must report the LOCAL file as the contributor,
        matching _load_schema_file's own last-write-wins merge exactly."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "shared").mkdir()
        (tmp_path / "api").mkdir()
        _write_root_service(name="api", schema_path="api/env.schema.toml")
        (tmp_path / "shared" / "base.schema.toml").write_text(
            '[LOG_LEVEL]\ndescription = "x"\ndefaultValue = "info"\n'
        )
        (tmp_path / "api" / "env.schema.toml").write_text(
            'extends = "../shared/base.schema.toml"\n\n'
            '[LOG_LEVEL]\ndescription = "overridden for a different reason"\n'
        )

        report = explain.build_report("LOG_LEVEL", "api")

        assert report.provenance["declared_in"] == "api/env.schema.toml"
        assert report.provenance["inherited"] is False
        # The local redeclaration dropped the base's defaultValue too --
        # same whole-field-replace behavior _load_schema_file already has.
        assert report.schema["requiredness"] == "required"

    def test_multi_level_extends_resolves_to_the_root_ancestor(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "root").mkdir()
        (tmp_path / "mid").mkdir()
        (tmp_path / "leaf").mkdir()
        _write_root_service(name="leaf", schema_path="leaf/env.schema.toml")
        (tmp_path / "root" / "base.schema.toml").write_text(
            '[SHARED_SECRET]\ndescription = "x"\n'
        )
        (tmp_path / "mid" / "env.schema.toml").write_text(
            'extends = "../root/base.schema.toml"\n'
        )
        (tmp_path / "leaf" / "env.schema.toml").write_text(
            'extends = "../mid/env.schema.toml"\n'
        )

        report = explain.build_report("SHARED_SECRET", "leaf")

        assert report.provenance["declared_in"] == "root/base.schema.toml"
        assert report.provenance["inherited"] is True
