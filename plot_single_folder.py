from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath

from dash import Dash, Input, Output, State, dcc, html
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import plot_alignment as profile_app


BASE_DIR = Path(__file__).resolve().parent
VARIABLE_COUNT = profile_app.VARIABLE_COUNT


def discover_subfolders() -> list[Path]:
    folders = [
        (Path(parent) / folder_name).resolve()
        for parent, folder_names, _filenames in os.walk(BASE_DIR)
        for folder_name in folder_names
    ]
    return sorted(
        folders,
        key=lambda path: str(path.relative_to(BASE_DIR)).casefold(),
    )


def make_folder_options() -> list[dict[str, str]]:
    options = []
    for folder in discover_subfolders():
        options.append(
            {
                "label": str(folder.relative_to(BASE_DIR)),
                "value": str(folder),
            }
        )
    return options


def resolve_folder(selected_folder: str | None, custom_folder: str | None) -> Path:
    folder_text = (custom_folder or "").strip() or (selected_folder or "").strip()
    if not folder_text:
        raise ValueError("Select a subfolder or enter a custom folder path.")

    # Windows "Copy as path" commonly surrounds a path with quotes.
    if (
        len(folder_text) >= 2
        and folder_text[0] == folder_text[-1]
        and folder_text[0] in {'"', "'"}
    ):
        folder_text = folder_text[1:-1].strip()

    folder_text = os.path.expandvars(folder_text)
    windows_path = PureWindowsPath(folder_text)
    if (
        os.name != "nt"
        and windows_path.is_absolute()
        and len(windows_path.drive) == 2
        and windows_path.drive[1] == ":"
    ):
        drive_letter = windows_path.drive[0].lower()
        folder = Path("/mnt") / drive_letter / Path(*windows_path.parts[1:])
    else:
        folder = Path(os.path.normpath(folder_text)).expanduser()

    if not folder.is_absolute():
        folder = BASE_DIR / folder
    folder = folder.resolve()

    if not folder.is_dir():
        raise ValueError(f"Folder does not exist: {folder}")
    return folder


def cnv_files(folder: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in folder.iterdir()
            if path.is_file() and path.suffix.casefold() == ".cnv"
        ),
        key=lambda path: path.name.casefold(),
    )


def load_file(folder_text: str, filename: str) -> profile_app.CnvData:
    path = Path(folder_text) / filename
    if path.parent.resolve() != Path(folder_text).resolve():
        raise ValueError("Invalid CNV filename.")
    return profile_app.load_cnv(str(path), path.stat().st_mtime_ns)


def variable_choices(
    folder_text: str | None,
    filename: str | None,
    y_axis_mode: str,
) -> tuple[list[str], dict[str, str]]:
    if not folder_text or not filename:
        return [], {}

    dataset = load_file(folder_text, filename)
    columns = list(dataset.frame.columns)
    pressure = find_pressure_column(columns)
    variables = [
        column
        for column in columns
        if y_axis_mode != "pressure" or column != pressure
    ]
    labels = {
        column: dataset.labels.get(column, column)
        for column in variables
    }
    return variables, labels


def find_pressure_column(columns: list[str]) -> str | None:
    for candidate in ("prDM", "pressure", "Pressure"):
        if candidate in columns:
            return candidate
    return next(
        (column for column in columns if "pressure" in column.casefold()),
        None,
    )


def make_single_figure(
    folder_name: str,
    dataset: profile_app.CnvData,
    variables: list[str],
    scans_to_drop: int,
    y_axis_mode: str,
) -> go.Figure:
    frame = dataset.frame.iloc[scans_to_drop:]
    valid_variables = [
        variable for variable in variables if variable in frame.columns
    ]
    pressure = find_pressure_column(list(frame.columns))

    if y_axis_mode == "pressure":
        y_column = pressure
        y_values = frame[y_column] if y_column is not None else None
        y_title = (
            dataset.labels.get(y_column, y_column)
            if y_column is not None
            else "Pressure"
        )
    else:
        y_column = None
        y_values = frame.index
        y_title = "Data index"

    if y_values is None or not valid_variables:
        figure = go.Figure()
        figure.add_annotation(
            text=(
                "No pressure column was found in this file."
                if y_values is None
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
                    y=y_values,
                    mode="lines",
                    name=variable,
                    line={
                        "color": profile_app.COLORS[index],
                        "width": 1.5,
                    },
                    hovertemplate=(
                        f"{dataset.labels.get(variable, variable)}: %{{x:.5g}}"
                        f"<br>{y_title}: %{{y:.5g}}<extra></extra>"
                    ),
                ),
                row=1,
                col=index + 1,
            )
            figure.update_xaxes(
                title_text=dataset.labels.get(variable, variable),
                title_font={
                    "color": profile_app.COLORS[index],
                    "size": 11,
                },
                tickfont={
                    "color": profile_app.COLORS[index],
                    "size": 10,
                },
                showgrid=True,
                gridcolor="#F3F4F6",
                zeroline=False,
                row=1,
                col=index + 1,
            )

        figure.update_yaxes(
            title_text=y_title,
            autorange="reversed",
            showgrid=True,
            gridcolor="#E5E7EB",
            zeroline=False,
            row=1,
            col=1,
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
    return figure


FOLDER_OPTIONS = make_folder_options()

single_app = Dash(__name__)
single_app.title = "Single-Folder CTD Profile Explorer"
single_app.layout = html.Div(
    [
        html.H1("Single-Folder CTD Profile Explorer"),
        html.Div(
            [
                html.Div(
                    [
                        html.Label("Subfolder", htmlFor="single-folder"),
                        dcc.Dropdown(
                            id="single-folder",
                            options=FOLDER_OPTIONS,
                            value=None,
                            placeholder="Select the folder containing CNV files",
                            clearable=True,
                        ),
                    ],
                    className="pump-folder-control",
                ),
                html.Div(
                    [
                        html.Label(
                            "Custom leaf-folder path (optional override)",
                            htmlFor="single-custom-folder",
                        ),
                        dcc.Input(
                            id="single-custom-folder",
                            type="text",
                            placeholder=r"Example: D:\data\06-drv",
                            debounce=True,
                        ),
                    ],
                    className="pump-folder-control",
                ),
                html.Button(
                    "Load folder",
                    id="load-single-folder",
                    n_clicks=0,
                ),
            ],
            className="pump-controls",
        ),
        dcc.Store(id="active-single-folder"),
        html.Div(id="single-folder-status", className="status"),
        html.Div(
            [
                html.Div(
                    [
                        html.Label("CNV file", htmlFor="single-filename"),
                        dcc.Dropdown(
                            id="single-filename",
                            options=[],
                            value=None,
                            clearable=False,
                        ),
                    ],
                    className="filename-control",
                ),
                html.Div(
                    [
                        html.Label(
                            "Scans to drop from beginning",
                            htmlFor="single-scans-to-drop",
                        ),
                        dcc.Input(
                            id="single-scans-to-drop",
                            type="number",
                            value=0,
                            min=0,
                            step=1,
                            debounce=True,
                        ),
                    ],
                    className="scan-control",
                ),
                html.Div(
                    [
                        html.Label("Inverted y-axis", htmlFor="single-y-axis"),
                        dcc.RadioItems(
                            id="single-y-axis",
                            options=[
                                {"label": "Pressure", "value": "pressure"},
                                {"label": "Data index", "value": "index"},
                            ],
                            value="pressure",
                            inline=True,
                            className="y-axis-choice",
                        ),
                    ],
                    className="y-axis-control",
                ),
            ],
            className="file-control single-file-control",
        ),
        html.Div(
            [
                html.Div(
                    [
                        html.Label(
                            f"X variable {index + 1}",
                            htmlFor=f"single-variable-{index}",
                        ),
                        dcc.Dropdown(
                            id=f"single-variable-{index}",
                            options=[],
                            value=None,
                            clearable=True,
                        ),
                    ],
                    className="variable-control",
                )
                for index in range(VARIABLE_COUNT)
            ],
            className="variable-grid",
        ),
        html.Div(id="single-plot-status", className="status"),
        dcc.Loading(html.Div(id="single-graph"), type="circle"),
    ],
    className="page",
)


@single_app.callback(
    Output("active-single-folder", "data"),
    Output("single-filename", "options"),
    Output("single-filename", "value"),
    Output("single-folder-status", "children"),
    Input("load-single-folder", "n_clicks"),
    State("single-folder", "value"),
    State("single-custom-folder", "value"),
    prevent_initial_call=True,
)
def select_folder(
    _n_clicks: int,
    selected_folder: str | None,
    custom_folder: str | None,
):
    try:
        folder = resolve_folder(selected_folder, custom_folder)
        files = cnv_files(folder)
        if not files:
            raise ValueError(f"No CNV files are directly inside: {folder}")

        options = [{"label": path.name, "value": path.name} for path in files]
        return (
            str(folder),
            options,
            files[0].name,
            f"Loaded {len(files):,} CNV files directly from {folder}.",
        )
    except Exception as error:
        return None, [], None, str(error)


@single_app.callback(
    [
        output
        for index in range(VARIABLE_COUNT)
        for output in (
            Output(f"single-variable-{index}", "options"),
            Output(f"single-variable-{index}", "value"),
        )
    ],
    Input("active-single-folder", "data"),
    Input("single-filename", "value"),
    Input("single-y-axis", "value"),
    [State(f"single-variable-{index}", "value") for index in range(VARIABLE_COUNT)],
)
def update_variables(
    folder_text: str | None,
    filename: str | None,
    y_axis_mode: str,
    *current_values: str | None,
):
    try:
        columns, labels = variable_choices(
            folder_text,
            filename,
            y_axis_mode,
        )
        options = profile_app.dropdown_options(columns, labels)
        defaults = profile_app.default_variables(columns)
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


@single_app.callback(
    Output("single-graph", "children"),
    Output("single-plot-status", "children"),
    Input("active-single-folder", "data"),
    Input("single-filename", "value"),
    Input("single-scans-to-drop", "value"),
    Input("single-y-axis", "value"),
    [Input(f"single-variable-{index}", "value") for index in range(VARIABLE_COUNT)],
)
def update_graph(
    folder_text: str | None,
    filename: str | None,
    scans_to_drop: int | float | None,
    y_axis_mode: str,
    *selected_variables: str | None,
):
    if not folder_text or not filename:
        return [], "Load a leaf folder to begin."

    try:
        folder = Path(folder_text)
        path = folder / filename
        dataset = load_file(folder_text, filename)
        variables = list(
            dict.fromkeys(value for value in selected_variables if value)
        )
        drop_count = max(0, int(scans_to_drop or 0))
        figure = make_single_figure(
            folder.name,
            dataset,
            variables,
            drop_count,
            y_axis_mode,
        )
        graph = html.Div(
            [
                html.Div(f"Source: {path}", className="source-path"),
                dcc.Graph(
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
        axis_label = "pressure" if y_axis_mode == "pressure" else "data index"
        return graph, (
            f"Showing {filename} with inverted {axis_label} after dropping "
            f"{drop_count:,} scans."
        )
    except Exception as error:
        return [], f"Could not plot file: {error}"


if __name__ == "__main__":
    single_app.run(debug=True, port=8052)
