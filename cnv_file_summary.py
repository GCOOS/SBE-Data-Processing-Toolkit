from __future__ import annotations

import argparse
import math
import os
import re
import sys
from pathlib import Path, PureWindowsPath


NAME_PATTERN = re.compile(
    r"^#\s*name\s*(?P<index>\d+)?\s*=\s*"
    r"(?P<name>[^:]+?)(?:\s*:\s*.*)?$"
)
BAD_FLAG_PATTERN = re.compile(
    r"^#\s*bad_flag\s*=\s*"
    r"(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)"
)


def resolve_directory(directory_text: str) -> Path:
    directory_text = os.path.expandvars(directory_text.strip())
    if (
        len(directory_text) >= 2
        and directory_text[0] == directory_text[-1]
        and directory_text[0] in {'"', "'"}
    ):
        directory_text = directory_text[1:-1].strip()

    windows_path = PureWindowsPath(directory_text)
    if (
        os.name != "nt"
        and windows_path.is_absolute()
        and len(windows_path.drive) == 2
        and windows_path.drive[1] == ":"
    ):
        directory = (
            Path("/mnt")
            / windows_path.drive[0].lower()
            / Path(*windows_path.parts[1:])
        )
    else:
        directory = Path(os.path.normpath(directory_text)).expanduser()

    directory = directory.resolve()
    if not directory.is_dir():
        raise ValueError(f"Directory does not exist: {directory}")
    return directory


def inspect_cnv(path: Path) -> tuple[list[str], int, float | None]:
    variables: dict[int, str] = {}
    data_length = 0
    bad_flag: float | None = None
    pressure_index: int | None = None
    maximum_pressure: float | None = None
    in_data = False

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if in_data:
                if stripped:
                    data_length += 1
                    if pressure_index is not None:
                        values = stripped.split()
                        if pressure_index < len(values):
                            try:
                                pressure = float(values[pressure_index])
                                if (
                                    math.isfinite(pressure)
                                    and pressure != bad_flag
                                    and (
                                        maximum_pressure is None
                                        or pressure > maximum_pressure
                                    )
                                ):
                                    maximum_pressure = pressure
                            except ValueError:
                                pass
                continue

            if stripped == "*END*":
                pressure_index = next(
                    (
                        index
                        for index, variable in variables.items()
                        if variable.casefold() == "prdm"
                    ),
                    next(
                        (
                            index
                            for index, variable in variables.items()
                            if "pressure" in variable.casefold()
                        ),
                        None,
                    ),
                )
                in_data = True
                continue

            match = NAME_PATTERN.match(stripped)
            if match:
                index_text = match.group("index")
                index = int(index_text) if index_text is not None else len(variables)
                variables[index] = match.group("name").strip()
                continue

            bad_flag_match = BAD_FLAG_PATTERN.match(stripped)
            if bad_flag_match:
                bad_flag = float(bad_flag_match.group("value"))

    if not in_data:
        raise ValueError("no '*END*' marker")

    ordered_variables = [
        variable
        for _index, variable in sorted(variables.items())
    ]
    return ordered_variables, data_length, maximum_pressure


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "List CNV variables and data lengths, ordered from shortest "
            "to longest."
        )
    )
    parser.add_argument(
        "directory",
        help="Directory to search recursively for CNV files.",
    )
    arguments = parser.parse_args()

    try:
        root = resolve_directory(arguments.directory)
    except ValueError as error:
        parser.error(str(error))

    paths = sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.casefold() == ".cnv"
        ),
        key=lambda path: str(path.relative_to(root)).casefold(),
    )

    results: list[tuple[int, Path, list[str], float | None]] = []
    errors: list[tuple[Path, str]] = []
    for path in paths:
        try:
            variables, data_length, maximum_pressure = inspect_cnv(path)
            results.append((data_length, path, variables, maximum_pressure))
        except Exception as error:
            errors.append((path, str(error)))

    results.sort(
        key=lambda item: (
            item[0],
            str(item[1].relative_to(root)).casefold(),
        )
    )

    print("Rows\tMaximum pressure (prDM)\tFile\tVariables")
    for data_length, path, variables, maximum_pressure in results:
        relative_path = path.relative_to(root)
        pressure_text = (
            f"{maximum_pressure:.7g}"
            if maximum_pressure is not None
            else "Unavailable"
        )
        print(
            f"{data_length}\t{pressure_text}\t"
            f"{relative_path}\t{', '.join(variables)}"
        )

    for path, message in errors:
        relative_path = path.relative_to(root)
        print(f"Could not read {relative_path}: {message}", file=sys.stderr)

    if not paths:
        print(f"No CNV files found under {root}.", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
