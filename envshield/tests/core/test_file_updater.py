from envshield.core import file_updater


def test_update_variables_in_py_file(tmp_path):
    """Tests updating variables in a Python file."""
    # tmp_path is a pytest fixture that creates a temporary directory
    p = tmp_path / "config.py"
    p.write_text('API_KEY = "old_value"\nDEBUG = True\n')

    updates = [{"key": "API_KEY", "value": "new_secret_value"}]
    file_updater.update_variables_in_file(str(p), updates)

    content = p.read_text()
    assert 'API_KEY = "new_secret_value"' in content
    assert "DEBUG = True" in content


def test_update_variables_in_dotenv_file(tmp_path):
    """Tests updating variables in a .env file."""
    p = tmp_path / ".env"
    p.write_text("API_KEY=old_value\nDEBUG=True\n")

    updates = [{"key": "API_KEY", "value": "new_secret_value"}]
    file_updater.update_variables_in_file(str(p), updates)

    content = p.read_text()
    assert "API_KEY=new_secret_value" in content
    assert "DEBUG=True" in content
