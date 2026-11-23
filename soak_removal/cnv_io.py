"""
Read and truncate Sea-Bird .cnv files, and recognize the plotted variables.

A .cnv file is a text header that ends with the ``*END*`` line, followed by one
data row per scan. Truncation keeps the header bytes unchanged and writes only
a contiguous slice of the original data rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

__all__ = [
    "PLOT_KEYS",
    "PLOT_TITLES",
    "CnvFile",
    "axis_title",
    "read_cnv",
    "recognize_variables",
    "write_truncated_cnv",
]

END_MARKER = b"*END*"
NAME_PATTERN = re.compile(r"^#\s*name\s+(\d+)\s*=\s*([^:]+?)\s*(?::\s*(.*))?$")
BAD_FLAG_PATTERN = re.compile(r"^#\s*bad_flag\s*=\s*(\S+)")

PLOT_KEYS = ("depth", "temp", "conductivity", "salinity", "do")
PLOT_TITLES = {
    "depth": "Depth",
    "temp": "Temperature",
    "conductivity": "Conductivity",
    "salinity": "Salinity",
    "do": "Dissolved Oxygen",
}

# Name patterns for each plot, in priority order. The first column whose
# Sea-Bird short name matches the earliest pattern wins.
VARIABLE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    # depSM / depSF (salt water), depFM / depFF (fresh water).
    "depth": (re.compile(r"^dep[SF][MF]$"),),
    # t090C, t068C (primary); t190C, t4990C, ... (secondary / other sensors).
    "temp": (
        re.compile(r"^t0(90|68)[CF]$"),
        re.compile(r"^t\d+(90|68)[CF]$"),
    ),
    # c0S/m, c0mS/cm, c0uS/cm (primary); c1S/m, ... (secondary).
    "conductivity": (
        re.compile(r"^c0(S/m|mS/cm|uS/cm)$"),
        re.compile(r"^c\d+(S/m|mS/cm|uS/cm)$"),
    ),
    # sal00 (primary); sal11, ... (secondary).
    "salinity": (re.compile(r"^sal00$"), re.compile(r"^sal\d+$")),
    # Oxygen concentration only: not raw voltage (sbeox0V), % saturation
    # (sbeox0PS) or Weiss saturation (oxsatML/L).
    "do": (
        re.compile(r"^sbeox0(Mg/L|ML/L|Mm/L|Mm/Kg)$"),
        re.compile(r"^sbeox\d+(Mg/L|ML/L|Mm/L|Mm/Kg)$"),
        re.compile(r"^sbox\d+(Mg/L|ML/L|Mm/L|Mm/Kg|dV/dT)$"),
    ),
}


@dataclass(frozen=True)
class CnvFile:
    """Parsed .cnv file. ``values`` has one row per data line (bad_flag → NaN)."""

    names: tuple[str, ...]
    descriptions: dict[str, str]
    values: np.ndarray

    @property
    def total_scans(self) -> int:
        return int(self.values.shape[0])

    def column(self, name: str | None) -> np.ndarray | None:
        if name is None or name not in self.names:
            return None
        return self.values[:, self.names.index(name)]


def _split_cnv(raw: bytes) -> tuple[bytes, list[bytes]]:
    """Header bytes (through the *END* line) and the non-empty data lines."""
    lines = raw.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.strip() == END_MARKER:
            data = [ln for ln in lines[i + 1:] if ln.strip()]
            return b"".join(lines[: i + 1]), data
    raise ValueError("No '*END*' marker was found.")


def _parse_cnv(raw: bytes) -> CnvFile:
    header, data_lines = _split_cnv(raw)
    columns: list[tuple[int, str, str]] = []
    bad_flag: float | None = None
    for line in header.decode("latin-1").splitlines():
        line = line.strip()
        m = NAME_PATTERN.match(line)
        if m:
            name = m.group(2).strip()
            columns.append((int(m.group(1)), name, (m.group(3) or name).strip()))
            continue
        m = BAD_FLAG_PATTERN.match(line)
        if m:
            try:
                bad_flag = float(m.group(1))
            except ValueError:
                pass
    if not columns:
        raise ValueError("No '# name' column definitions were found.")
    columns.sort(key=lambda c: c[0])
    names = tuple(c[1] for c in columns)
    n_cols = len(names)

    # One output row per data line so row indices always match line indices.
    values = np.full((len(data_lines), n_cols), np.nan, dtype=np.float64)
    for i, line in enumerate(data_lines):
        fields = line.split()
        if len(fields) != n_cols:
            continue
        try:
            values[i] = [float(f) for f in fields]
        except ValueError:
            continue
    if bad_flag is not None:
        values[values == bad_flag] = np.nan
    return CnvFile(
        names=names,
        descriptions={c[1]: c[2] for c in columns},
        values=values,
    )


@lru_cache(maxsize=16)
def _read_cached(path_text: str, mtime_ns: int, size: int) -> CnvFile:
    del mtime_ns, size
    return _parse_cnv(Path(path_text).read_bytes())


def read_cnv(path: Path) -> CnvFile:
    """Parse a .cnv file (cached until the file changes)."""
    st = path.stat()
    return _read_cached(str(path.resolve()), st.st_mtime_ns, st.st_size)


def write_truncated_cnv(
    input_path: Path, output_path: Path, start_idx: int, end_idx: int
) -> int:
    """
    Write the unchanged header and data rows ``[start_idx, end_idx)``.

    Returns the number of data rows written.
    """
    header, data_lines = _split_cnv(input_path.read_bytes())
    n = len(data_lines)
    start_idx = max(0, min(int(start_idx), n))
    end_idx = max(start_idx, min(int(end_idx), n))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(header + b"".join(data_lines[start_idx:end_idx]))
    return end_idx - start_idx


def recognize_variables(cnv: CnvFile) -> dict[str, str | None]:
    """Column name for each plot key, or None when no recognized column exists."""
    found: dict[str, str | None] = {}
    for key in PLOT_KEYS:
        found[key] = None
        for pattern in VARIABLE_PATTERNS[key]:
            match = next((n for n in cnv.names if pattern.match(n)), None)
            if match is not None:
                found[key] = match
                break
    return found


def axis_title(name: str, description: str) -> str:
    """Short y-axis title such as 't090C [ITS-90, deg C]'."""
    m = re.search(r"\[([^\[\]]+)\]", description)
    return f"{name} [{m.group(1).strip()}]" if m else name
