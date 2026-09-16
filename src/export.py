"""GeoTIFF and GeoPackage writers.

Both raster outputs are written using the ORIGINAL input raster's rasterio
profile (crs, transform), so they are properly georeferenced and align
exactly with the source imagery.
"""
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio


def write_raster(
    change_map: np.ndarray,
    binary_mask: np.ndarray,
    profile: dict,
    outdir: Path,
    aoi_mask: np.ndarray,
    change_map_name: str = "change_map.tif",
    change_binary_name: str = "change_binary.tif",
) -> tuple[Path, Path]:
    """Write the continuous change intensity map and the binary change mask
    as georeferenced single-band GeoTIFFs, both using `profile` (crs,
    transform, width, height) from the original input raster.

    Parameters
    ----------
    change_map : np.ndarray
        (H, W) float change intensity (e.g. pca_mag). May contain NaN
        outside the AOI.
    binary_mask : np.ndarray
        (H, W) uint8 binary change mask (0/1), e.g. the cleaned mask from
        cleanup.clean_mask. 0 outside the AOI (never NaN, since NaN can't be
        represented in a uint8 array).
    profile : dict
        rasterio profile from the original input raster (crs, transform, ...).
    aoi_mask : np.ndarray
        (H, W) bool, True = inside AOI (from preprocessing.apply_aoi_mask).
        This -- not a proxy based on nonzero raw pixel values -- is what
        determines which pixels are written as nodata in both outputs.
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    change_map_path = outdir / change_map_name
    change_binary_path = outdir / change_binary_name

    change_map_profile = profile.copy()
    change_map_profile.update(count=1, dtype="float32", nodata=-9999.0, compress="lzw")

    # Resolve any remaining NaN to the chosen nodata value explicitly, then
    # also apply aoi_mask, so both mechanisms agree on which pixels are nodata.
    change_map_out = change_map.astype(np.float32).copy()
    change_map_out = np.where(np.isnan(change_map_out), -9999.0, change_map_out)
    change_map_out[~aoi_mask] = -9999.0

    with rasterio.open(change_map_path, "w", **change_map_profile) as dst:
        dst.write(change_map_out, 1)

    change_binary_profile = profile.copy()
    change_binary_profile.update(count=1, dtype="uint8", nodata=255, compress="lzw")

    change_binary_out = binary_mask.astype(np.uint8).copy()
    change_binary_out[~aoi_mask] = 255

    with rasterio.open(change_binary_path, "w", **change_binary_profile) as dst:
        dst.write(change_binary_out, 1)

    return change_map_path, change_binary_path


def write_vector(
    gdf: gpd.GeoDataFrame,
    outdir: Path,
    filename: str = "change_polygons.gpkg",
    layer: str = "change_polygons",
) -> Path:
    """Save the change-polygon GeoDataFrame to a GeoPackage."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    vector_path = outdir / filename
    gdf.to_file(vector_path, driver="GPKG", layer=layer)
    return vector_path


def verify_raster_outputs(
    change_map_path: Path,
    change_binary_path: Path,
    input_profile: dict,
    expected_pct_changed: float,
) -> dict:
    """Reopen both output rasters and independently verify:
      - CRS and transform match the original input raster exactly.
      - The changed-percentage recomputed directly from the written binary
        raster (excluding nodata pixels) matches the in-memory result that
        produced it.

    Returns a dict of the checks performed and their results; prints a
    human-readable summary.
    """
    checks = {}

    with rasterio.open(change_map_path) as src:
        checks["change_map_crs_match"] = src.crs == input_profile["crs"]
        checks["change_map_transform_match"] = src.transform == input_profile["transform"]

    with rasterio.open(change_binary_path) as src:
        checks["change_binary_crs_match"] = src.crs == input_profile["crs"]
        checks["change_binary_transform_match"] = src.transform == input_profile["transform"]

        binary = src.read(1)
        nodata = src.nodata
        valid = binary != nodata
        recomputed_pct = 100 * (binary[valid] == 1).sum() / valid.sum()

    checks["recomputed_pct_changed"] = recomputed_pct
    checks["expected_pct_changed"] = expected_pct_changed
    checks["pct_changed_match"] = abs(recomputed_pct - expected_pct_changed) < 1e-6

    all_ok = all(v for k, v in checks.items() if isinstance(v, (bool, np.bool_)))

    print("Raster output verification:")
    print(f"  change_map.tif    CRS match: {checks['change_map_crs_match']}, "
          f"transform match: {checks['change_map_transform_match']}")
    print(f"  change_binary.tif CRS match: {checks['change_binary_crs_match']}, "
          f"transform match: {checks['change_binary_transform_match']}")
    print(f"  Recomputed % changed from written raster: {recomputed_pct:.4f}% "
          f"(in-memory: {expected_pct_changed:.4f}%, match: {checks['pct_changed_match']})")
    print(f"  All checks passed: {all_ok}")

    checks["all_ok"] = all_ok
    return checks
