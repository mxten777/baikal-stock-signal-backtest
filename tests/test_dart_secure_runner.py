"""Windows DPAPI integration using synthetic values and private temp directories."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "dart_secure_runner.ps1"
SHELL = shutil.which("powershell.exe")
TOKEN = "synthetic-test-only-not-a-real-api-key"

pytestmark = pytest.mark.skipif(os.name != "nt" or SHELL is None, reason="Windows DPAPI required")


@pytest.fixture(autouse=True, params=[shutil.which("powershell.exe"), shutil.which("pwsh.exe")])
def shell_and_cleanup(request, monkeypatch, tmp_path):
    if request.param is None:
        pytest.skip("PowerShell executable unavailable")
    monkeypatch.setattr(sys.modules[__name__], "SHELL", request.param)
    yield
    directory = tmp_path / "private"
    if directory.exists():
        shutil.rmtree(directory)


def run(mode, directory, *, token=None):
    env = {key: value for key, value in os.environ.items() if key.upper() != "DART_API_KEY"}
    if token is not None:
        env["DART_API_KEY"] = token
    result = subprocess.run(
        [SHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(RUNNER),
         "-Mode", mode, "-SecretDirectory", str(directory)],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert TOKEN not in result.stdout + result.stderr
    return result


def test_setup_dpapi_roundtrip_child_environment_and_no_plaintext(tmp_path):
    directory = tmp_path / "private"
    result = run("Setup", directory, token=TOKEN)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CHILD_KEY_PRESENT=True" in result.stdout
    encrypted = directory / "dart_api_key.dpapi"
    assert encrypted.is_file()
    assert TOKEN.encode() not in encrypted.read_bytes()
    before = encrypted.read_bytes()
    result = run("Verify", directory)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CHILD_KEY_PRESENT=True" in result.stdout
    assert encrypted.read_bytes() == before
    assert sorted(p.name for p in directory.iterdir()) == ["dart_api_key.dpapi"]


def test_existing_secret_is_never_overwritten(tmp_path):
    directory = tmp_path / "private"
    assert run("Setup", directory, token=TOKEN).returncode == 0
    encrypted = directory / "dart_api_key.dpapi"
    before = encrypted.read_bytes()
    assert run("Setup", directory, token=TOKEN).returncode == 1
    assert encrypted.read_bytes() == before


def test_missing_secret_and_noninteractive_input_fail_without_live_calls(tmp_path):
    directory = tmp_path / "private"
    result = run("Verify", directory)
    assert result.returncode == 1
    assert not directory.exists()
    result = run("Setup", directory)
    assert result.returncode == 1
    assert not (directory / "dart_api_key.dpapi").exists()


def test_repository_secret_directory_is_rejected():
    result = run("Setup", ROOT / "forbidden-dart-secret", token=TOKEN)
    assert result.returncode == 1
    assert not (ROOT / "forbidden-dart-secret").exists()


def test_corrupt_ciphertext_fails_without_echo(tmp_path):
    directory = tmp_path / "private"
    assert run("Setup", directory, token=TOKEN).returncode == 0
    (directory / "dart_api_key.dpapi").write_text("not-a-dpapi-blob", encoding="ascii")
    result = run("Verify", directory)
    assert result.returncode == 1
    assert "not-a-dpapi-blob" not in result.stdout + result.stderr


def test_broad_acl_is_rejected(tmp_path):
    directory = tmp_path / "private"
    assert run("Setup", directory, token=TOKEN).returncode == 0
    # Add a read grant for BUILTIN Users on the dummy secret, never a real key.
    subprocess.run(
        ["icacls.exe", str(directory / "dart_api_key.dpapi"), "/grant", "*S-1-5-32-545:R"],
        check=True, capture_output=True, timeout=10,
    )
    result = run("Verify", directory)
    assert result.returncode == 1
    assert json.loads(result.stdout)["error_code"] == "SECRET_READ_OR_DECRYPT_FAILED"
