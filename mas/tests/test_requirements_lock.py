from __future__ import annotations

from pathlib import Path

import pytest

from scripts import validate_requirements_lock


MAS_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_PATH = MAS_ROOT / "requirements.txt"
LOCK_PATH = MAS_ROOT / "requirements.lock.txt"
REPO_ROOT = MAS_ROOT.parent


def test_committed_requirements_lock_is_current_and_exact():
    assert validate_requirements_lock.validate(REQUIREMENTS_PATH, LOCK_PATH) == 78


def test_requirements_change_fails_the_source_digest_guard(tmp_path: Path):
    requirements = tmp_path / "requirements.txt"
    lock = tmp_path / "requirements.lock.txt"
    requirements.write_bytes(REQUIREMENTS_PATH.read_bytes() + b"\nnew-package>=1\n")
    lock.write_bytes(LOCK_PATH.read_bytes())

    with pytest.raises(
        validate_requirements_lock.LockValidationError,
        match="SHA-256 does not match",
    ):
        validate_requirements_lock.validate(requirements, lock)


@pytest.mark.parametrize("replacement", ["anyio>=4.14.2", "anyio===4.14.2"])
def test_non_double_equals_lock_entry_fails_closed(
    tmp_path: Path, replacement: str
):
    requirements = tmp_path / "requirements.txt"
    lock = tmp_path / "requirements.lock.txt"
    requirements.write_bytes(REQUIREMENTS_PATH.read_bytes())
    lock.write_text(
        LOCK_PATH.read_text(encoding="utf-8").replace(
            "anyio==4.14.2", replacement
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        validate_requirements_lock.LockValidationError,
        match="not an exact package==version pin",
    ):
        validate_requirements_lock.validate(requirements, lock)


def test_installer_tooling_is_not_an_application_lock_entry():
    package_names = {
        line.split("==", 1)[0].lower()
        for line in LOCK_PATH.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }
    assert package_names.isdisjoint({"pip", "setuptools", "wheel"})


@pytest.mark.parametrize(
    ("relative_path", "install_count"),
    [
        (".github/workflows/tests.yml", 1),
        (".github/workflows/evals.yml", 5),
        (".github/workflows/evals-nightly-batch.yml", 1),
    ],
)
def test_full_environment_ci_installs_are_bounded(relative_path: str, install_count: int):
    workflow = (REPO_ROOT / relative_path).read_text(encoding="utf-8")

    assert workflow.count("python -m pip install --upgrade pip==25.3") == install_count
    assert workflow.count("python scripts/validate_requirements_lock.py") == install_count
    assert workflow.count("python -m pip install -r requirements.lock.txt") == install_count
    assert workflow.count("cache-dependency-path: mas/requirements.lock.txt") == install_count
    assert "pip install -r requirements.txt" not in workflow


def test_docker_install_is_bounded_and_validated():
    dockerfile = (MAS_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "pip install --no-cache-dir --upgrade pip==25.3" in dockerfile
    assert "python scripts/validate_requirements_lock.py" in dockerfile
    assert "pip install --no-cache-dir -r requirements.lock.txt" in dockerfile
    assert "-r requirements.txt" not in dockerfile


def test_provider_only_preflights_remain_outside_the_full_environment_lock():
    workflow = (REPO_ROOT / ".github/workflows/evals.yml").read_text(encoding="utf-8")

    assert "python -m pip install 'anthropic==0.122.0'" in workflow
    assert "python -m pip install 'openai==2.54.0'" in workflow
