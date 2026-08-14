"""Validate the bounded contract between requirements.txt and its exact lock."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path


SOURCE_DIGEST_PREFIX = "# requirements-source-sha256: "
SOURCE_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
EXACT_PIN_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[[A-Za-z0-9._,-]+\])?=="
    r"(?P<version>[A-Za-z0-9][A-Za-z0-9.!+_-]*)$"
)


class LockValidationError(ValueError):
    """The requirements lock is stale or is not fully exact."""


def _normalized_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def validate(requirements_path: Path, lock_path: Path) -> int:
    """Return the exact-pin count, or raise ``LockValidationError``."""

    source_digest = hashlib.sha256(requirements_path.read_bytes()).hexdigest()
    lines = lock_path.read_text(encoding="utf-8").splitlines()
    recorded_digests = [
        line.removeprefix(SOURCE_DIGEST_PREFIX).strip()
        for line in lines
        if line.startswith(SOURCE_DIGEST_PREFIX)
    ]
    if len(recorded_digests) != 1 or not SOURCE_DIGEST_PATTERN.fullmatch(
        recorded_digests[0] if recorded_digests else ""
    ):
        raise LockValidationError(
            "lock must contain exactly one valid requirements-source-sha256 header"
        )
    if recorded_digests[0] != source_digest:
        raise LockValidationError(
            "requirements.txt SHA-256 does not match the lock: "
            f"recorded {recorded_digests[0]}, actual {source_digest}"
        )

    package_names: set[str] = set()
    pin_count = 0
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = EXACT_PIN_PATTERN.fullmatch(line)
        if match is None:
            raise LockValidationError(
                f"lock line {line_number} is not an exact package==version pin: "
                f"{raw_line!r}"
            )
        normalized_name = _normalized_name(match.group("name"))
        if normalized_name in package_names:
            raise LockValidationError(
                f"lock contains duplicate package pin: {match.group('name')}"
            )
        package_names.add(normalized_name)
        pin_count += 1

    if pin_count == 0:
        raise LockValidationError("lock contains no exact package pins")
    return pin_count


def _parser() -> argparse.ArgumentParser:
    mas_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--requirements",
        type=Path,
        default=mas_root / "requirements.txt",
    )
    parser.add_argument(
        "--lock",
        type=Path,
        default=mas_root / "requirements.lock.txt",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        pin_count = validate(args.requirements, args.lock)
    except (LockValidationError, OSError, UnicodeError) as exc:
        print(f"requirements lock validation failed: {exc}", file=sys.stderr)
        return 1

    source_digest = hashlib.sha256(args.requirements.read_bytes()).hexdigest()
    print(
        f"requirements lock valid: {pin_count} exact pins; "
        f"source sha256 {source_digest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
