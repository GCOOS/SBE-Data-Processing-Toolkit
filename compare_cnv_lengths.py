from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path, PureWindowsPath

from cnv_file_summary import inspect_cnv


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
        raise ValueError(f"Folder does not exist: {directory}")
    return directory


def cnv_data_length(path: Path) -> int:
    row_count = 0
    in_data = False
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if in_data:
                if stripped:
                    row_count += 1
            elif stripped == "*END*":
                in_data = True

    if not in_data:
        raise ValueError("no '*END*' marker")
    return row_count


def files_by_casefolded_name(folder: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in folder.iterdir():
        if not path.is_file() or path.suffix.casefold() != ".cnv":
            continue
        key = path.name.casefold()
        if key in files:
            raise ValueError(
                f"Duplicate case-insensitive filename in {folder}: {path.name}"
            )
        files[key] = path
    return files


def relative_length(cnv_length: int, updown_length: int) -> float:
    if updown_length == 0:
        return 1.0 if cnv_length == 0 else math.inf
    return cnv_length / updown_length


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare data lengths for matching CNV files in CNV/01-cnv "
            "and UPDOWN/01-cnv."
        )
    )
    parser.add_argument(
        "folder",
        help="Folder containing the CNV and UPDOWN subfolders.",
    )
    arguments = parser.parse_args()

    try:
        root = resolve_directory(arguments.folder)
        cnv_folder = root / "CNV" / "01-cnv"
        updown_folder = root / "UPDOWN" / "01-cnv"
        if not cnv_folder.is_dir():
            raise ValueError(f"Folder does not exist: {cnv_folder}")
        if not updown_folder.is_dir():
            raise ValueError(f"Folder does not exist: {updown_folder}")
        cnv_files = files_by_casefolded_name(cnv_folder)
        updown_files = files_by_casefolded_name(updown_folder)
    except ValueError as error:
        parser.error(str(error))

    matching_names = cnv_files.keys() & updown_files.keys()
    comparisons: list[tuple[float, str, int, int, float | None]] = []
    had_errors = False
    for key in matching_names:
        cnv_path = cnv_files[key]
        updown_path = updown_files[key]
        try:
            cnv_length = cnv_data_length(cnv_path)
            _variables, updown_length, updown_max_pressure = inspect_cnv(
                updown_path
            )
            comparisons.append(
                (
                    relative_length(cnv_length, updown_length),
                    cnv_path.name,
                    cnv_length,
                    updown_length,
                    updown_max_pressure,
                )
            )
        except (OSError, ValueError) as error:
            had_errors = True
            print(f"Could not compare {cnv_path.name}: {error}", file=sys.stderr)

    comparisons.sort(key=lambda item: (item[0], item[1].casefold()))

    print(
        "File\tCNV/01-cnv rows\tUPDOWN/01-cnv rows\t"
        "Row difference\tCNV length relative to UPDOWN\t"
        "UPDOWN maximum pressure (prDM)"
    )
    for (
        relative,
        filename,
        cnv_length,
        updown_length,
        updown_max_pressure,
    ) in comparisons:
        relative_text = "Infinity" if math.isinf(relative) else f"{relative:.2%}"
        pressure_text = (
            f"{updown_max_pressure:.7g}"
            if updown_max_pressure is not None
            else "Unavailable"
        )
        print(
            f"{filename}\t{cnv_length}\t{updown_length}\t"
            f"{updown_length - cnv_length:+d}\t{relative_text}\t"
            f"{pressure_text}"
        )

    only_cnv = sorted(
        (cnv_files[key].name for key in cnv_files.keys() - updown_files.keys()),
        key=str.casefold,
    )
    only_updown = sorted(
        (
            updown_files[key].name
            for key in updown_files.keys() - cnv_files.keys()
        ),
        key=str.casefold,
    )
    for filename in only_cnv:
        print(f"No UPDOWN/01-cnv match for {filename}", file=sys.stderr)
    for filename in only_updown:
        print(f"No CNV/01-cnv match for {filename}", file=sys.stderr)

    if not matching_names:
        print("No matching CNV filenames were found.", file=sys.stderr)
    return 1 if had_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
