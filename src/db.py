"""Store vectorized change polygons in a SQLite + SpatiaLite database.

Uses Python's stdlib sqlite3 module with the mod_spatialite extension loaded
at runtime (rather than GDAL's own bundled SpatiaLite support), so the
resulting database is a standard SpatiaLite file usable from any tool that
speaks SpatiaLite (QGIS, spatialite-gui, ogr2ogr, etc.), with an explicit,
inspectable schema (spatial index included).
"""
import os
import platform
import sqlite3
from pathlib import Path

import geopandas as gpd

TABLE_NAME = "change_features"
GEOM_COLUMN = "geom"

_SPATIALITE_CANDIDATES = ("mod_spatialite", "mod_spatialite.dll", "mod_spatialite.so")

# Project root: two levels up from this file (src/db.py -> project root).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_INSTALL_HINT = (
    "Could not load the mod_spatialite SQLite extension "
    f"(tried: {', '.join(_SPATIALITE_CANDIDATES)}).\n"
    "mod_spatialite is a native library, separate from Python packages, and is "
    "not installed on this system (or is not the same architecture/ABI as "
    "Python's sqlite3 module).\n"
    "To fix this:\n"
    "  - conda:  conda install -c conda-forge libspatialite\n"
    "  - micromamba (no conda/admin needed): create a local env with\n"
    "        micromamba create -p ./condaenv -c conda-forge libspatialite\n"
    "    at the project root -- this module auto-detects that folder.\n"
    "  - or set MOD_SPATIALITE_DIR to the directory containing "
    "mod_spatialite(.dll/.so), or add it to PATH yourself.\n"
    "Then re-run this pipeline."
)


def _candidate_spatialite_dirs() -> list[Path]:
    """Directories to search for a mod_spatialite binary, in priority order:
    an explicit MOD_SPATIALITE_DIR env var, then a local `condaenv` folder
    at the project root (as created by `micromamba create -p ./condaenv -c
    conda-forge libspatialite`) -- so a fresh checkout works without the
    user having to manually edit PATH.
    """
    dirs = []

    env_dir = os.environ.get("MOD_SPATIALITE_DIR")
    if env_dir:
        dirs.append(Path(env_dir))

    if platform.system() == "Windows":
        dirs.append(_PROJECT_ROOT / "condaenv" / "Library" / "bin")
    else:
        dirs.append(_PROJECT_ROOT / "condaenv" / "lib")

    return [d for d in dirs if d.is_dir()]


def _make_spatialite_discoverable() -> None:
    """Best-effort: add any known mod_spatialite directory to the DLL/shared
    library search path before attempting to load the extension, so it's
    found without the user having to set PATH manually.
    """
    for d in _candidate_spatialite_dirs():
        d_str = str(d)
        if platform.system() == "Windows" and hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(d_str)
            except OSError:
                pass
        if d_str not in os.environ.get("PATH", ""):
            os.environ["PATH"] = d_str + os.pathsep + os.environ.get("PATH", "")


def _connect_with_spatialite(db_path: Path) -> sqlite3.Connection:
    """Open a sqlite3 connection to `db_path` with mod_spatialite loaded.

    Tries a few common extension names/extensions, since the exact file name
    (e.g. with or without a .dll/.so suffix) varies by platform and install
    method. Before trying, makes a best-effort attempt to add any known
    mod_spatialite directory (an explicit MOD_SPATIALITE_DIR env var, or a
    local ./condaenv install) to the library search path, so this works out
    of the box for someone who followed the micromamba install above without
    them having to touch PATH themselves.
    Raises a RuntimeError with a clear installation hint if none load.
    """
    _make_spatialite_discoverable()

    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)

    last_error = None
    for candidate in _SPATIALITE_CANDIDATES:
        try:
            conn.load_extension(candidate)
            last_error = None
            break
        except sqlite3.OperationalError as e:
            last_error = e
            continue

    if last_error is not None:
        conn.close()
        raise RuntimeError(_INSTALL_HINT) from last_error

    return conn


def _is_spatial_metadata_initialized(conn: sqlite3.Connection) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='spatial_ref_sys'"
    )
    return cur.fetchone() is not None


def init_db(db_path: Path, srid: int) -> sqlite3.Connection:
    """Open (creating if needed) the SpatiaLite database at `db_path`,
    initialize spatial metadata if this is a new database, and create the
    change_features table (with a spatial index) if it doesn't exist yet.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    is_new_db = not db_path.exists()

    conn = _connect_with_spatialite(db_path)

    # InitSpatialMetaData only needs to run once per new database file.
    if is_new_db or not _is_spatial_metadata_initialized(conn):
        conn.execute("SELECT InitSpatialMetaData(1)")
        conn.commit()

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detection_date_before TEXT NOT NULL,
            detection_date_after TEXT NOT NULL,
            change_magnitude REAL,
            area_sq_m REAL,
            method TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()

    # AddGeometryColumn/CreateSpatialIndex are no-ops (SpatiaLite raises if
    # called again) once the geometry column already exists, so only run
    # them the first time.
    cur = conn.execute(
        "SELECT 1 FROM geometry_columns WHERE f_table_name = ? AND f_geometry_column = ?",
        (TABLE_NAME, GEOM_COLUMN),
    )
    if cur.fetchone() is None:
        conn.execute(
            f"SELECT AddGeometryColumn('{TABLE_NAME}', '{GEOM_COLUMN}', {srid}, 'POLYGON', 'XY')"
        )
        conn.execute(f"SELECT CreateSpatialIndex('{TABLE_NAME}', '{GEOM_COLUMN}')")
        conn.commit()

    return conn


def get_srid(crs) -> int:
    """Extract the EPSG code from a rasterio/pyproj CRS. Raises if the CRS
    has no EPSG code, rather than silently defaulting to 4326.
    """
    epsg = crs.to_epsg() if hasattr(crs, "to_epsg") else None
    if epsg is None:
        raise ValueError(
            f"Could not determine an EPSG code for CRS {crs!r}; "
            "refusing to guess/default the SRID."
        )
    return epsg


def load_change_features(gdf: gpd.GeoDataFrame, db_path: Path, srid: int) -> None:
    """Load `gdf` (as produced by vectorize.vectorize_change_mask -- columns
    id, date_before, date_after, area_m2, confidence, geometry) into the
    change_features table of the SpatiaLite database at `db_path`, creating/
    initializing the database and table first if needed.

    `gdf`'s own `id` column is dropped -- the database assigns its own
    AUTOINCREMENT id -- and its columns are mapped onto the table's schema:
    date_before -> detection_date_before, date_after -> detection_date_after,
    area_m2 -> area_sq_m, confidence -> change_magnitude (the table's schema
    predates the `confidence` column name; this keeps that schema stable
    rather than changing it every time the upstream GeoDataFrame's naming
    changes). `method` is populated with a fixed label since `gdf` no longer
    carries one.

    Uses geopandas' native SpatiaLite support (`to_file(..., driver="SQLite",
    spatialite=True)`), which handles geometry encoding correctly
    automatically, rather than hand-written INSERT + GeomFromText() calls.
    """
    db_path = Path(db_path)

    # Ensures the database/table/spatial-index exist with the exact schema
    # above, before geopandas' own OGR-driven write touches the file.
    conn = init_db(db_path, srid=srid)
    conn.close()

    write_gdf = gdf.rename(columns={
        "geometry": GEOM_COLUMN,
        "date_before": "detection_date_before",
        "date_after": "detection_date_after",
        "area_m2": "area_sq_m",
        "confidence": "change_magnitude",
    }).set_geometry(GEOM_COLUMN)

    if "change_magnitude" not in write_gdf.columns:
        write_gdf["change_magnitude"] = None
    if "method" not in write_gdf.columns:
        write_gdf["method"] = "PCA"

    write_gdf = write_gdf[
        ["detection_date_before", "detection_date_after", "change_magnitude", "area_sq_m", "method", GEOM_COLUMN]
    ]

    # NOTE: the geopandas/pyogrio API for this is `mode="a"`, not
    # `if_exists="append"` (that's a pandas.to_sql-style kwarg that this
    # pyogrio version's SQLite driver rejects -- verified against the
    # installed geopandas 1.1.4 / pyogrio 0.13.0).
    write_gdf.to_file(
        db_path,
        layer=TABLE_NAME,
        driver="SQLite",
        spatialite=True,
        mode="a",
    )


def verify_load(db_path: Path) -> tuple[int, float]:
    """Run a confirmation query against change_features and print + return
    (feature_count, total_area_sq_m).
    """
    conn = _connect_with_spatialite(db_path)
    try:
        cur = conn.execute(
            f"SELECT COUNT(*) AS feature_count, SUM(area_sq_m) AS total_area_sq_m FROM {TABLE_NAME}"
        )
        feature_count, total_area_sq_m = cur.fetchone()
    finally:
        conn.close()

    total_area_sq_m = total_area_sq_m or 0.0
    print(f"{TABLE_NAME}: feature_count={feature_count}, total_area_sq_m={total_area_sq_m:,.0f}")
    return feature_count, total_area_sq_m
