#!/usr/bin/env python
"""CLI entry point for the Sentinel-2 change detection pipeline.

Example
-------
    python run_pipeline.py --before data/before.tif --after data/after.tif \\
        --aoi aoi.geojson --outdir data/processed --min-polygon-area 100 \\
        --export-html
"""
import argparse
import sys
from pathlib import Path

from src.pipeline import run_pipeline


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sentinel-2 PCA/CVA change detection pipeline, AOI-clipped "
                    "(before/after/aoi -> change_map.tif, change_binary.tif, "
                    "change_polygons.gpkg)"
    )
    parser.add_argument("--before", required=True, type=Path,
                        help="Path to the 'before' 3-band GeoTIFF.")
    parser.add_argument("--after", required=True, type=Path,
                        help="Path to the 'after' 3-band GeoTIFF.")
    parser.add_argument("--aoi", required=True, type=Path,
                        help="Path to the AOI polygon (GeoJSON or any vector format "
                             "geopandas can read). Pixels outside it are excluded from "
                             "every statistic (percentiles, PCA fit, Otsu threshold) and "
                             "written as nodata in the outputs.")
    parser.add_argument("--outdir", required=True, type=Path,
                        help="Directory to write change_map.tif, change_binary.tif, "
                             "and change_polygons.gpkg into.")
    parser.add_argument("--date-before", type=str, default=None,
                        help="Label for the 'before' date (e.g. 'sentinel2_20230812'), used "
                             "in the vector/DB output and the HTML dashboard's figure titles. "
                             "Default: the --before file's stem (e.g. 'before' for before.tif).")
    parser.add_argument("--date-after", type=str, default=None,
                        help="Label for the 'after' date (e.g. 'sentinel2_20230902'). "
                             "Default: the --after file's stem.")
    parser.add_argument("--min-polygon-area", type=float, default=100.0,
                        help="Minimum polygon area (in the raster's native CRS units, "
                             "e.g. sq metres for UTM) to keep after vectorization. "
                             "Default: 100.0 (one 10m x 10m pixel).")
    parser.add_argument("--export-html", action="store_true",
                        help="Also export the Part 4 Plotly visualizations as a single "
                             "combined visualization_reports/html/dashboard.html. Reuses the "
                             "arrays/masks/GeoDataFrame already computed by this run -- "
                             "nothing is recomputed.")
    parser.add_argument("--expected-pct-changed", type=float, default=None,
                        help="Optional regression check: if given, the final changed-AOI "
                             "percentage is compared against this value and flagged if it "
                             "deviates by more than --regression-tolerance-pp.")
    parser.add_argument("--regression-tolerance-pp", type=float, default=0.05,
                        help="Tolerance, in percentage points, for --expected-pct-changed. "
                             "Default: 0.05.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    summary = run_pipeline(
        before_path=args.before,
        after_path=args.after,
        aoi_path=args.aoi,
        outdir=args.outdir,
        min_polygon_area=args.min_polygon_area,
        detection_date_before=args.date_before,
        detection_date_after=args.date_after,
        export_html=args.export_html,
        expected_pct_changed=args.expected_pct_changed,
        regression_tolerance_pp=args.regression_tolerance_pp,
    )

    print("Change detection pipeline complete.")
    print(f"  Valid AOI pixel coverage:      {summary.valid_pixel_pct:.2f}% "
          f"({summary.valid_pixel_count:,} px)")
    print(f"  Changed pixels before cleanup: {summary.pct_changed_before_cleanup:.2f}% of AOI")
    print(f"  Changed pixels after cleanup:  {summary.pct_changed_after_cleanup:.2f}% of AOI")
    print(f"  Polygons extracted:            {summary.n_polygons}")
    print(f"  Total changed area:            {summary.total_changed_area_sq_m:,.0f} sq m "
          f"({summary.total_changed_area_sq_m / 1e4:.2f} ha)")
    print(f"  Wrote: {summary.change_map_path}")
    print(f"  Wrote: {summary.change_binary_path}")
    print(f"  Wrote: {summary.vector_path}")
    if summary.db_path is not None:
        print(f"  Wrote: {summary.db_path}")
    else:
        print("  Database storage skipped (see warning above).")
    for html_path in summary.html_paths:
        print(f"  Wrote: {html_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
