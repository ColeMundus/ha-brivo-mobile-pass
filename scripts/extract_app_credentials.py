#!/usr/bin/env python3
"""Extract Brivo's US production app credentials locally with JADX.

Requires Python 3.10+ and JADX. Reads an APK without activating a pass.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

JAVA_STRING = r'"(?:\\.|[^"\\])*"'
JAVA_TOKEN = re.compile(r"//[^\n]*|/\*.*?\*/|" + JAVA_STRING + r"|'(?:\\.|[^'\\])*'", re.S)
CLASS_NAME = "com.brivo.pass.Constants"


class ExtractionError(Exception):
    """An extraction error that does not expose credential values."""


def masked(source: str) -> str:
    """Hide comments and literals while preserving source offsets."""
    return JAVA_TOKEN.sub(lambda match: " " * len(match.group()), source)


def block_end(code: str, opening: int) -> int:
    depth = 1
    for index in range(opening + 1, len(code)):
        if code[index] == "{":
            depth += 1
        elif code[index] == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    raise ExtractionError("The decompiled class is incomplete. Use the manual lookup guide.")


def method_body(source: str, name: str) -> str:
    code = masked(source)
    matches = list(re.finditer(r"\bString\s+" + name + r"\s*\([^)]*\)\s*\{", code))
    if len(matches) != 1:
        raise ExtractionError(
            "This APK uses an unsupported credential layout. Use the manual lookup guide."
        )
    opening = matches[0].end() - 1
    return source[opening + 1 : block_end(code, opening) - 1]


def production_value(
    source: str, name: str, us_region: int, region_map: str, release_type: str
) -> str:
    body = method_body(source, name)
    code = masked(body)
    # The verified layout excludes all other regions before the production branch.
    guards = list(re.finditer(r"if\s*\(\s*(\w+)\s*!=\s*" + str(us_region) + r"\s*\)\s*\{", code))
    if len(guards) != 1:
        raise ExtractionError(
            "Could not identify the US region branch. Use the manual lookup guide."
        )
    guard = guards[0]
    assignment = (
        r"\bint\s+"
        + re.escape(guard.group(1))
        + r"\s*=\s*WhenMappings\."
        + re.escape(region_map)
        + r"\s*\[\s*\w+\.ordinal\(\)\s*\]\s*;"
    )
    if not re.search(assignment, code[: guard.start()]):
        raise ExtractionError("The credential branch does not use the US region map.")
    guard_end = block_end(code, guard.end() - 1)
    if re.search(
        r"\b" + re.escape(guard.group(1)) + r"\s*==\s*" + str(us_region) + r"\b",
        code[: guard.start()],
    ):
        raise ExtractionError("The US branch has changed. Use the manual lookup guide.")
    pattern = (
        r"if\s*\(\s*Intrinsics\.areEqual\(\s*(" + JAVA_STRING + r")\s*,\s*"
        r"BuildTypes\.RELEASE_PROD\.getType\(\)\s*\)\s*\)\s*\{\s*return\s+("
        + JAVA_STRING
        + r")\s*;\s*\}"
    )
    matches = [m for m in re.finditer(pattern, body) if code[m.start() : m.start() + 2] == "if"]
    if len(matches) != 1 or code[guard_end : matches[0].start()].strip():
        raise ExtractionError(
            "Could not identify a single production credential. Use the manual lookup guide."
        )
    try:
        build, value = map(json.loads, matches[0].groups())
    except ValueError:
        raise ExtractionError(
            "Unsupported Java string encoding. Use the manual lookup guide."
        ) from None
    if build != release_type or not value or any(ord(char) < 32 for char in value):
        raise ExtractionError(
            "The production branch could not be verified. Use the manual lookup guide."
        )
    return value


def extract_from_source(source: str) -> dict[str, str]:
    code = masked(source)
    regions = re.findall(
        r"(\w+)\[\s*RegionSelectionState\.FIREBASE_UNITED_STATES\.ordinal\(\)\s*\]\s*=\s*(\d+)\s*;",
        code,
    )
    if len(regions) != 1:
        raise ExtractionError("Could not identify the US region. Use the manual lookup guide.")
    array_name, us_region = regions[0]
    maps = re.findall(r"(\$EnumSwitchMapping\$\d+)\s*=\s*" + re.escape(array_name) + r"\s*;", code)
    if len(maps) != 1:
        raise ExtractionError("Could not identify the region map. Use the manual lookup guide.")
    releases = [
        m
        for m in re.finditer(r"\bRELEASE_PROD\s*\(\s*(" + JAVA_STRING + r")\s*\)", source)
        if code[m.start() : m.start() + 12] == "RELEASE_PROD"
    ]
    if len(releases) != 1:
        raise ExtractionError(
            "Could not identify the production build. Use the manual lookup guide."
        )
    try:
        release_type = json.loads(releases[0].group(1))
    except ValueError:
        raise ExtractionError("Unsupported production build value.") from None
    return {
        "client_id": production_value(source, "getClientId", int(us_region), maps[0], release_type),
        "client_secret": production_value(
            source, "getClientSecret", int(us_region), maps[0], release_type
        ),
    }


def extract_apk(apk: Path, jadx: str) -> dict[str, str]:
    if not apk.is_file():
        raise ExtractionError("APK not found. Check the file path.")
    try:
        with zipfile.ZipFile(apk) as archive:
            if not {"AndroidManifest.xml", "classes.dex"}.issubset(archive.namelist()):
                raise ExtractionError("Choose a plain Android APK, not an XAPK or APKM bundle.")
    except (OSError, zipfile.BadZipFile):
        raise ExtractionError("The file is not a readable APK.") from None
    executable = shutil.which(jadx)
    if executable is None:
        raise ExtractionError(
            "JADX was not found. Install JADX or set --jadx to its executable path."
        )
    with tempfile.TemporaryDirectory(prefix="brivo-credentials-") as folder:
        output = Path(folder) / "Constants.java"
        try:
            subprocess.run(
                [
                    executable,
                    "--no-res",
                    "--single-class",
                    CLASS_NAME,
                    "--single-class-output",
                    str(output),
                    str(apk.resolve()),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=180,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise ExtractionError("JADX timed out. Try the manual lookup guide.") from None
        except OSError:
            raise ExtractionError("JADX could not start. Check its Java installation.") from None
        # JADX can return a nonzero status while recovering this class successfully.
        if not output.is_file():
            raise ExtractionError(
                "JADX could not recover the credential class. Check the APK and Java installation, or use the manual lookup guide."
            )
        return extract_from_source(output.read_text(encoding="utf-8"))


def save_credentials(path: Path, credentials: dict[str, str]) -> None:
    """Create an owner-only output file without replacing existing files."""
    fd, name = tempfile.mkstemp(prefix=".brivo-credentials-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(credentials, file, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.link(name, path)  # Atomic, and refuses to overwrite an existing file.
    finally:
        os.unlink(name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path, help="Downloaded Brivo Mobile Pass .apk file")
    parser.add_argument("--jadx", default="jadx", help="JADX executable (default: jadx)")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("brivo-app-credentials.private.json"),
        help="Output JSON file; existing files are never overwritten",
    )
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists. Choose another filename with --output.")
    try:
        credentials = extract_apk(args.apk, args.jadx)
        save_credentials(args.output, credentials)
    except (ExtractionError, OSError) as error:
        print(
            str(error)
            if isinstance(error, ExtractionError)
            else "Could not save the output file. Check its directory and permissions.",
            file=sys.stderr,
        )
        return 1
    print(f"Saved US production app credentials to {args.output}.")
    print("Open that file and copy client_id and client_secret into Home Assistant.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
