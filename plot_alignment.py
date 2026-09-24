from __future__ import annotations

import argparse
import io
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PureWindowsPath

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from dash import Dash, Input, Output, State, dcc, html


BASE_DIR = Path(__file__).resolve().parent
DRIVER_FOLDER = "06-drv"
VARIABLE_COUNT = 5

NAME_PATTERN = re.compile(
    r"^#\s*name\s*(?P<index>\d+)?\s*=\s*"
    r"(?P<name>[^:]+?)(?:\s*:\s*(?P<description>.*))?$"
)
BAD_FLAG_PATTERN = re.compile(
    r"^#\s*bad_flag\s*=\s*(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)"
)

COLORS = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00")


def resolve_root_folder(folder_text: str) -> Path:
    folder_text = os.path.expandvars(folder_text.strip().strip("\"'"))
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

    folder = folder.resolve()
    if not folder.is_dir():
        raise ValueError(f"Root folder does not exist: {folder}")
    return folder


def has_alignment_structure(folder: Path) -> bool:
    out_dir = folder / "OUT"
    return out_dir.is_dir() and any(
        child.is_dir() and (child / DRIVER_FOLDER).is_dir()
        for child in out_dir.iterdir()
    )


def select_alignment_folder(search_root: Path) -> Path:
    if has_alignment_structure(search_root):
        return search_root

    candidates = sorted(
        (
            child.resolve()
            for child in search_root.iterdir()
            if child.is_dir() and has_alignment_structure(child)
        ),
        key=lambda path: path.name.casefold(),
    )
    if not candidates:
        raise ValueError(
            f"No folder containing OUT/*/{DRIVER_FOLDER} was found directly "
            f"under {search_root}."
        )
    if len(candidates) == 1:
        print(f"Using alignment folder: {candidates[0]}")
        return candidates[0]

    print(f"Multiple alignment folders were found under {search_root}:")
    for index, candidate in enumerate(candidates, start=1):
        print(f"  {index}. {candidate.name}")

    while True:
        response = input(f"Select a folder [1-{len(candidates)}]: ").strip()
        if response.isdigit():
            selected_index = int(response)
            if 1 <= selected_index <= len(candidates):
                return candidates[selected_index - 1]
        print("Enter one of the listed folder numbers.")


def command_line_alignment_folder() -> Path:
    parser = argparse.ArgumentParser(
        description="Plot matching CNV files from an OUT/*/06-drv structure."
    )
    parser.add_argument(
        "root_folder",
        nargs="?",
        default=str(BASE_DIR),
        help=(
            "Folder containing OUT/*/06-drv, or a folder whose immediate "
            "subfolders contain that structure."
        ),
    )
    arguments = parser.parse_args()
    try:
        search_root = resolve_root_folder(arguments.root_folder)
        return select_alignment_folder(search_root)
    except ValueError as error:
        parser.error(str(error))


ALIGNMENT_DIR = (
    command_line_alignment_folder() if __name__ == "__main__" else BASE_DIR
)
OUT_DIR = ALIGNMENT_DIR / "OUT"


@dataclass(frozen=True)
class CnvData:
    frame: pd.DataFrame
    labels: dict[str, str]


def discover_data_folders() -> dict[str, Path]:
    if not OUT_DIR.is_dir():
        return {}

    folders: dict[str, Path] = {}
    for child in sorted((path for path in OUT_DIR.iterdir() if path.is_dir()), key=lambda p: p.name):
        driver = child / DRIVER_FOLDER
        if driver.is_dir() and any(driver.glob("*.cnv")):
            folders[child.name] = driver
    return folders


DATA_FOLDERS = discover_data_folders()


def discover_common_filenames() -> list[str]:
    if not DATA_FOLDERS:
        return []

    filename_sets = [
        {path.name for path in driver.glob("*.cnv")}
        for driver in DATA_FOLDERS.values()
    ]
    return sorted(set.intersection(*filename_sets), key=str.casefold)


FILENAMES = discover_common_filenames()


def _parse_cnv(path: Path) -> CnvData:
    column_entries: list[tuple[int, str, str]] = []
    data_lines: list[str] = []
    bad_flag: float | None = None
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
                index = int(index_text) if index_text is not None else len(column_entries)
                name = name_match.group("name").strip()
                description = (name_match.group("description") or name).strip()
                column_entries.append((index, name, description))
                continue

            bad_flag_match = BAD_FLAG_PATTERN.match(stripped)
            if bad_flag_match:
                bad_flag = float(bad_flag_match.group("value"))

    if not column_entries:
        raise ValueError(f"No '# name' column definitions found in {path.name}")
    if not in_data:
        raise ValueError(f"No '*END*' marker found in {path.name}")

    column_entries.sort(key=lambda entry: entry[0])
    columns = [entry[1] for entry in column_entries]
    labels = {entry[1]: entry[2] for entry in column_entries}

    frame = pd.read_csv(
        io.StringIO("\n".join(data_lines)),
        sep=r"\s+",
        names=columns,
        usecols=range(len(columns)),
        engine="python",
        on_bad_lines="skip",
    )
    frame = frame.apply(pd.to_numeric, errors="coerce")
    if bad_flag is not None:
        frame = frame.mask(frame == bad_flag)

    return CnvData(frame=frame, labels=labels)


@lru_cache(maxsize=128)
def load_cnv(path_text: str, modified_ns: int) -> CnvData:
    del modified_ns  # It is part of the cache key so changed files are reloaded.
    return _parse_cnv(Path(path_text))


def load_folder_file(driver: Path, filename: str) -> CnvData:
    path = driver / filename
    return load_cnv(str(path), path.stat().st_mtime_ns)


def find_pressure_column(columns: list[str]) -> str | None:
    preferred = ("prDM", "pressure", "Pressure")
    for candidate in preferred:
        if candidate in columns:
            return candidate
    return next(
        (column for column in columns if "pressure" in column.casefold()),
        None,
    )


def common_axis_columns(filename: str | None) -> tuple[list[str], dict[str, str]]:
    if not filename:
        return [], {}

    datasets = [
        load_folder_file(driver, filename)
        for driver in DATA_FOLDERS.values()
    ]
    shared = set(datasets[0].frame.columns)
    for dataset in datasets[1:]:
        shared &= set(dataset.frame.columns)

    ordered = [column for column in datasets[0].frame.columns if column in shared]
    labels = {
        column: datasets[0].labels.get(column, column)
        for column in ordered
    }
    return ordered, labels


def common_columns(filename: str | None) -> tuple[list[str], dict[str, str]]:
    ordered, labels = common_axis_columns(filename)
    pressure = find_pressure_column(ordered)
    variables = [column for column in ordered if column != pressure]
    return variables, {column: labels[column] for column in variables}


def default_variables(columns: list[str]) -> list[str | None]:
    preferred = ("t090C", "sal00", "sbeox0ML/L", "flECO-AFL", "CStarTr0")
    selected = [column for column in preferred if column in columns]
    selected.extend(column for column in columns if column not in selected)
    values: list[str | None] = selected[:VARIABLE_COUNT]
    return values + [None] * (VARIABLE_COUNT - len(values))


def make_figure(
    folder_name: str,
    dataset: CnvData,
    variables: list[str],
    scans_to_drop: int = 0,
) -> go.Figure:
    frame = dataset.frame.iloc[scans_to_drop:]
    pressure = find_pressure_column(list(frame.columns))
    valid_variables = [variable for variable in variables if variable in frame.columns]

    if pressure is None or not valid_variables:
        figure = go.Figure()
        figure.add_annotation(
            text=(
                "No pressure column was found in this file."
                if pressure is None
                else "Select at least one x variable."
            ),
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
        )
    else:
        figure = make_subplots(
            rows=1,
            cols=len(valid_variables),
            shared_yaxes=True,
            horizontal_spacing=0.035,
            subplot_titles=valid_variables,
        )
        for index, variable in enumerate(valid_variables):
            figure.add_trace(
                go.Scattergl(
                    x=frame[variable],
                    y=frame[pressure],
                    mode="lines",
                    name=variable,
                    line={"color": COLORS[index], "width": 1.5},
                    hovertemplate=(
                        f"{dataset.labels.get(variable, variable)}: %{{x:.5g}}"
                        f"<br>{dataset.labels.get(pressure, pressure)}: "
                        "%{y:.5g}<extra></extra>"
                    ),
                ),
                row=1,
                col=index + 1,
            )
            figure.update_xaxes(
                title_text=dataset.labels.get(variable, variable),
                title_font={"color": COLORS[index], "size": 11},
                tickfont={"color": COLORS[index], "size": 10},
                showgrid=True,
                gridcolor="#F3F4F6",
                zeroline=False,
                row=1,
                col=index + 1,
            )

    figure.update_layout(
        title={"text": folder_name, "x": 0.5, "xanchor": "center"},
        height=720,
        margin={"l": 85, "r": 35, "t": 80, "b": 125},
        hovermode="closest",
        showlegend=False,
        plot_bgcolor="#FFFFFF",
        paper_bgcolor="#FFFFFF",
    )
    if pressure is not None and valid_variables:
        figure.update_yaxes(
            title_text=dataset.labels.get(pressure, pressure),
            autorange="reversed",
            showgrid=True,
            gridcolor="#E5E7EB",
            zeroline=False,
            row=1,
            col=1,
        )
    return figure


def make_xy_figure(
    folder_name: str,
    dataset: CnvData,
    x_variable: str,
    y_variable: str,
    scans_to_drop: int = 0,
) -> go.Figure:
    frame = dataset.frame.iloc[scans_to_drop:]
    if x_variable not in frame.columns or y_variable not in frame.columns:
        figure = go.Figure()
        figure.add_annotation(
            text="The selected X or Y variable is not available in this file.",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
        )
    else:
        x_label = dataset.labels.get(x_variable, x_variable)
        y_label = dataset.labels.get(y_variable, y_variable)
        figure = go.Figure(
            go.Scattergl(
                x=frame[x_variable],
                y=frame[y_variable],
                mode="lines",
                line={"color": COLORS[0], "width": 1.5},
                hovertemplate=(
                    f"{x_label}: %{{x:.5g}}"
                    f"<br>{y_label}: %{{y:.5g}}<extra></extra>"
                ),
            )
        )
        figure.update_xaxes(
            title_text=x_label,
            showgrid=True,
            gridcolor="#F3F4F6",
            zeroline=False,
        )
        figure.update_yaxes(
            title_text=y_label,
            autorange="reversed",
            showgrid=True,
            gridcolor="#E5E7EB",
            zeroline=False,
        )

    figure.update_layout(
        title={"text": folder_name, "x": 0.5, "xanchor": "center"},
        height=720,
        margin={"l": 85, "r": 35, "t": 80, "b": 90},
        hovermode="closest",
        showlegend=False,
        plot_bgcolor="#FFFFFF",
        paper_bgcolor="#FFFFFF",
    )
    return figure


def dropdown_options(columns: list[str], labels: dict[str, str]) -> list[dict[str, str]]:
    return [
        {"label": f"{column} — {labels.get(column, column)}", "value": column}
        for column in columns
    ]


def build_layout() -> html.Div:
    initial_filename = FILENAMES[0] if FILENAMES else None
    try:
        initial_columns, initial_labels = common_columns(initial_filename)
        initial_variables = default_variables(initial_columns)
        options = dropdown_options(initial_columns, initial_labels)
        axis_columns, axis_labels = common_axis_columns(initial_filename)
        axis_options = dropdown_options(axis_columns, axis_labels)
        initial_y = find_pressure_column(axis_columns)
        initial_x = next(
            (
                variable
                for variable in initial_variables
                if variable is not None and variable != initial_y
            ),
            next(
                (column for column in axis_columns if column != initial_y),
                None,
            ),
        )
    except Exception:
        initial_variables = [None] * VARIABLE_COUNT
        options = []
        axis_options = []
        initial_x = None
        initial_y = None

    controls = [
        html.Div(
            [
                html.Label(f"X variable {index + 1}", htmlFor=f"variable-{index}"),
                dcc.Dropdown(
                    id=f"variable-{index}",
                    options=options,
                    value=initial_variables[index],
                    clearable=True,
                ),
            ],
            className="variable-control",
        )
        for index in range(VARIABLE_COUNT)
    ]

    return html.Div(
        [
            html.H1("CTD Profile Explorer"),
            html.Div(
                [
                    html.Div(
                        [
                            html.Label("CNV file", htmlFor="filename"),
                            dcc.Dropdown(
                                id="filename",
                                options=[
                                    {"label": name, "value": name}
                                    for name in FILENAMES
                                ],
                                value=initial_filename,
                                clearable=False,
                            ),
                        ],
                        className="filename-control",
                    ),
                    html.Div(
                        [
                            html.Label(
                                "Scans to drop from beginning",
                                htmlFor="scans-to-drop",
                            ),
                            dcc.Input(
                                id="scans-to-drop",
                                type="number",
                                value=0,
                                min=0,
                                step=1,
                                debounce=True,
                            ),
                        ],
                        className="scan-control",
                    ),
                ],
                className="file-control",
            ),
            dcc.Tabs(
                [
                    dcc.Tab(
                        label="Pressure profiles",
                        value="pressure-profiles",
                        children=[
                            html.Div(controls, className="variable-grid"),
                            html.Div(id="status", className="status"),
                            dcc.Loading(html.Div(id="graphs"), type="circle"),
                        ],
                    ),
                    dcc.Tab(
                        label="Custom X/Y axes",
                        value="custom-axes",
                        children=[
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.Label(
                                                "X variable",
                                                htmlFor="custom-x-variable",
                                            ),
                                            dcc.Dropdown(
                                                id="custom-x-variable",
                                                options=axis_options,
                                                value=initial_x,
                                                clearable=False,
                                            ),
                                        ]
                                    ),
                                    html.Div(
                                        [
                                            html.Label(
                                                "Y variable",
                                                htmlFor="custom-y-variable",
                                            ),
                                            dcc.Dropdown(
                                                id="custom-y-variable",
                                                options=axis_options,
                                                value=initial_y,
                                                clearable=False,
                                            ),
                                        ]
                                    ),
                                ],
                                className="custom-axis-controls",
                            ),
                            html.Div(id="custom-axis-status", className="status"),
                            dcc.Loading(
                                html.Div(id="custom-axis-graphs"),
                                type="circle",
                            ),
                        ],
                    ),
                ],
                value="pressure-profiles",
                className="profile-tabs",
            ),
        ],
        className="page",
    )


app = Dash(__name__)
app.title = "CTD Profile Explorer"
app.layout = build_layout()


@app.callback(
    [
        output
        for index in range(VARIABLE_COUNT)
        for output in (
            Output(f"variable-{index}", "options"),
            Output(f"variable-{index}", "value"),
        )
    ],
    Input("filename", "value"),
    [State(f"variable-{index}", "value") for index in range(VARIABLE_COUNT)],
)
def update_variable_controls(filename: str | None, *current_values: str | None):
    try:
        columns, labels = common_columns(filename)
        options = dropdown_options(columns, labels)
        defaults = default_variables(columns)
        values = [
            current if current in columns else defaults[index]
            for index, current in enumerate(current_values)
        ]
        return [
            item
            for value in values
            for item in (options, value)
        ]
    except Exception:
        return [
            item
            for _ in range(VARIABLE_COUNT)
            for item in ([], None)
        ]


@app.callback(
    Output("custom-x-variable", "options"),
    Output("custom-x-variable", "value"),
    Output("custom-y-variable", "options"),
    Output("custom-y-variable", "value"),
    Input("filename", "value"),
    State("custom-x-variable", "value"),
    State("custom-y-variable", "value"),
)
def update_custom_axis_controls(
    filename: str | None,
    current_x: str | None,
    current_y: str | None,
):
    try:
        columns, labels = common_axis_columns(filename)
        options = dropdown_options(columns, labels)
        default_y = find_pressure_column(columns)
        if default_y is None and columns:
            default_y = columns[0]
        default_x = next(
            (column for column in columns if column != default_y),
            default_y,
        )
        x_value = current_x if current_x in columns else default_x
        y_value = current_y if current_y in columns else default_y
        return options, x_value, options, y_value
    except Exception:
        return [], None, [], None


@app.callback(
    Output("graphs", "children"),
    Output("status", "children"),
    Input("filename", "value"),
    Input("scans-to-drop", "value"),
    [Input(f"variable-{index}", "value") for index in range(VARIABLE_COUNT)],
)
def update_graphs(
    filename: str | None,
    scans_to_drop: int | float | None,
    *selected_variables: str | None,
):
    if not DATA_FOLDERS:
        return [], f"No OUT/*/{DRIVER_FOLDER} folders containing CNV files were found."
    if not FILENAMES:
        return [], "No CNV filename is common to every data folder."
    if not filename:
        return [], "Select a CNV file."

    drop_count = max(0, int(scans_to_drop or 0))
    variables = list(dict.fromkeys(value for value in selected_variables if value))
    graph_components = []
    errors = []

    for folder_name, driver in DATA_FOLDERS.items():
        try:
            source_path = driver / filename
            dataset = load_folder_file(driver, filename)
            figure = make_figure(folder_name, dataset, variables, drop_count)
            graph_components.append(
                html.Div(
                    [
                        html.Div(
                            f"Source: {source_path.relative_to(ALIGNMENT_DIR)}",
                            className="source-path",
                        ),
                        dcc.Graph(
                            id={"type": "profile-graph", "folder": folder_name},
                            figure=figure,
                            config={
                                "displaylogo": False,
                                "responsive": True,
                                "scrollZoom": True,
                            },
                        ),
                    ],
                    className="graph-card",
                )
            )
        except Exception as error:
            errors.append(f"{folder_name}: {error}")

    status = (
        (
            f"Showing {filename} in {len(graph_components)} folders "
            f"from {len(graph_components)} distinct source paths "
            f"after dropping {drop_count:,} scans."
        )
        if not errors
        else "Could not load " + "; ".join(errors)
    )
    return graph_components, status


@app.callback(
    Output("custom-axis-graphs", "children"),
    Output("custom-axis-status", "children"),
    Input("filename", "value"),
    Input("scans-to-drop", "value"),
    Input("custom-x-variable", "value"),
    Input("custom-y-variable", "value"),
)
def update_custom_axis_graphs(
    filename: str | None,
    scans_to_drop: int | float | None,
    x_variable: str | None,
    y_variable: str | None,
):
    if not DATA_FOLDERS:
        return [], f"No OUT/*/{DRIVER_FOLDER} folders containing CNV files were found."
    if not FILENAMES:
        return [], "No CNV filename is common to every data folder."
    if not filename:
        return [], "Select a CNV file."
    if not x_variable or not y_variable:
        return [], "Select both an X variable and a Y variable."

    drop_count = max(0, int(scans_to_drop or 0))
    graph_components = []
    errors = []
    for folder_name, driver in DATA_FOLDERS.items():
        try:
            source_path = driver / filename
            dataset = load_folder_file(driver, filename)
            figure = make_xy_figure(
                folder_name,
                dataset,
                x_variable,
                y_variable,
                drop_count,
            )
            graph_components.append(
                html.Div(
                    [
                        html.Div(
                            f"Source: {source_path.relative_to(ALIGNMENT_DIR)}",
                            className="source-path",
                        ),
                        dcc.Graph(
                            id={
                                "type": "custom-profile-graph",
                                "folder": folder_name,
                            },
                            figure=figure,
                            config={
                                "displaylogo": False,
                                "responsive": True,
                                "scrollZoom": True,
                            },
                        ),
                    ],
                    className="graph-card",
                )
            )
        except Exception as error:
            errors.append(f"{folder_name}: {error}")

    status = (
        (
            f"Showing {x_variable} versus {y_variable} for {filename} in "
            f"{len(graph_components)} folders after dropping "
            f"{drop_count:,} scans."
        )
        if not errors
        else "Could not load " + "; ".join(errors)
    )
    return graph_components, status


if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
