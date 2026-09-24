from __future__ import annotations

import argparse
import io
import re
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


NAME_PATTERN = re.compile(
    r"^#\s*name\s*(?P<index>\d+)?\s*=\s*"
    r"(?P<name>[^:]+?)(?:\s*:\s*(?P<description>.*))?$"
)
BAD_FLAG_PATTERN = re.compile(
    r"^#\s*bad_flag\s*=\s*(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)"
)
INTERVAL_PATTERN = re.compile(
    r"^#\s*interval\s*=\s*seconds:\s*"
    r"(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PumpOnReport:
    path: Path
    first_transition: int
    transition_pressure: float | None
    maximum_pressure: float | None
    last_pump_on: int
    pump_on_rows: int
    total_rows: int
    sampling_interval: float | None
    pump_on_seconds: float | None


def parse_pump_status(
    path: Path,
) -> tuple[pd.Series, pd.Series | None, float | None]:
    columns: list[tuple[int, str]] = []
    data_lines: list[str] = []
    bad_flag: float | None = None
    sampling_interval: float | None = None
    in_data = False

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if in_data:
                if stripped:
                    data_lines.append(stripped)
                continue

            if stripped == "*END*":
                in_data = True
                continue

            name_match = NAME_PATTERN.match(stripped)
            if name_match:
                index_text = name_match.group("index")
                index = int(index_text) if index_text is not None else len(columns)
                columns.append((index, name_match.group("name").strip()))
                continue

            bad_flag_match = BAD_FLAG_PATTERN.match(stripped)
            if bad_flag_match:
                bad_flag = float(bad_flag_match.group("value"))
                continue

            interval_match = INTERVAL_PATTERN.match(stripped)
            if interval_match:
                sampling_interval = float(interval_match.group("value"))

    if not columns:
        raise ValueError("no '# name' definitions")
    if not in_data:
        raise ValueError("no '*END*' marker")

    columns.sort(key=lambda item: item[0])
    column_names = [name for _, name in columns]
    pump_columns = [name for name in column_names if name.casefold() == "pumps"]
    if not pump_columns:
        raise ValueError("no 'pumps: Pump Status' column")
    pressure_columns = [
        name
        for name in column_names
        if name.casefold() == "prdm" or "pressure" in name.casefold()
    ]

    frame = pd.read_csv(
        io.StringIO("\n".join(data_lines)),
        sep=r"\s+",
        names=column_names,
        usecols=range(len(column_names)),
        engine="python",
        on_bad_lines="skip",
    )
    pump_status = pd.to_numeric(frame[pump_columns[0]], errors="coerce")
    pressure = (
        pd.to_numeric(frame[pressure_columns[0]], errors="coerce")
        if pressure_columns
        else None
    )
    if bad_flag is not None:
        pump_status = pump_status.mask(pump_status == bad_flag)
        if pressure is not None:
            pressure = pressure.mask(pressure == bad_flag)
    return (
        pump_status.reset_index(drop=True),
        pressure.reset_index(drop=True) if pressure is not None else None,
        sampling_interval,
    )


def find_cnv_files(directory: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.casefold() == ".cnv"
        ),
        key=lambda path: str(path.relative_to(directory)).casefold(),
    )


def analyze(directory: Path):
    zero_only: list[tuple[Path, int, float | None, float | None]] = []
    pump_on: list[PumpOnReport] = []
    errors: list[tuple[Path, str]] = []
    files = find_cnv_files(directory)

    for path in files:
        relative_path = path.relative_to(directory)
        try:
            pump_status, pressure, interval = parse_pump_status(path)
            valid_status = pump_status.dropna()
            total_rows = len(pump_status)

            if valid_status.empty:
                errors.append((relative_path, "pump status has no numeric values"))
                continue

            pump_on_indexes = pump_status.index[pump_status.eq(1)].tolist()
            if pump_on_indexes:
                transition_indexes = pump_status.index[
                    pump_status.eq(1) & pump_status.shift(fill_value=0).eq(0)
                ].tolist()
                first_transition = (
                    transition_indexes[0]
                    if transition_indexes
                    else pump_on_indexes[0]
                )
                pump_on_rows = len(pump_on_indexes)
                transition_pressure = (
                    float(pressure.iloc[first_transition])
                    if pressure is not None
                    and first_transition < len(pressure)
                    and pd.notna(pressure.iloc[first_transition])
                    else None
                )
                maximum_pressure = (
                    float(pressure.max())
                    if pressure is not None and pressure.notna().any()
                    else None
                )
                pump_on_seconds = (
                    pump_on_rows * interval if interval is not None else None
                )
                pump_on.append(
                    PumpOnReport(
                        path=relative_path,
                        first_transition=first_transition,
                        transition_pressure=transition_pressure,
                        maximum_pressure=maximum_pressure,
                        last_pump_on=pump_on_indexes[-1],
                        pump_on_rows=pump_on_rows,
                        total_rows=total_rows,
                        sampling_interval=interval,
                        pump_on_seconds=pump_on_seconds,
                    )
                )
            elif valid_status.eq(0).all():
                minimum_pressure = (
                    float(pressure.min())
                    if pressure is not None and pressure.notna().any()
                    else None
                )
                maximum_pressure = (
                    float(pressure.max())
                    if pressure is not None and pressure.notna().any()
                    else None
                )
                zero_only.append(
                    (
                        relative_path,
                        total_rows,
                        minimum_pressure,
                        maximum_pressure,
                    )
                )
            else:
                unexpected = sorted(valid_status.unique().tolist())
                errors.append(
                    (
                        relative_path,
                        f"contains neither pump-on values nor only zeroes: {unexpected}",
                    )
                )
        except Exception as error:
            errors.append((relative_path, str(error)))

    pump_on.sort(
        key=lambda report: (
            report.pump_on_seconds is None,
            (
                report.pump_on_seconds
                if report.pump_on_seconds is not None
                else float("inf")
            ),
            str(report.path).casefold(),
        )
    )
    return files, zero_only, pump_on, errors


def print_report(directory: Path) -> int:
    files, zero_only, pump_on, errors = analyze(directory)

    print(f"Directory: {directory}")
    print(f"CNV files analyzed: {len(files)}")
    print(
        f"Zero-only: {len(zero_only)}; pump-on: {len(pump_on)}; "
        f"unclassified: {len(errors)}"
    )

    print("\nFILES CONTAINING ONLY PUMP STATUS 0")
    if zero_only:
        for path, total_rows, minimum_pressure, maximum_pressure in zero_only:
            minimum_text = (
                f"{minimum_pressure:,.7g} dbar"
                if minimum_pressure is not None
                else "unavailable"
            )
            maximum_text = (
                f"{maximum_pressure:,.7g} dbar"
                if maximum_pressure is not None
                else "unavailable"
            )
            print(
                f"- {path} | minimum pressure: {minimum_text} | "
                f"maximum pressure: {maximum_text} | "
                f"total length: {total_rows:,} rows"
            )
    else:
        print("(none)")

    print("\nFILES CONTAINING PUMP STATUS 1 (SHORTEST DURATION FIRST)")
    if pump_on:
        for report in pump_on:
            if (
                report.sampling_interval is not None
                and report.pump_on_seconds is not None
            ):
                timing = (
                    f"sampling interval: {report.sampling_interval:.7g} s | "
                    f"pump-on duration: {report.pump_on_seconds:,.7g} s"
                )
            else:
                timing = "sampling interval: unavailable | pump-on duration: unavailable"
            pressure_text = (
                f"{report.transition_pressure:,.7g} dbar"
                if report.transition_pressure is not None
                else "unavailable"
            )
            maximum_pressure_text = (
                f"{report.maximum_pressure:,.7g} dbar"
                if report.maximum_pressure is not None
                else "unavailable"
            )

            print(
                f"- {report.path} | "
                f"first 0->1 transition index: {report.first_transition:,} | "
                f"transition pressure: {pressure_text} "
                f"(maximum: {maximum_pressure_text}) | "
                f"last pump-on index: {report.last_pump_on:,} | "
                f"pump-on length: {report.pump_on_rows:,} rows | "
                f"{timing} | total length: {report.total_rows:,} rows"
            )
    else:
        print("(none)")

    if errors:
        print("\nFILES THAT COULD NOT BE CLASSIFIED")
        for path, message in errors:
            print(f"- {path} | {message}")

    return 0 if files else 1


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Recursively classify CNV files by pump status and report pump-on "
            "indexes and duration."
        )
    )
    parser.add_argument(
        "directory",
        type=Path,
        help="Directory containing CNV files directly or in subfolders.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help=(
            "Output text file. Defaults to pump_status_report.txt inside "
            "the analyzed directory."
        ),
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    directory = arguments.directory.expanduser().resolve()
    if not directory.is_dir():
        print(f"Error: directory does not exist: {directory}", file=sys.stderr)
        return 2

    output_path = (
        arguments.output.expanduser().resolve()
        if arguments.output is not None
        else directory / "pump_status_report.txt"
    )
    if not output_path.parent.is_dir():
        print(
            f"Error: output directory does not exist: {output_path.parent}",
            file=sys.stderr,
        )
        return 2

    try:
        with output_path.open("w", encoding="utf-8") as output_file:
            with redirect_stdout(output_file):
                exit_code = print_report(directory)
    except OSError as error:
        print(f"Error writing report: {error}", file=sys.stderr)
        return 2

    print(f"Report written to: {output_path}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
