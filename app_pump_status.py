from __future__ import annotations

import argparse
import io
import os
import re
from pathlib import Path, PureWindowsPath

import pandas as pd
from dash import Dash, Input, Output, State, dcc, html, no_update


SCRIPT_DIR = Path(__file__).resolve().parent
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


def path_from_text(folder_text: str, relative_to: Path) -> Path:
    folder_text = os.path.expandvars(folder_text.strip())
    if (
        len(folder_text) >= 2
        and folder_text[0] == folder_text[-1]
        and folder_text[0] in {'"', "'"}
    ):
        folder_text = folder_text[1:-1].strip()

    windows_path = PureWindowsPath(folder_text)
    if (
        os.name != "nt"
        and windows_path.is_absolute()
        and len(windows_path.drive) == 2
        and windows_path.drive[1] == ":"
    ):
        folder = (
            Path("/mnt")
            / windows_path.drive[0].lower()
            / Path(*windows_path.parts[1:])
        )
    else:
        folder = Path(os.path.normpath(folder_text)).expanduser()

    if not folder.is_absolute():
        folder = relative_to / folder
    return folder.resolve()


def command_line_search_root() -> Path:
    parser = argparse.ArgumentParser(
        description="Analyze pump status in CNV files under one folder."
    )
    parser.add_argument(
        "folder",
        nargs="?",
        default=str(SCRIPT_DIR),
        help="Root folder beneath which CNV files may be discovered.",
    )
    arguments = parser.parse_args()
    search_root = path_from_text(arguments.folder, SCRIPT_DIR)
    if not search_root.is_dir():
        parser.error(f"Folder does not exist: {search_root}")
    return search_root


SEARCH_ROOT = (
    command_line_search_root() if __name__ == "__main__" else SCRIPT_DIR
)


def discover_cnv_folders() -> list[Path]:
    folders = {path.parent.resolve() for path in SEARCH_ROOT.rglob("*.cnv")}
    return sorted(folders, key=lambda path: str(path).casefold())


def folder_options() -> list[dict[str, str]]:
    options = []
    for folder in discover_cnv_folders():
        try:
            label = str(folder.relative_to(SEARCH_ROOT))
        except ValueError:
            label = str(folder)
        options.append({"label": label, "value": str(folder)})
    return options


def resolve_folder(selected_folder: str | None, custom_folder: str | None) -> Path:
    folder_text = (custom_folder or "").strip() or (selected_folder or "").strip()
    if not folder_text:
        raise ValueError("Select a discovered folder or enter a custom folder path.")

    folder = path_from_text(folder_text, SEARCH_ROOT)

    if not folder.is_dir():
        raise ValueError(f"Folder does not exist: {folder}")
    try:
        folder.relative_to(SEARCH_ROOT)
    except ValueError as error:
        raise ValueError(
            f"Folder must be inside the search root: {SEARCH_ROOT}"
        ) from error
    return folder


def read_cnv_pump_status(
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
    pump_candidates = [
        name
        for name in column_names
        if name.casefold() == "pumps" or "pump status" in name.casefold()
    ]
    if not pump_candidates:
        raise ValueError("no 'pumps: Pump Status' column")
    pump_column = pump_candidates[0]
    pressure_candidates = [
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
    pump_status = pd.to_numeric(frame[pump_column], errors="coerce")
    pressure = (
        pd.to_numeric(frame[pressure_candidates[0]], errors="coerce")
        if pressure_candidates
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


def classify_files(folder: Path):
    zero_only: list[tuple[str, int]] = []
    pump_on: list[
        tuple[
            str,
            int,
            int,
            int,
            int,
            float | None,
            float | None,
            float | None,
            float | None,
        ]
    ] = []
    errors: list[tuple[str, str]] = []

    files = sorted(folder.glob("*.cnv"), key=lambda path: path.name.casefold())
    for path in files:
        try:
            pump_status, pressure, sampling_interval = read_cnv_pump_status(path)
            valid_status = pump_status.dropna()
            total_length = len(pump_status)

            if valid_status.empty:
                errors.append((path.name, "pump status has no numeric values"))
                continue

            pump_on_rows = pump_status.index[pump_status.eq(1)].tolist()
            if pump_on_rows:
                transition_rows = pump_status.index[
                    pump_status.eq(1) & pump_status.shift(fill_value=0).eq(0)
                ].tolist()
                first_transition = transition_rows[0] if transition_rows else pump_on_rows[0]
                last_pump_on = pump_on_rows[-1]
                pump_on_length = len(pump_on_rows)
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
                    pump_on_length * sampling_interval
                    if sampling_interval is not None
                    else None
                )
                pump_on.append(
                    (
                        path.name,
                        first_transition,
                        last_pump_on,
                        pump_on_length,
                        total_length,
                        sampling_interval,
                        pump_on_seconds,
                        transition_pressure,
                        maximum_pressure,
                    )
                )
            elif valid_status.eq(0).all():
                zero_only.append((path.name, total_length))
            else:
                unexpected = sorted(valid_status.unique().tolist())
                errors.append(
                    (path.name, f"contains neither pump-on values nor only zeroes: {unexpected}")
                )
        except Exception as error:
            errors.append((path.name, str(error)))

    pump_on.sort(
        key=lambda item: (
            item[6] is None,
            item[6] if item[6] is not None else float("inf"),
            item[0].casefold(),
        )
    )
    return files, zero_only, pump_on, errors


def result_table(headers: list[str], rows: list[list[str]]) -> html.Div:
    body_rows = (
        [html.Tr([html.Td(value) for value in row]) for row in rows]
        if rows
        else [
            html.Tr(
                [
                    html.Td(
                        "No files",
                        colSpan=len(headers),
                        className="empty-table-cell",
                    )
                ]
            )
        ]
    )
    return html.Div(
        html.Table(
            [
                html.Thead(html.Tr([html.Th(header) for header in headers])),
                html.Tbody(body_rows),
            ],
            className="report-table",
        ),
        className="report-table-wrap",
    )


INITIAL_OPTIONS = folder_options()
INITIAL_FOLDER = INITIAL_OPTIONS[0]["value"] if INITIAL_OPTIONS else None

app = Dash(__name__)
app.title = "CNV Pump Status Report"
app.layout = html.Div(
    [
        html.H1("CNV Pump Status Report"),
        html.Div(f"Search root: {SEARCH_ROOT}", className="search-root"),
        html.Div(
            [
                html.Div(
                    [
                        html.Label(
                            "Discovered CNV folder under search root",
                            htmlFor="pump-folder",
                        ),
                        dcc.Dropdown(
                            id="pump-folder",
                            options=INITIAL_OPTIONS,
                            value=INITIAL_FOLDER,
                            clearable=True,
                        ),
                    ],
                    className="pump-folder-control",
                ),
                html.Div(
                    [
                        html.Label(
                            "Custom folder path (optional override)",
                            htmlFor="custom-pump-folder",
                        ),
                        dcc.Input(
                            id="custom-pump-folder",
                            type="text",
                            placeholder=r"Example: D:\data\06-drv",
                            debounce=True,
                        ),
                    ],
                    className="pump-folder-control",
                ),
                html.Button("Analyze folder", id="analyze-pumps", n_clicks=0),
            ],
            className="pump-controls",
        ),
        html.Div(
            [
                html.Div(
                    [
                        html.Label(
                            "Maximum pump-on time to report (seconds)",
                            htmlFor="pump-time-threshold",
                        ),
                        dcc.Input(
                            id="pump-time-threshold",
                            type="number",
                            value=60,
                            min=0,
                            step="any",
                        ),
                    ],
                    className="pump-threshold-control",
                ),
                html.Button(
                    "Write CSV report",
                    id="write-pump-report",
                    n_clicks=0,
                ),
                html.Div(id="pump-export-status", className="status"),
            ],
            className="pump-export-controls",
        ),
        dcc.Store(id="pump-report-data"),
        dcc.Download(id="pump-report-download"),
        dcc.Loading(
            html.Div(
                [
                    html.Div(id="pump-summary", className="status"),
                    html.Div(
                        [
                            html.Section(
                                [
                                    html.H2("Files containing only pump status 0"),
                                    html.Div(id="zero-only-files"),
                                ],
                                className="report-card",
                            ),
                            html.Section(
                                [
                                    html.H2("Files containing pump status 1"),
                                    html.P(
                                        "The first 0-to-1 transition index is zero-based from "
                                        "the first data row after *END*. Pump-on duration is "
                                        "the number of rows equal to 1 multiplied by the "
                                        "header sampling interval.",
                                        className="report-note",
                                    ),
                                    html.Div(id="pump-on-files"),
                                ],
                                className="report-card",
                            ),
                        ],
                        className="report-tables",
                    ),
                    html.Section(
                        [
                            html.H2("Files that could not be classified"),
                            html.Div(id="pump-errors"),
                        ],
                        id="error-card",
                        className="report-card error-card",
                    ),
                ]
            ),
            type="circle",
        ),
    ],
    className="page",
)


@app.callback(
    Output("pump-summary", "children"),
    Output("zero-only-files", "children"),
    Output("pump-on-files", "children"),
    Output("pump-errors", "children"),
    Output("error-card", "style"),
    Output("pump-report-data", "data"),
    Input("analyze-pumps", "n_clicks"),
    State("pump-folder", "value"),
    State("custom-pump-folder", "value"),
    prevent_initial_call=True,
)
def analyze_folder(
    _n_clicks: int,
    selected_folder: str | None,
    custom_folder: str | None,
):
    try:
        folder = resolve_folder(selected_folder, custom_folder)
        files, zero_only, pump_on, errors = classify_files(folder)
    except Exception as error:
        return str(error), [], [], [], {"display": "none"}, None

    zero_table = result_table(
        ["File", "Total rows"],
        [
            [name, f"{total_length:,}"]
            for name, total_length in zero_only
        ],
    )
    pump_on_rows = [
        [
            name,
            f"{first_index:,}",
            (
                f"{transition_pressure:,.7g}"
                if transition_pressure is not None
                else "Unavailable"
            ),
            (
                f"{maximum_pressure:,.7g}"
                if maximum_pressure is not None
                else "Unavailable"
            ),
            f"{last_index:,}",
            f"{pump_on_length:,}",
            (
                f"{sampling_interval:.7g}"
                if sampling_interval is not None
                else "Unavailable"
            ),
            (
                f"{pump_on_seconds:,.7g}"
                if pump_on_seconds is not None
                else "Unavailable"
            ),
            f"{total_length:,}",
        ]
        for (
            name,
            first_index,
            last_index,
            pump_on_length,
            total_length,
            sampling_interval,
            pump_on_seconds,
            transition_pressure,
            maximum_pressure,
        ) in pump_on
    ]
    pump_on_table = result_table(
        [
            "File",
            "First 0→1 index",
            "Transition pressure (dbar)",
            "Maximum pressure (dbar)",
            "Last pump-on index",
            "Pump-on rows",
            "Sampling interval (s)",
            "Pump-on duration (s)",
            "Total rows",
        ],
        pump_on_rows,
    )
    error_table = result_table(
        ["File", "Error"],
        [
            [name, message]
            for name, message in errors
        ],
    )

    summary = (
        f"Analyzed {len(files):,} CNV files in {folder}. "
        f"Zero-only: {len(zero_only):,}; pump-on: {len(pump_on):,}; "
        f"unclassified: {len(errors):,}."
    )
    report_rows = [
        {
            "File": name,
            "Classification": "Pump status 0 only",
            "First 0→1 index": None,
            "Transition pressure (dbar)": None,
            "Maximum pressure (dbar)": None,
            "Last pump-on index": None,
            "Pump-on rows": 0,
            "Sampling interval (s)": None,
            "Pump-on duration (s)": 0.0,
            "Total rows": total_length,
        }
        for name, total_length in zero_only
    ]
    report_rows.extend(
        {
            "File": name,
            "Classification": "Pump status 1 present",
            "First 0→1 index": first_index,
            "Transition pressure (dbar)": transition_pressure,
            "Maximum pressure (dbar)": maximum_pressure,
            "Last pump-on index": last_index,
            "Pump-on rows": pump_on_length,
            "Sampling interval (s)": sampling_interval,
            "Pump-on duration (s)": pump_on_seconds,
            "Total rows": total_length,
        }
        for (
            name,
            first_index,
            last_index,
            pump_on_length,
            total_length,
            sampling_interval,
            pump_on_seconds,
            transition_pressure,
            maximum_pressure,
        ) in pump_on
    )
    error_style = {} if errors else {"display": "none"}
    report_data = {"folder": str(folder), "rows": report_rows}
    return (
        summary,
        zero_table,
        pump_on_table,
        error_table,
        error_style,
        report_data,
    )


@app.callback(
    Output("pump-report-download", "data"),
    Output("pump-export-status", "children"),
    Input("write-pump-report", "n_clicks"),
    State("pump-time-threshold", "value"),
    State("pump-report-data", "data"),
    State("pump-folder", "value"),
    State("custom-pump-folder", "value"),
    prevent_initial_call=True,
)
def write_csv_report(
    _n_clicks: int,
    threshold_value: int | float | None,
    report_data: dict | None,
    selected_folder: str | None,
    custom_folder: str | None,
):
    if not report_data:
        return no_update, "Analyze a folder before writing the CSV report."

    try:
        current_folder = resolve_folder(selected_folder, custom_folder)
    except Exception:
        return no_update, "Select and analyze a valid folder first."
    if str(current_folder) != report_data.get("folder"):
        return no_update, "Analyze the currently selected folder before exporting."

    try:
        threshold = float(threshold_value)
    except (TypeError, ValueError):
        return no_update, "Enter a pump-on time threshold in seconds."
    if threshold < 0:
        return no_update, "The pump-on time threshold cannot be negative."

    matching_rows = [
        {
            "Source folder": report_data["folder"],
            "Threshold (s)": threshold,
            **row,
        }
        for row in report_data.get("rows", [])
        if row.get("Pump-on duration (s)") is not None
        and float(row["Pump-on duration (s)"]) <= threshold
    ]
    columns = [
        "Source folder",
        "Threshold (s)",
        "File",
        "Classification",
        "First 0→1 index",
        "Transition pressure (dbar)",
        "Maximum pressure (dbar)",
        "Last pump-on index",
        "Pump-on rows",
        "Sampling interval (s)",
        "Pump-on duration (s)",
        "Total rows",
    ]
    report_frame = pd.DataFrame(matching_rows, columns=columns)
    threshold_text = f"{threshold:g}".replace(".", "_")
    download = dcc.send_data_frame(
        report_frame.to_csv,
        f"pump_status_at_or_below_{threshold_text}_seconds.csv",
        index=False,
    )
    status = (
        f"CSV report contains {len(matching_rows):,} files with pump-on time "
        f"at or below {threshold:g} seconds."
    )
    return download, status


if __name__ == "__main__":
    app.run(debug=True, port=8051, use_reloader=False)
