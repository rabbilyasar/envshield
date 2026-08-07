# envshield/tests/core/test_service_discovery.py
import os

from envshield.core import service_discovery


def test_detect_env_style_finds_plain_dotenv_file(tmp_path):
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / ".env").write_text("KEY=value\n")

    result = service_discovery.detect_env_style(str(tmp_path / "api"))

    assert result == {"format": "dotenv", "local_file": None, "example_file": None}


def test_detect_env_style_overrides_local_file_for_non_dot_env_variant(tmp_path):
    """Only '.env' itself needs no override -- any other dotenv variant found must be pointed to explicitly."""
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / ".env.local").write_text("KEY=value\n")

    result = service_discovery.detect_env_style(str(tmp_path / "api"))

    assert result["format"] == "dotenv"
    assert result["local_file"] == str(tmp_path / "api" / ".env.local")


def test_detect_env_style_finds_python_config_module(tmp_path):
    (tmp_path / "athena" / "config").mkdir(parents=True)
    (tmp_path / "athena" / "config" / "env_config.local.py").write_text(
        'DB_HOST = ""\nDB_NAME = "athena"\nCACHE_PORT = 6379\n'
    )

    result = service_discovery.detect_env_style(str(tmp_path / "athena"))

    assert result["format"] == "python"
    assert result["local_file"] == str(
        tmp_path / "athena" / "config" / "env_config.local.py"
    )


def test_detect_env_style_finds_mastodon_style_production_only_env(tmp_path):
    """
    Regression: Mastodon's actual, documented convention is a single
    '.env.production' file with no plain '.env' at all -- a short fixed
    candidate list (.env.development, .env.dev, ...) missed this entirely.
    """
    (tmp_path / "mastodon").mkdir()
    (tmp_path / "mastodon" / ".env.production").write_text("DB_HOST=localhost\n")

    result = service_discovery.detect_env_style(str(tmp_path / "mastodon"))

    assert result["format"] == "dotenv"
    assert result["local_file"] == str(tmp_path / "mastodon" / ".env.production")


def test_detect_env_style_finds_nx_style_per_target_env_file(tmp_path):
    """Regression: Nx's per-target naming ('.env.<target>.<configuration>') is multi-segment and was missed too."""
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / ".env.serve.development").write_text("DB_URL=postgres://x\n")

    result = service_discovery.detect_env_style(str(tmp_path / "api"))

    assert result["format"] == "dotenv"
    assert result["local_file"] == str(tmp_path / "api" / ".env.serve.development")


def test_detect_env_style_falls_back_to_a_template_when_no_real_file_exists(tmp_path):
    """
    A service that hasn't been set up locally yet may only have a checked-in
    template ('.env.sample', not the standard '.env.example' name here) --
    still worth registering as dotenv-format and seeding a schema from, just
    without a local_file override since there's no real file yet.
    """
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / ".env.sample").write_text("API_KEY=\nLOG_LEVEL=info\n")

    result = service_discovery.detect_env_style(str(tmp_path / "api"))

    assert result == {
        "format": "dotenv",
        "local_file": None,
        "example_file": str(tmp_path / "api" / ".env.sample"),
    }


def test_detect_env_style_prefers_real_file_over_template(tmp_path):
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / ".env").write_text("API_KEY=real-value\n")
    (tmp_path / "api" / ".env.example").write_text("API_KEY=\n")

    result = service_discovery.detect_env_style(str(tmp_path / "api"))

    assert result["format"] == "dotenv"
    assert result["local_file"] is None  # plain '.env' needs no override
    assert result["example_file"] is None


def test_detect_env_style_ignores_python_file_with_too_few_vars(tmp_path):
    """
    Regression guard: a python config candidate must actually look like a
    config module (several top-level UPPER_CASE assignments), not just
    exist at a candidate path -- otherwise a one-off script placed there
    would be misidentified.
    """
    (tmp_path / "svc" / "config").mkdir(parents=True)
    (tmp_path / "svc" / "config" / "settings.py").write_text("DEBUG = True\n")

    result = service_discovery.detect_env_style(str(tmp_path / "svc"))

    assert result == {"format": None, "local_file": None, "example_file": None}


def test_detect_env_style_prefers_dotenv_over_python(tmp_path):
    (tmp_path / "svc" / "config").mkdir(parents=True)
    (tmp_path / "svc" / ".env").write_text("KEY=value\n")
    (tmp_path / "svc" / "config" / "settings.py").write_text(
        "A = 1\nB = 2\nC = 3\n"
    )

    result = service_discovery.detect_env_style(str(tmp_path / "svc"))

    assert result["format"] == "dotenv"


def test_discover_candidates_finds_zeus_shaped_services_and_skips_libraries(tmp_path):
    """
    Regression: a naive 'has a project marker (pyproject.toml/package.json)'
    heuristic would wrongly flag shared library packages as services --
    they're exactly as likely to have one of those markers as a real
    service is, but have no environment config of their own.
    """
    # Real services: a Python-module-config one and a dotenv one.
    (tmp_path / "athena" / "config").mkdir(parents=True)
    (tmp_path / "athena" / "config" / "env_config.local.py").write_text(
        'DB_HOST = ""\nDB_NAME = "athena"\nCACHE_PORT = 6379\n'
    )
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / ".env").write_text("API_URL=http://localhost\n")

    # A shared library: has a project marker, but no env config at all.
    (tmp_path / "modules" / "phineas").mkdir(parents=True)
    (tmp_path / "modules" / "phineas" / "pyproject.toml").write_text(
        "[project]\nname = 'phineas'\n"
    )

    # Noise directories that must never be walked into.
    (tmp_path / ".git").mkdir()
    (tmp_path / "node_modules" / "some-pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "some-pkg" / ".env").write_text("X=1\n")

    candidates = service_discovery.discover_candidates(str(tmp_path))
    names = {c["name"] for c in candidates}

    assert names == {"athena", "web"}
    athena = next(c for c in candidates if c["name"] == "athena")
    assert athena["format"] == "python"
    web = next(c for c in candidates if c["name"] == "web")
    assert web["format"] == "dotenv"


def test_discover_candidates_recurses_one_level_into_services_container(tmp_path):
    (tmp_path / "services" / "api").mkdir(parents=True)
    (tmp_path / "services" / "api" / ".env").write_text("KEY=1\n")
    (tmp_path / "services" / "web").mkdir(parents=True)
    (tmp_path / "services" / "web" / ".env").write_text("KEY=1\n")

    candidates = service_discovery.discover_candidates(str(tmp_path))
    names = {c["name"] for c in candidates}

    assert names == {"api", "web"}
    api = next(c for c in candidates if c["name"] == "api")
    assert api["dir"] == os.path.normpath(str(tmp_path / "services" / "api"))


def test_discover_candidates_skips_already_known_directories(tmp_path):
    """Idempotency for the 'extend' use case: a previously-registered service must not be re-suggested."""
    (tmp_path / "athena").mkdir()
    (tmp_path / "athena" / ".env").write_text("KEY=1\n")
    (tmp_path / "hermes").mkdir()
    (tmp_path / "hermes" / ".env").write_text("KEY=1\n")

    candidates = service_discovery.discover_candidates(
        str(tmp_path), known_dirs=[str(tmp_path / "athena")]
    )

    assert {c["name"] for c in candidates} == {"hermes"}


def test_discover_candidates_disambiguates_name_collisions(tmp_path):
    (tmp_path / "services" / "api").mkdir(parents=True)
    (tmp_path / "services" / "api" / ".env").write_text("KEY=1\n")
    (tmp_path / "packages" / "api").mkdir(parents=True)
    (tmp_path / "packages" / "api" / ".env").write_text("KEY=1\n")

    candidates = service_discovery.discover_candidates(str(tmp_path))
    names = {c["name"] for c in candidates}

    assert len(candidates) == 2
    assert "api" in names
    assert any(n != "api" for n in names)  # the second one got disambiguated


def test_find_compose_file_prefers_service_directory_over_project_root(tmp_path):
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "docker-compose.yml").write_text("services:\n  api:\n    image: x\n")
    (tmp_path / "docker-compose.yml").write_text("services:\n  root:\n    image: x\n")

    found = service_discovery.find_compose_file(str(tmp_path / "api"), str(tmp_path))

    assert found == os.path.normpath(str(tmp_path / "api" / "docker-compose.yml"))


def test_find_compose_file_falls_back_to_project_root(tmp_path):
    (tmp_path / "api").mkdir()
    (tmp_path / "docker-compose.yml").write_text("services:\n  api:\n    image: x\n")

    found = service_discovery.find_compose_file(str(tmp_path / "api"), str(tmp_path))

    assert found == os.path.normpath(str(tmp_path / "docker-compose.yml"))


def test_find_compose_file_returns_none_when_absent(tmp_path):
    (tmp_path / "api").mkdir()

    assert service_discovery.find_compose_file(str(tmp_path / "api"), str(tmp_path)) is None


def test_discover_candidates_includes_deployment_manifest_when_found(tmp_path):
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / ".env").write_text("KEY=1\n")
    (tmp_path / "docker-compose.yml").write_text("services:\n  api:\n    image: x\n")

    candidates = service_discovery.discover_candidates(str(tmp_path))

    api = next(c for c in candidates if c["name"] == "api")
    assert api["deployment_manifest"] == os.path.normpath(str(tmp_path / "docker-compose.yml"))


def test_discover_candidates_does_not_attach_manifest_that_does_not_name_the_service(tmp_path):
    """
    Regression: find_compose_file only checks that *a* compose file exists
    nearby, not that this service is actually declared in it. A shared root
    compose file with exactly one container would otherwise get silently
    attached to every unrelated directory discover finds -- and since a
    single-container compose file is used regardless of --container/prefer,
    that means validating an unrelated service against the wrong container's
    variables with no error at all.
    """
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / ".env").write_text("KEY=1\n")
    (tmp_path / "docker-compose.yml").write_text("services:\n  api:\n    image: x\n")

    candidates = service_discovery.discover_candidates(str(tmp_path))

    docs = next(c for c in candidates if c["name"] == "docs")
    assert docs["deployment_manifest"] is None


def test_compose_declares_service(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services:\n  api:\n    image: x\n  worker:\n    image: y\n")

    assert service_discovery.compose_declares_service(str(compose), "api") is True
    assert service_discovery.compose_declares_service(str(compose), "docs") is False


def test_find_config_source_returns_conventional_dotenv_path(tmp_path):
    """
    Unlike detect_env_style (which omits the path for the conventional
    '.env' name since its caller only needs an override signal),
    find_config_source always returns the real path -- callers like 'init'
    need an actual file to read from.
    """
    (tmp_path / ".env").write_text("KEY=1\n")

    assert service_discovery.find_config_source(str(tmp_path)) == str(tmp_path / ".env")


def test_find_config_source_finds_python_config_module(tmp_path):
    (tmp_path / "config").mkdir()
    settings = tmp_path / "config" / "settings.py"
    settings.write_text("SECRET_KEY = 'x'\nDEBUG = True\nAPI_PORT = 5000\n")

    assert service_discovery.find_config_source(str(tmp_path)) == str(settings)


def test_find_config_source_returns_none_when_nothing_found(tmp_path):
    assert service_discovery.find_config_source(str(tmp_path)) is None
