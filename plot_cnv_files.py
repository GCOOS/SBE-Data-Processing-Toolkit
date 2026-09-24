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
from dash import ALL, Dash, Input, Output, State, ctx, dcc, html, no_update
from plotly.subplots import make_subplots


NAME_PATTERN = re.compile(
    r"^#\s*name\s*(?P<index>\d+)?\s*=\s*"
    r"(?P<name>[^:]+?)(?:\s*:\s*(?P<description>.*))?$"
)
BAD_FLAG_PATTERN = re.compile(
    r"^#\s*bad_flag\s*=\s*"
    r"(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)"
)
EXCLUDED_VARIABLES = {"times", "latitude", "longitude", "flag"}
COLORS = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00")
SCRIPT_ROOT = Path(__file__).resolve().parent
SKIP_FOLDER_NAMES = frozenset(
    {
        "ctdenv",
        "__pycache__",
        ".git",
        "node_modules",
        ".venv",
        "venv",
    }
)


@dataclass(frozen=True)
class CnvData:
    frame: pd.DataFrame
    labels: dict[str, str]


def is_pressure_variable(variable: str) -> bool:
    return (
        variable.casefold() == "prdm"
        or "pressure" in variable.casefold()
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
        raise ValueError(f"Folder does not exist: {directory}")
    return directory


def command_line_folder() -> Path:
    parser = argparse.ArgumentParser(
        description=(
            "Visualize variables in CNV files. Folder lists include the "
            "script directory and its subfolders."
        )
    )
    parser.add_argument(
        "folder",
        help=(
            "Folder to open at startup. Other folders are listed from the "
            "directory that contains this script."
        ),
    )
    arguments = parser.parse_args()
    try:
        return resolve_directory(arguments.folder)
    except ValueError as error:
        parser.error(str(error))


def discover_folders(root: Path) -> list[Path]:
    folders = [root]
    for parent, names, _files in os.walk(root):
        names[:] = [
            name
            for name in names
            if name not in SKIP_FOLDER_NAMES and not name.startswith(".")
        ]
        parent_path = Path(parent)
        folders.extend((parent_path / name).resolve() for name in names)
    return folders


def folder_label(folder: Path, root: Path) -> str:
    if folder == root:
        return f"{root.name} (script root)"
    try:
        return str(folder.relative_to(root))
    except ValueError:
        return str(folder)


def browse_folder_options(
    root: Path,
    selected: Path | None = None,
) -> list[dict[str, str]]:
    folders = discover_folders(root)
    if selected is not None and selected not in folders:
        folders.append(selected)

    def sort_key(folder: Path) -> tuple[bool, str]:
        try:
            relative = str(folder.relative_to(root))
        except ValueError:
            relative = str(folder)
        return (folder != root, relative.casefold())

    return [
        {"label": folder_label(folder, root), "value": str(folder)}
        for folder in sorted(set(folders), key=sort_key)
    ]


def cnv_data_length(path: Path) -> int | None:
    row_count = 0
    in_data = False
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if in_data:
                    if stripped:
                        row_count += 1
                elif stripped == "*END*":
                    in_data = True
        return row_count if in_data else None
    except OSError:
        return None


def scan_folder(root: Path) -> dict:
    discovered_files: list[Path] = []
    for parent, names, filenames in os.walk(root):
        names[:] = [
            name
            for name in names
            if name not in SKIP_FOLDER_NAMES and not name.startswith(".")
        ]
        parent_path = Path(parent)
        discovered_files.extend(
            (parent_path / filename).resolve()
            for filename in filenames
            if Path(filename).suffix.casefold() == ".cnv"
        )
    file_lengths = {
        str(path): cnv_data_length(path)
        for path in discovered_files
    }
    files = sorted(
        discovered_files,
        key=lambda path: (
            file_lengths[str(path)] is None,
            (
                file_lengths[str(path)]
                if file_lengths[str(path)] is not None
                else float("inf")
            ),
            path.name.casefold(),
            str(path.relative_to(root)).casefold(),
        ),
    )
    return {
        "root": str(root),
        "files": [
            {
                "path": str(path),
                "name": path.name,
                "relative": str(path.relative_to(root)),
                "length": file_lengths[str(path)],
            }
            for path in files
        ],
    }


INITIAL_FOLDER = (
    command_line_folder() if __name__ == "__main__" else SCRIPT_ROOT
)
INITIAL_FOLDER_DATA = scan_folder(INITIAL_FOLDER)
FOLDER_OPTIONS = browse_folder_options(SCRIPT_ROOT, INITIAL_FOLDER)


@lru_cache(maxsize=32)
def load_cnv(path_text: str, modified_ns: int) -> CnvData:
    del modified_ns
    path = Path(path_text)
    columns: list[tuple[int, str, str]] = []
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
                index = int(index_text) if index_text is not None else len(columns)
                name = name_match.group("name").strip()
                description = (name_match.group("description") or name).strip()
                columns.append((index, name, description))
                continue

            bad_flag_match = BAD_FLAG_PATTERN.match(stripped)
            if bad_flag_match:
                bad_flag = float(bad_flag_match.group("value"))

    if not columns:
        raise ValueError("No '# name' column definitions were found.")
    if not in_data:
        raise ValueError("No '*END*' marker was found.")

    columns.sort(key=lambda item: item[0])
    names = [item[1] for item in columns]
    labels = {item[1]: item[2] for item in columns}
    if data_lines:
        frame = pd.read_csv(
            io.StringIO("\n".join(data_lines)),
            sep=r"\s+",
            names=names,
            usecols=range(len(names)),
            engine="python",
            on_bad_lines="skip",
        )
        frame = frame.apply(pd.to_numeric, errors="coerce")
    else:
        frame = pd.DataFrame(columns=names, dtype=float)

    if bad_flag is not None:
        frame = frame.mask(frame == bad_flag)
    return CnvData(frame=frame, labels=labels)


def plotted_variables(columns: list[str]) -> list[str]:
    included = [
        column
        for column in columns
        if column.casefold() not in EXCLUDED_VARIABLES
    ]
    pressure = next(
        (column for column in included if is_pressure_variable(column)),
        None,
    )
    if pressure is None:
        return included
    return [pressure, *(column for column in included if column != pressure)]


def plot_label_parts(variable: str, description: str) -> tuple[str, str]:
    match = re.match(r"^(?P<title>.*?)\s*\[(?P<unit>[^\[\]]+)\]\s*$", description)
    if match is None:
        return description, ""
    title = match.group("title").rstrip(" ,")
    return title or variable, match.group("unit").strip()


def make_figure(title: str, dataset: CnvData) -> go.Figure:
    variables = plotted_variables(list(dataset.frame.columns))
    if not variables:
        figure = go.Figure()
        figure.add_annotation(
            text="No plottable variables were found.",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
        )
        return figure

    figure = make_subplots(
        rows=len(variables),
        cols=1,
        shared_xaxes=True,
        vertical_spacing=min(0.025, 0.25 / len(variables)),
        subplot_titles=[
            plot_label_parts(
                variable,
                dataset.labels.get(variable, variable),
            )[0]
            for variable in variables
        ],
    )
    data_index = dataset.frame.index
    for row, variable in enumerate(variables, start=1):
        label = dataset.labels.get(variable, variable)
        _subplot_title, unit = plot_label_parts(variable, label)
        figure.add_trace(
            go.Scatter(
                x=data_index,
                y=dataset.frame[variable],
                mode="lines",
                line={
                    "color": COLORS[(row - 1) % len(COLORS)],
                    "width": 1.2,
                },
                hovertemplate=(
                    f"Index: %{{x:,}}<br>{label}: %{{y:.6g}}"
                    "<extra></extra>"
                ),
                name=variable,
            ),
            row=row,
            col=1,
        )
        figure.update_yaxes(
            title_text=unit,
            autorange=(
                "reversed"
                if is_pressure_variable(variable)
                else True
            ),
            showgrid=True,
            gridcolor="#E5E7EB",
            zeroline=False,
            row=row,
            col=1,
        )
        figure.update_xaxes(
            showticklabels=True,
            showgrid=True,
            gridcolor="#F3F4F6",
            zeroline=False,
            row=row,
            col=1,
        )
    figure.update_layout(
        title={
            "text": title,
            "x": 0.5,
            "xanchor": "center",
        },
        height=max(600, 240 * len(variables)),
        margin={"l": 120, "r": 35, "t": 80, "b": 70},
        hovermode="closest",
        showlegend=False,
        autosize=False,
        plot_bgcolor="#FFFFFF",
        paper_bgcolor="#FFFFFF",
    )
    return figure


def variable_graph(figure: go.Figure) -> dcc.Graph:
    height = figure.layout.height or 600
    return dcc.Graph(
        figure=figure,
        responsive=True,
        style={"width": "100%", "height": f"{height}px"},
        config={
            "displaylogo": False,
            "responsive": True,
            "scrollZoom": True,
        },
    )


def file_buttons(
    search_text: str | None,
    selected_path: str | None,
    folder_data: dict,
):
    query = (search_text or "").strip().casefold()
    matching_files = [
        file
        for file in folder_data.get("files", [])
        if not query or query in file["relative"].casefold()
    ]
    if not matching_files:
        return html.Div("No matching CNV files.", className="empty-file-list")

    return [
        html.Button(
            file["name"],
            id={"type": "cnv-file-button", "path": file["path"]},
            n_clicks=0,
            title=file["relative"],
            className=(
                "cnv-file-button selected"
                if file["path"] == selected_path
                else "cnv-file-button"
            ),
        )
        for file in matching_files
    ]


initial_files = INITIAL_FOLDER_DATA["files"]
initial_file = initial_files[0]["path"] if initial_files else None
app = Dash(__name__)
app.title = "CNV File Viewer"
app.layout = html.Div(
    [
        html.Aside(
            [
                html.H1("CNV files"),
                html.Label("First-column folder", htmlFor="cnv-folder-path"),
                dcc.Dropdown(
                    id="cnv-folder-path",
                    options=FOLDER_OPTIONS,
                    value=str(INITIAL_FOLDER),
                    clearable=False,
                    searchable=True,
                ),
                html.Button(
                    "Load folder",
                    id="load-cnv-folder",
                    n_clicks=0,
                ),
                html.Div(
                    f"Found {len(initial_files):,} CNV files.",
                    id="cnv-folder-status",
                    className="cnv-root-folder",
                ),
                html.H2("Second column"),
                html.Label(
                    "Second-column folder",
                    htmlFor="second-cnv-folder-path",
                ),
                dcc.Dropdown(
                    id="second-cnv-folder-path",
                    options=FOLDER_OPTIONS,
                    value=str(INITIAL_FOLDER),
                    clearable=False,
                    searchable=True,
                ),
                html.Div(
                    [
                        html.Button(
                            "Add / update",
                            id="add-cnv-column",
                            n_clicks=0,
                        ),
                        html.Button(
                            "Remove",
                            id="remove-cnv-column",
                            n_clicks=0,
                        ),
                    ],
                    className="cnv-column-buttons",
                ),
                html.Div(
                    "Second column is hidden.",
                    id="second-cnv-folder-status",
                    className="cnv-root-folder",
                ),
                dcc.Input(
                    id="cnv-file-search",
                    type="search",
                    placeholder="Search filenames...",
                    debounce=False,
                ),
                html.Div(
                    [
                        html.Button(
                            "Previous file",
                            id="previous-cnv-file",
                            n_clicks=0,
                        ),
                        html.Button(
                            "Next file",
                            id="next-cnv-file",
                            n_clicks=0,
                        ),
                    ],
                    className="cnv-file-navigation",
                ),
                html.Div(
                    file_buttons(
                        None,
                        initial_file,
                        INITIAL_FOLDER_DATA,
                    ),
                    id="cnv-file-list",
                    className="cnv-file-list",
                ),
            ],
            className="cnv-sidebar",
        ),
        html.Main(
            [
                dcc.Store(
                    id="cnv-folder-data",
                    data=INITIAL_FOLDER_DATA,
                ),
                dcc.Store(id="second-cnv-folder-data"),
                dcc.Store(id="second-cnv-column-enabled", data=False),
                dcc.Store(id="selected-cnv-file", data=initial_file),
                html.Div(id="cnv-viewer-status", className="status"),
                dcc.Loading(
                    html.Div(id="cnv-variable-plots"),
                    type="circle",
                ),
            ],
            className="cnv-viewer-main",
        ),
    ],
    className="cnv-viewer-page",
)


@app.callback(
    Output("cnv-folder-data", "data"),
    Output("cnv-folder-status", "children"),
    Input("load-cnv-folder", "n_clicks"),
    State("cnv-folder-path", "value"),
    prevent_initial_call=True,
)
def load_folder(_n_clicks: int, folder_text: str | None):
    try:
        if not folder_text:
            raise ValueError("Enter a folder path.")
        root = resolve_directory(folder_text)
        folder_data = scan_folder(root)
        count = len(folder_data["files"])
        return folder_data, f"Found {count:,} CNV files under {root}."
    except Exception as error:
        return no_update, str(error)


@app.callback(
    Output("second-cnv-folder-data", "data"),
    Output("second-cnv-column-enabled", "data"),
    Output("second-cnv-folder-status", "children"),
    Input("add-cnv-column", "n_clicks"),
    Input("remove-cnv-column", "n_clicks"),
    State("second-cnv-folder-path", "value"),
    prevent_initial_call=True,
)
def update_second_column(
    _add_clicks: int,
    _remove_clicks: int,
    folder_text: str | None,
):
    if ctx.triggered_id == "remove-cnv-column":
        return None, False, "Second column is hidden."

    try:
        if not folder_text:
            raise ValueError("Select a second-column folder.")
        root = resolve_directory(folder_text)
        folder_data = scan_folder(root)
        count = len(folder_data["files"])
        return (
            folder_data,
            True,
            f"Found {count:,} CNV files under {root}.",
        )
    except Exception as error:
        return no_update, no_update, str(error)


@app.callback(
    Output("cnv-file-list", "children"),
    Input("cnv-file-search", "value"),
    Input("selected-cnv-file", "data"),
    Input("cnv-folder-data", "data"),
)
def update_file_list(
    search_text: str | None,
    selected_path: str | None,
    folder_data: dict,
):
    return file_buttons(search_text, selected_path, folder_data)


@app.callback(
    Output("selected-cnv-file", "data"),
    Input({"type": "cnv-file-button", "path": ALL}, "n_clicks"),
    Input("previous-cnv-file", "n_clicks"),
    Input("next-cnv-file", "n_clicks"),
    Input("cnv-folder-data", "data"),
    State("selected-cnv-file", "data"),
    prevent_initial_call=True,
)
def select_file(
    _file_clicks: list[int],
    _previous_clicks: int,
    _next_clicks: int,
    folder_data: dict,
    selected_path: str | None,
):
    triggered_id = ctx.triggered_id
    paths = [file["path"] for file in folder_data.get("files", [])]
    if triggered_id == "cnv-folder-data":
        return paths[0] if paths else None
    if isinstance(triggered_id, dict):
        triggered_value = ctx.triggered[0].get("value")
        if not isinstance(triggered_value, int) or triggered_value <= 0:
            return no_update
        return triggered_id["path"]
    if triggered_id == "previous-cnv-file" and paths:
        if selected_path not in paths:
            return paths[-1]
        return paths[(paths.index(selected_path) - 1) % len(paths)]
    if triggered_id == "next-cnv-file" and paths:
        if selected_path not in paths:
            return paths[0]
        return paths[(paths.index(selected_path) + 1) % len(paths)]
    return no_update


@app.callback(
    Output("cnv-variable-plots", "children"),
    Output("cnv-viewer-status", "children"),
    Input("selected-cnv-file", "data"),
    Input("cnv-folder-data", "data"),
    Input("second-cnv-column-enabled", "data"),
    Input("second-cnv-folder-data", "data"),
)
def update_plots(
    selected_path: str | None,
    folder_data: dict,
    second_column_enabled: bool,
    second_folder_data: dict | None,
):
    if not selected_path:
        return [], f"No CNV files were found under {folder_data['root']}."

    try:
        first_file = next(
            file
            for file in folder_data["files"]
            if file["path"] == selected_path
        )
        first_path = Path(first_file["path"])
        first_dataset = load_cnv(
            str(first_path),
            first_path.stat().st_mtime_ns,
        )
        first_figure = make_figure(first_file["relative"], first_dataset)
        first_card = html.Div(
            [
                html.Div(
                    f"First folder: {folder_data['root']}",
                    className="source-path",
                ),
                variable_graph(first_figure),
            ],
            className="cnv-plot-column",
        )
        cards = [first_card]
        variable_count = len(
            plotted_variables(list(first_dataset.frame.columns))
        )
        status = (
            f"Showing {first_file['relative']}: "
            f"{len(first_dataset.frame):,} rows and {variable_count:,} plots."
        )

        if second_column_enabled:
            second_file = next(
                (
                    file
                    for file in (second_folder_data or {}).get("files", [])
                    if file["name"].casefold()
                    == first_file["name"].casefold()
                ),
                None,
            )
            if second_file is None:
                cards.append(
                    html.Div(
                        (
                            f"No file named {first_file['name']} was found "
                            "in the second folder."
                        ),
                        className="cnv-plot-column cnv-missing-file",
                    )
                )
                status += " No matching file was found in the second folder."
            else:
                second_path = Path(second_file["path"])
                second_dataset = load_cnv(
                    str(second_path),
                    second_path.stat().st_mtime_ns,
                )
                second_figure = make_figure(
                    second_file["relative"],
                    second_dataset,
                )
                cards.append(
                    html.Div(
                        [
                            html.Div(
                                (
                                    "Second folder: "
                                    f"{second_folder_data['root']}"
                                ),
                                className="source-path",
                            ),
                            variable_graph(second_figure),
                        ],
                        className="cnv-plot-column",
                    )
                )
                status += (
                    f" Comparison file has "
                    f"{len(second_dataset.frame):,} rows."
                )

        grid_class = (
            "cnv-plot-grid two-columns"
            if second_column_enabled
            else "cnv-plot-grid"
        )
        return html.Div(cards, className=grid_class), status
    except Exception as error:
        return [], f"Could not plot file: {error}"


if __name__ == "__main__":
    app.run(debug=True, port=8053, use_reloader=False)
