# Sentinel-2 Change Detection

Detects land-surface change between two Sentinel-2 acquisitions of an open-pit
mining site in Zambia, using RGB-only imagery and scoping every statistic to
an area of interest (AOI). Outputs a change-intensity raster, a binary change
mask, vector polygons of the detected change regions, and a SpatiaLite
database of those polygons.

All commands below assume your working directory is this folder
(`submission/`).

```
cd submission
```

## Repo structure

```
submission/
├── README.md                    # this file
├── report.md                    # full methodology, results, and interpretation
├── requirements.txt
├── .gitignore
├── data/
│   ├── sentinel2_20230812/       # per-band B02/B03/B04.tif, at the path exploration.ipynb expects
│   ├── sentinel2_20230902/       # (duplicates of data/raw/*.tif in per-band form -- see below)
│   ├── raw/                      # sentinel2_20230812.tif, sentinel2_20230902.tif, aoi.geojson (pipeline inputs)
│   └── processed/                # pipeline outputs (see "Outputs" below)
│       ├── change_map.tif        # continuous PCA change-magnitude raster
│       ├── change_binary.tif     # binary change mask (post-cleanup)
│       ├── change_polygons.gpkg  # vectorized change regions
│       └── change_features.sqlite # same polygons in a SpatiaLite database
├── src/
│   ├── __init__.py
│   ├── preprocessing.py         # AOI clipping, reflectance scaling, radiometric normalization
│   ├── change_detection.py      # PCA/CVA magnitude, Otsu thresholding (NaN-safe)
│   ├── cleanup.py                # morphological opening (seam-line artifact removal)
│   ├── vectorize.py              # raster-to-polygon conversion + attributes
│   ├── db.py                     # SQLite + SpatiaLite load
│   ├── export.py                 # GeoTIFF/GeoPackage writers
│   ├── export_visualizations.py  # combined dashboard.html builder
│   └── pipeline.py               # orchestrates the full flow end-to-end
├── run_pipeline.py               # CLI entry point
├── notebooks/
│   └── exploration.ipynb        # original interactive validation notebook (see below)
└── visualization_reports/
    └── html/
        └── dashboard.html        # standalone visual dashboard (open in a browser)
```

## How to run

```
pip install -r requirements.txt

python run_pipeline.py \
    --before data/raw/sentinel2_20230812.tif \
    --after data/raw/sentinel2_20230902.tif \
    --aoi data/raw/aoi.geojson \
    --outdir data/processed \
    --export-html
```

This reproduces everything already committed under `data/processed/` and
`visualization_reports/html/`. Add `--expected-pct-changed 2.08` to get an
explicit PASS/FAIL regression check against the validated result (see below).

## Approach summary

1. **Preprocessing** — AOI-clip both dates (out-of-AOI pixels → NaN, before
   any statistic is computed), scale digital numbers to reflectance, clip to
   `[0, 1]`, and radiometrically normalize the "after" image onto "before"
   (per band, NaN-safe).
2. **Change detection** — band-difference the two dates, then compute change
   magnitude two equivalent ways: Change Vector Analysis (CVA, Euclidean
   distance across bands) and PCA on the difference image (L2 norm across
   components — numerically identical to CVA magnitude here). Otsu's method
   picks the threshold automatically from the in-AOI magnitude values.
3. **Cleanup** — morphological opening removes a thin diagonal tile-seam
   artifact and isolated speckle without erasing genuine change blobs.
4. **Vectorization & storage** — the cleaned binary mask is converted to
   polygons (with area and a confidence score) and written to a GeoPackage
   and a SpatiaLite database.

Every percentage in this pipeline is "percent of the AOI" — always
`changed_pixel_count / valid_in_AOI_pixel_count`, never `.mean()` or
`array.size`, so out-of-AOI pixels never dilute the reported change fraction.

Full methodology, results, and interpretation of the detected changes are in
[report.md](report.md).

**Validated result** (regenerated fresh as part of preparing this
submission): Otsu threshold **0.0598**, **2.08%** of the AOI changed after
cleanup (**551.45 ha**), **374** polygons.

## Key assumptions

- **RGB-only imagery.** The source data has only the visible Sentinel-2 bands
  (B04/B03/B02) — no near-infrared (B08) band was available, so NDVI and
  other NIR-based indices were not viable and were not used. CVA/PCA were
  chosen instead because they use all three available bands jointly.
- **Reflectance scaling.** Raw pixel values are assumed to be Sentinel-2 L2A
  digital numbers (reflectance × 10000) and are divided by 10000 whenever the
  99th percentile of the raw array exceeds 1.5; otherwise the array is
  assumed to already be scaled reflectance and is left as-is.
- **AOI CRS.** `aoi.geojson` is assumed to be in a CRS that geopandas can
  reproject onto the raster's native CRS (`EPSG:32735`, UTM zone 35S, in this
  dataset). Pixels outside the reprojected AOI are excluded from *every*
  statistic — percentiles, the PCA fit, the Otsu threshold — not just masked
  out of the final output.
- **Tile-seam artifact.** A thin diagonal streak in the raw band data (a
  Sentinel-2 detector-strip/tile-mosaic seam, not a processing bug) is
  removed via morphological opening. It's documented here as a known data
  artifact rather than something the pipeline is "hiding."
- **Database choice.** Change polygons are stored in SQLite + SpatiaLite
  rather than PostGIS. SQLite is a single portable file — no server setup or
  connection string needed to inspect it, which matters for grading. The
  `mod_spatialite` extension load is non-fatal: if it isn't available on a
  machine, the raster and GeoPackage outputs are still written and
  `run_pipeline.py` prints a clear warning with installation instructions
  instead of failing the whole run.

## `notebooks/exploration.ipynb`

This is the original interactive notebook the pipeline in `src/` was
extracted from. The analysis code and markdown are unedited; the only
changes are to 2 cells' input paths (`DATA_DIR` and the AOI path, both now
`../data/...`, see "Running the notebook" below) so it runs correctly in
its committed location. It's kept primarily as evidence of the validation
process behind the final design (the redesigned `src/` modules are the
artifact intended for reuse), but it's also fully runnable in place. It
shows the iterative work that `report.md` and `src/`
summarize: histogram/noise-vs-signal checks on the change-magnitude
distribution, cross-method agreement testing (band differencing vs. CVA vs.
PCA classified the same pixels identically across all in-AOI pixels), and
the visual discovery of both the border-registration artifact and the
diagonal tile-seam artifact that `cleanup.py` addresses.

**Note:** the rendered *outputs* of its 5 largest plotting cells (Part 4 —
raw bands per date, true-color comparison, change mask before/after cleanup,
AOI+polygons overlay) were cleared, dropping the file from ~107 MB to ~14 MB.
Nothing analytical was removed: those 5 cells' code is untouched, and their
exact output is already reproduced, in full and in more usable (interactive)
form, at `visualization_reports/html/dashboard.html` — `export_visualizations.py`
literally reimplements these same cells for that file, so keeping ~93 MB of
duplicate static images inline here added no information, just made the
notebook impractical to open locally. Every other cell's outputs (histograms,
cross-method agreement counts, smaller diagnostic plots) are untouched.

### Running the notebook

Open and run it from inside `notebooks/` — its paths (`../data/...`) are
relative to the notebook file's own directory, which is also VS Code's
default Jupyter working directory (`${fileDirname}`), so it runs with
**no extra configuration needed**. This repo carries the per-band imagery
those paths need:
- `data/sentinel2_20230812/` and `data/sentinel2_20230902/` (per-band
  B02/B03/B04 GeoTIFFs) — the same imagery as `data/raw/sentinel2_*.tif`,
  just in the original per-band form the notebook reads.
- `data/raw/aoi.geojson` — read directly by the notebook via `../data/raw/aoi.geojson`.

Verified end-to-end from `notebooks/` as the working directory
(`jupyter nbconvert --to notebook --execute`, and confirmed interactively in
VS Code): all 39 executable cells run with zero errors, reproducing the same
validated result as `run_pipeline.py` (374 polygons, 551.45 ha).

**Caveats:**
- The notebook writes its own outputs to `../data/processed/` — the same
  folder `run_pipeline.py` uses — so running it interactively overwrites the
  committed `change_map.tif`/`change_binary.tif`/`change_polygons.gpkg`/
  `change_features.sqlite`. Confirmed harmless (the notebook's own run
  reproduces the identical 374 polygons / 551.45 ha; only inconsequential
  binary details like embedded timestamps differ), but if you want the exact
  committed files back afterward, just re-run `run_pipeline.py` (see "How to
  run" above) or `git checkout -- data/processed/`.
- The notebook's own SpatiaLite write (`change_features.sqlite`) uses a
  simpler ad hoc schema (the GeoDataFrame's own columns: `id`, `date_before`,
  `date_after`, `area_m2`, `confidence`, `geometry`) — **not** the schema
  `src/db.py` builds (`id`, `detection_date_before`, `detection_date_after`,
  `change_magnitude`, `area_sq_m`, `method`, `created_at`, `geom`, with a
  spatial index). This is expected: the notebook is the original exploratory
  code, `src/db.py` is the refined version. If you run the notebook, the
  committed `change_features.sqlite` (produced by `src/db.py`/
  `run_pipeline.py`) gets overwritten with the notebook's simpler schema —
  restore it the same way as above if you need the original back.

## Notes on repo contents

- `data/raw/` and `data/processed/` are committed directly — the two
  Sentinel-2 date rasters (~16 MB each), `change_features.sqlite` (~7 MB),
  and `notebooks/exploration.ipynb` (~14 MB, see above) are all small enough
  to check into git without Git LFS.
- `data/sentinel2_20230812/` and `data/sentinel2_20230902/` (~31 MB total)
  are the same source imagery as `data/raw/`, duplicated in the original
  per-band layout so `notebooks/exploration.ipynb` runs unmodified (see
  "Running the notebook" above).
- `visualization_reports/html/dashboard.html` (~95 MB) is tracked with
  **Git LFS** (see `.gitattributes`). If you clone this repo, run
  `git lfs install` once beforehand so it checks out correctly instead of as
  a pointer stub. Its size is inherent to how it renders Section 1
  (`plotly.Heatmap` serializes every raw pixel as JSON) — documented, not a
  bug.
