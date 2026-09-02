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
                "[DATABASE_URL]\n"
                'description = "Primary DB"\n'
                'type = "url"\n'
                "secret = true\n"
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


class TestDescribeFieldWithholdsSecretDefaults:
    """
    BL-001 regression (explain's field-description surface): a secret
    field's real defaultValue must never be echoed in 'explain's schema
    description, in Rich output or --json. config_manager.load_schema
    already refuses this schema shape outright (see test_config_manager.py's
    TestLoadSchemaRejectsSecretDefaults); this exercises _describe_field's
    own defense-in-depth guard directly, for a field_schema dict that
    reached it some other way.
    """

    def test_secret_field_with_a_default_reports_default_as_none(self):
        described = explain._describe_field(
            {
                "secret": True,
                "defaultValue": "sk_live_SYNTHETIC_NOT_A_REAL_SECRET",
            }
        )

        assert described["default"] is None
        assert described["secret"] is True
        # 'requiredness' still reflects that a default exists, without
        # echoing its value -- distinct concerns, both correct.
        assert described["requiredness"] == "optional"

    def test_non_secret_field_with_a_default_is_unaffected(self):
        described = explain._describe_field({"defaultValue": "info"})

        assert described["default"] == "info"


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


class TestBuildUndeclaredReport:
    """
    build_undeclared_report is the CLI's graceful-degradation path, called
    only after build_report has already raised VariableNotFoundError above
    -- it doesn't replace that contract, it's a sibling for exactly the
    case that exception signals.
    """

    def test_declared_variable_with_a_source_read_is_named(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[X]\ndescription = "x"\n')
        with open("config.py", "w") as f:
            f.write("import os\nos.getenv('DOES_NOT_EXIST')\n")

        report = explain.build_undeclared_report("DOES_NOT_EXIST", "app")

        assert report.variable == "DOES_NOT_EXIST"
        assert report.service == "app"
        assert len(report.source_usages) == 1
        assert report.source_usages[0].file_path == "config.py"
        assert report.source_usages[0].line == 2

    def test_undeclared_variable_with_no_source_reference_reports_an_empty_list(
        self, tmp_path, monkeypatch
    ):
        """
        A meaningful, real case: e.g. a name a developer is only
        considering adding, or one read via a pattern EnvShield doesn't
        recognize (app.config[...], a bare getenv() not imported from os,
        etc.) -- reported as an empty list, the same "absence of evidence,
        never proof of absence" contract build_report's own source_usages
        already follows, not a special case invented for this function.
        """
        monkeypatch.chdir(tmp_path)
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[X]\ndescription = "x"\n')
        with open("config.py", "w") as f:
            f.write("x = 1\n")

        report = explain.build_undeclared_report("DOES_NOT_EXIST", "app")

        assert report.source_usages == []

    def test_to_dict_shape(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[X]\ndescription = "x"\n')

        report = explain.build_undeclared_report("DOES_NOT_EXIST", "app")
        payload = report.to_dict()

        assert payload["variable"] == "DOES_NOT_EXIST"
        assert payload["service"] == "app"
        assert payload["found"] is False
        assert payload["declared"] is False
        assert payload["source_usages"] == []
        assert payload["manifest_references"] == []


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


class TestAdditionalSourceRoots:
    """BL-106: additional_source_roots widens a service's discovery scope
    to directories outside its own (e.g. a shared internal library)."""

    def _service_with_roots(self, roots_yaml=""):
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n"
                "  api:\n"
                "    schema: services/api/env.schema.toml\n"
                f"{roots_yaml}"
            )
        import os as _os

        _os.makedirs("services/api", exist_ok=True)
        with open("services/api/env.schema.toml", "w") as f:
            f.write('[SHARED_FLAG]\ndescription = "x"\n')

    def test_one_additional_root_is_discovered(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._service_with_roots(
            "    additional_source_roots:\n      - shared/lib\n"
        )
        import os as _os

        _os.makedirs("shared/lib", exist_ok=True)
        with open("shared/lib/util.py", "w") as f:
            f.write("import os\nx = os.environ.get('SHARED_FLAG')\n")

        report = explain.build_report("SHARED_FLAG", "api")

        assert report.source_usages[0].file_path == "shared/lib/util.py"

    def test_multiple_additional_roots_are_all_discovered(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._service_with_roots(
            "    additional_source_roots:\n      - shared/lib\n      - vendor/other\n"
        )
        import os as _os

        _os.makedirs("shared/lib", exist_ok=True)
        _os.makedirs("vendor/other", exist_ok=True)
        with open("shared/lib/a.py", "w") as f:
            f.write("import os\nos.environ.get('SHARED_FLAG')\n")
        with open("vendor/other/b.py", "w") as f:
            f.write("import os\nos.environ.get('SHARED_FLAG')\n")

        report = explain.build_report("SHARED_FLAG", "api")

        files = {u.file_path for u in report.source_usages}
        assert files == {"shared/lib/a.py", "vendor/other/b.py"}

    def test_nonexistent_root_is_a_silent_no_op(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._service_with_roots(
            "    additional_source_roots:\n      - does/not/exist\n"
        )

        report = explain.build_report("SHARED_FLAG", "api")

        assert report.source_usages == []

    def test_a_root_nested_inside_the_service_directory_produces_no_duplicate(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        self._service_with_roots(
            "    additional_source_roots:\n      - services/api/vendored\n"
        )
        import os as _os

        _os.makedirs("services/api/vendored", exist_ok=True)
        with open("services/api/vendored/util.py", "w") as f:
            f.write("import os\nos.environ.get('SHARED_FLAG')\n")

        report = explain.build_report("SHARED_FLAG", "api")

        assert len(report.source_usages) == 1

    def test_the_same_root_may_be_shared_by_two_services(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n"
                "  api:\n"
                "    schema: services/api/env.schema.toml\n"
                "    additional_source_roots:\n      - shared/lib\n"
                "  web:\n"
                "    schema: services/web/env.schema.toml\n"
                "    additional_source_roots:\n      - shared/lib\n"
            )
        import os as _os

        _os.makedirs("services/api", exist_ok=True)
        _os.makedirs("services/web", exist_ok=True)
        _os.makedirs("shared/lib", exist_ok=True)
        with open("services/api/env.schema.toml", "w") as f:
            f.write('[SHARED_FLAG]\ndescription = "x"\n')
        with open("services/web/env.schema.toml", "w") as f:
            f.write('[SHARED_FLAG]\ndescription = "x"\n')
        with open("shared/lib/util.py", "w") as f:
            f.write("import os\nos.environ.get('SHARED_FLAG')\n")

        api_report = explain.build_report("SHARED_FLAG", "api")
        web_report = explain.build_report("SHARED_FLAG", "web")

        assert api_report.source_usages[0].file_path == "shared/lib/util.py"
        assert web_report.source_usages[0].file_path == "shared/lib/util.py"

    def test_absent_key_preserves_existing_behavior(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._service_with_roots("")
        import os as _os

        _os.makedirs("shared/lib", exist_ok=True)
        with open("shared/lib/util.py", "w") as f:
            f.write("import os\nos.environ.get('SHARED_FLAG')\n")

        report = explain.build_report("SHARED_FLAG", "api")

        assert report.source_usages == []

    def test_symlinked_additional_root_is_refused(self, tmp_path, monkeypatch):
        """
        Mirrors the existing symlink refusal already applied to a service's
        own directory (_discoverable_files' unconditional
        os.path.islink(root_dir) check) -- the same guard, applied to an
        additional root, with no new mechanism. The symlink target is
        deliberately *inside* the project, so this exercises that
        discovery-level guard specifically, distinct from
        _ensure_within_project's separate (and separately tested, see
        test_config_manager.py) boundary check for a root resolving
        outside the project entirely.
        """
        monkeypatch.chdir(tmp_path)
        self._service_with_roots(
            "    additional_source_roots:\n      - shared/lib\n"
        )
        import os as _os

        _os.makedirs("real_shared_lib", exist_ok=True)
        with open("real_shared_lib/util.py", "w") as f:
            f.write("import os\nos.environ.get('SHARED_FLAG')\n")
        _os.makedirs("shared", exist_ok=True)
        _os.symlink(_os.path.abspath("real_shared_lib"), "shared/lib")

        report = explain.build_report("SHARED_FLAG", "api")

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

    def test_a_manifest_with_an_unresolved_env_from_is_unresolved_not_not_declared(
        self, tmp_path, monkeypatch
    ):
        """
        Regression, same root cause as the check/doctor fix: a Kubernetes
        manifest referencing an external ConfigMap/Secret EnvShield can't
        inspect must not be reported as 'not_declared' -- that's a
        confident claim of absence this parser can't back up, exactly the
        false negative this command's own docstring already promises
        never to make.
        """
        monkeypatch.chdir(tmp_path)
        _write_root_service()
        config_manager.add_manifest("deployment.yaml", {"app": "app"})
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[DATABASE_URL]\ndescription = "x"\n')
        with open("deployment.yaml", "w") as f:
            f.write(
                "apiVersion: apps/v1\n"
                "kind: Deployment\n"
                "metadata:\n  name: app\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: app\n"
                "          envFrom:\n"
                "            - secretRef:\n"
                "                name: externally-managed-secret\n"
            )

        report = explain.build_report("DATABASE_URL", "app")

        assert report.manifest_references[0].status == "unresolved"

    def test_a_malformed_manifest_is_reported_as_a_structured_error(
        self, tmp_path, monkeypatch
    ):
        """
        BL-002 regression: a manifest EnvShield can't parse must surface as
        one 'error' entry in the report, not propagate EnvShieldException
        out of build_report entirely and abort the whole command. Uses a
        '.py' file registered as a manifest -- get_parser resolves by
        extension regardless of the file's intended role, so this is a
        real, reachable path even though it's an unusual one.
        """
        monkeypatch.chdir(tmp_path)
        _write_root_service()
        config_manager.add_manifest("broken_manifest.py", {"app": "app"})
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription = "x"\n')
        with open("broken_manifest.py", "w") as f:
            f.write("API_KEY =")  # unterminated -- invalid syntax

        report = explain.build_report("API_KEY", "app")

        assert len(report.manifest_references) == 1
        ref = report.manifest_references[0]
        assert ref.status == "error"
        assert "broken_manifest.py" in ref.detail

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
