"""Orchestrates the full change-detection flow end-to-end.

Order:
  1. Load before.tif and after.tif with rasterio, keep the profile/transform/crs.
  2. preprocessing: AOI clip -> reflectance scale -> clip -> radiometric
     normalize. AOI-excluded pixels are NaN from this point on.
  3. change_detection: PCA magnitude + Otsu threshold, fit only on in-AOI
     (non-NaN) pixels; out-of-AOI pixels are 0 in the binary mask.
  4. cleanup: morphological opening (border-trim removed -- redundant once
     AOI clipping properly excludes edge pixels from all statistics).
  5. export.write_raster() for both outputs, using the actual AOI mask as
     valid_mask, then export.verify_raster_outputs() to independently
     confirm CRS/transform and recompute % changed from the written files.
  6. vectorize to produce the GeoDataFrame (id, date_before, date_after,
     area_m2, confidence, geometry).
  7. export.write_vector() to save the .gpkg.
  8. db.load_change_features() to store the polygons in SQLite/SpatiaLite.
     Non-fatal: if mod_spatialite can't be loaded, the raster/vector outputs
     from steps 5-7 are already written, so this step logs a clear warning
     and the pipeline still completes rather than failing outright.
  9. (optional, --export-html) export_visualizations.export_all() to write
     the Part 4 Plotly figures as standalone HTML files, reusing the
     before/after arrays, masks, and GeoDataFrame already computed above --
     nothing is recomputed for this step.

All percentage calculations use valid_pixel_count (in-AOI pixels) as the
denominator, never array.size or .mean() (which would include out-of-AOI
pixels and understate the true in-AOI change fraction).
"""
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio

from . import change_detection, cleanup, db, export, export_visualizations, preprocessing, vectorize


@dataclass
class PipelineSummary:
    pct_changed_before_cleanup: float
    pct_changed_after_cleanup: float
    valid_pixel_count: int
    valid_pixel_pct: float
    n_polygons: int
    total_changed_area_sq_m: float
    change_map_path: Path
    change_binary_path: Path
    vector_path: Path
    db_path: Path = None
    db_error: str = None
    html_paths: list = field(default_factory=list)
    raster_verification: dict = None
    regression_check: dict = None


def _load_raster(path: Path):
    with rasterio.open(path) as src:
        arr = np.moveaxis(src.read(), 0, -1)  # (bands, H, W) -> (H, W, bands)
        profile = src.profile.copy()
    return arr, profile


def run_pipeline(
    before_path: Path,
    after_path: Path,
    aoi_path: Path,
    outdir: Path,
    min_polygon_area: float = 100.0,
    detection_date_before: str = None,
    detection_date_after: str = None,
    export_html: bool = False,
    html_outdir: Path = export_visualizations.DEFAULT_REPORT_DIR,
    expected_pct_changed: float = None,
    regression_tolerance_pp: float = 0.05,
) -> PipelineSummary:
    before_path = Path(before_path)
    after_path = Path(after_path)
    outdir = Path(outdir)

    if detection_date_before is None:
        detection_date_before = before_path.stem
    if detection_date_after is None:
        detection_date_after = after_path.stem

    # 1. Load, keeping the profile/transform/crs.
    before_raw, before_profile = _load_raster(before_path)
    after_raw, after_profile = _load_raster(after_path)

    if before_raw.shape != after_raw.shape:
        raise ValueError(
            f"before/after shape mismatch: {before_raw.shape} vs {after_raw.shape}"
        )
    if before_profile["crs"] != after_profile["crs"]:
        raise ValueError(
            f"before/after CRS mismatch: {before_profile['crs']} vs {after_profile['crs']}"
        )
    if before_profile["transform"] != after_profile["transform"]:
        raise ValueError("before/after transform mismatch")

    # 2. Preprocessing: AOI clip -> reflectance scale -> clip -> radiometric normalize.
    before, after_norm, aoi_mask, aoi_native = preprocessing.preprocess(
        before_raw, after_raw, aoi_path, before_profile["transform"], before_profile["crs"]
    )
    valid_pixel_count = int(aoi_mask.sum())
    valid_pixel_pct = 100 * valid_pixel_count / aoi_mask.size

    # 3. Change detection: PCA magnitude + Otsu threshold, valid-AOI-only fit.
    result = change_detection.compute_change(before, after_norm)
    pct_before_cleanup = 100 * result.pca_mask.sum() / result.valid_pixel_count

    # 4. Cleanup: morphological opening only (no border-trim -- see cleanup.py).
    cleaned_mask = cleanup.clean_mask(result.pca_mask)
    pct_after_cleanup = 100 * cleaned_mask.sum() / result.valid_pixel_count

    # 5. Write raster outputs, using the actual AOI mask (not a nonzero-pixel
    # proxy) to determine nodata, then independently verify the outputs.
    change_map_path, change_binary_path = export.write_raster(
        change_map=result.pca_mag,
        binary_mask=cleaned_mask,
        profile=before_profile,
        outdir=outdir,
        aoi_mask=aoi_mask,
    )
    raster_verification = export.verify_raster_outputs(
        change_map_path, change_binary_path, before_profile,
        expected_pct_changed=pct_after_cleanup,
    )

    # 6. Vectorize, using the original raster's transform/crs.
    gdf = vectorize.vectorize_change_mask(
        binary_mask=cleaned_mask,
        transform=before_profile["transform"],
        crs=before_profile["crs"],
        date_before=detection_date_before,
        date_after=detection_date_after,
        magnitude=result.pca_mag,
        min_area_sq_m=min_polygon_area,
    )

    # 7. Write vector output.
    vector_path = export.write_vector(gdf, outdir=outdir)

    # 8. Store in SQLite/SpatiaLite. Non-fatal: outputs 5-7 already exist.
    db_path = outdir / "change_detection.db"
    db_error = None
    try:
        srid = db.get_srid(before_profile["crs"])
        db.load_change_features(gdf, db_path=db_path, srid=srid)
        db.verify_load(db_path)
    except RuntimeError as e:
        db_error = str(e)
        db_path = None
        print(f"WARNING: skipping database storage.\n{db_error}")

    # 9. (optional) Export the Part 4 HTML dashboard, reusing the arrays/
    # masks/GeoDataFrame already computed above -- nothing recomputed.
    html_paths = []
    if export_html:
        band_labels = [f"Band {i + 1}" for i in range(before_raw.shape[2])]
        dates = [detection_date_before, detection_date_after]
        band_arrays = {
            detection_date_before: {label: before_raw[:, :, i] for i, label in enumerate(band_labels)},
            detection_date_after: {label: after_raw[:, :, i] for i, label in enumerate(band_labels)},
        }
        stacked = {detection_date_before: before_raw, detection_date_after: after_raw}

        dashboard_path = export_visualizations.export_dashboard(
            output_dir=html_outdir,
            arrays=band_arrays,
            stacked=stacked,
            DATES=dates,
            BANDS=band_labels,
            after_norm=after_norm,
            pca_mask=result.pca_mask,
            pca_mask_opened=cleaned_mask,
            change_gdf=gdf,
            aoi_gdf=aoi_native,
            base_profile=before_profile,
            valid_rows=result.valid_pixel_count,
        )
        html_paths = [dashboard_path]

    # Regression check against a known-validated reference percentage, if given.
    regression_check = None
    if expected_pct_changed is not None:
        deviation_pp = abs(pct_after_cleanup - expected_pct_changed)
        passed = deviation_pp <= regression_tolerance_pp
        regression_check = {
            "expected_pct": expected_pct_changed,
            "actual_pct": pct_after_cleanup,
            "deviation_pp": deviation_pp,
            "tolerance_pp": regression_tolerance_pp,
            "passed": passed,
        }
        status = "PASS" if passed else "FAIL"
        print(f"Regression check [{status}]: expected {expected_pct_changed:.4f}%, "
              f"got {pct_after_cleanup:.4f}% (deviation {deviation_pp:.4f}pp, "
              f"tolerance {regression_tolerance_pp}pp)")

    return PipelineSummary(
        pct_changed_before_cleanup=pct_before_cleanup,
        pct_changed_after_cleanup=pct_after_cleanup,
        valid_pixel_count=valid_pixel_count,
        valid_pixel_pct=valid_pixel_pct,
        n_polygons=len(gdf),
        total_changed_area_sq_m=float(gdf["area_m2"].sum()) if len(gdf) else 0.0,
        change_map_path=change_map_path,
        change_binary_path=change_binary_path,
        vector_path=vector_path,
        db_path=db_path,
        db_error=db_error,
        html_paths=html_paths,
        raster_verification=raster_verification,
        regression_check=regression_check,
    )
