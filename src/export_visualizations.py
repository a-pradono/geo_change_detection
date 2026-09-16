"""Export the Part 4 Plotly visualizations as a SINGLE combined dashboard
HTML file (visualization_reports/html/dashboard.html) -- no separate per-figure files.

Each build_*_fig() function reproduces the exact plotting logic already
validated interactively in analysis_notebook.ipynb (Part 4 cells) -- the
computations are unchanged, just parameterized instead of reading notebook
globals, so the same code can run from either the notebook or the CLI
pipeline without recomputing anything.

NaN-safe handling: after_norm, pca_mag, and any other AOI-derived array may
contain NaN outside the AOI (see preprocessing.py/change_detection.py).
Every stretch/normalize function here uses np.nanpercentile instead of
np.percentile, and np.nan_to_num(img, nan=0) is applied immediately before
casting to uint8 for display, so the excluded AOI region renders as black
rather than corrupting the cast. Any percentage shown in a figure title uses
valid_rows (in-AOI pixel count) as the denominator, not .mean().
"""
import base64
from io import BytesIO
from pathlib import Path

import geopandas as gpd
import numpy as np
import plotly.graph_objects as go
from PIL import Image
from plotly.subplots import make_subplots

DEFAULT_REPORT_DIR = Path("visualization_reports/html")


def _stretch(img: np.ndarray) -> np.ndarray:
    """2nd-98th percentile stretch to [0, 1] -- identical to the notebook's
    `stretch()` helper, made NaN-safe: np.clip already passes NaN through
    unchanged, and np.nanpercentile ignores NaN when computing the stretch
    bounds so AOI-excluded pixels don't skew them. NaN pixels remain NaN in
    the output (resolved to black by the caller, right before the uint8
    cast, via np.nan_to_num).
    """
    img = np.clip(img, 0, 1)
    lo, hi = np.nanpercentile(img, (2, 98))
    return np.clip((img - lo) / (hi - lo + 1e-8), 0, 1)


def _to_uint8_rgb(img: np.ndarray) -> np.ndarray:
    """Stretch + resolve NaN to black (0) + cast to uint8, for display."""
    stretched = _stretch(img)
    stretched = np.nan_to_num(stretched, nan=0.0)
    return (stretched * 255).astype(np.uint8)


# --- 1. Raw bands per date ---------------------------------------------------

def build_raw_bands_fig(arrays: dict, DATES: list, BANDS: list) -> go.Figure:
    """Grid of raw bands per date (date x band heatmaps).

    arrays: dict[date][band] -> 2D np.ndarray, raw and pre-AOI-clip -- no
    NaN present, no NaN handling needed.

    Reproduces analysis_notebook.ipynb cell `de672657` exactly.
    """
    titles = [f"{date}<br>{band}" for date in DATES for band in BANDS]

    fig = make_subplots(rows=len(DATES), cols=len(BANDS), subplot_titles=titles,
                         vertical_spacing=0.08, horizontal_spacing=0.03)

    for row, date in enumerate(DATES):
        for col, band in enumerate(BANDS):
            img = arrays[date][band]
            valid = img[img > 0]
            vmin, vmax = np.percentile(valid, (2, 98))

            fig.add_trace(
                go.Heatmap(
                    z=img,
                    colorscale="gray",
                    zmin=vmin,
                    zmax=vmax,
                    showscale=False
                ),
                row=row + 1, col=col + 1
            )

    fig.update_yaxes(autorange="reversed")  # match imshow's top-down convention
    fig.update_xaxes(showticklabels=False)
    fig.update_yaxes(showticklabels=False)
    fig.update_layout(
        height=400 * len(DATES),
        width=400 * len(BANDS),
        title="Raw Bands Per Date"
    )
    return fig


# --- 2. Stacked true color per date ------------------------------------------

def build_true_color_fig(stacked: dict, DATES: list) -> go.Figure:
    """Stacked true color composite, before vs after, side by side.

    stacked: dict[date] -> (H, W, 3) array, raw and pre-AOI-clip -- no NaN
    present, no NaN handling needed.

    Reproduces analysis_notebook.ipynb cell `cfbeef71` exactly.
    """
    fig = make_subplots(rows=1, cols=len(DATES),
                         subplot_titles=[f"{date} (Stacked True Color)" for date in DATES])

    for i, date in enumerate(DATES):
        img = stacked[date].astype(np.float32)
        valid = img[img > 0]
        vmin, vmax = np.percentile(valid, (2, 98))
        img_stretched = np.clip((img - vmin) / (vmax - vmin), 0, 1)
        img_uint8 = (img_stretched * 255).astype(np.uint8)

        fig.add_trace(go.Image(z=img_uint8), row=1, col=i + 1)

    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    fig.update_layout(height=600, width=600 * len(DATES), title="Stacked True Color Comparison")
    return fig


# --- 3. Change mask overlay BEFORE cleaning artifacts ------------------------

def build_raw_change_fig(after_norm: np.ndarray, pca_mask: np.ndarray, valid_rows) -> go.Figure:
    """Change mask overlay before artifact cleaning.

    after_norm: (H, W, 3) radiometrically-normalized "after" image (RGB
    order, [::-1] to match the notebook's BGR->RGB display convention). May
    contain NaN outside the AOI.
    pca_mask: (H, W) raw (pre-cleanup, AOI-clipped) binary change mask, 0
    outside the AOI.
    valid_rows: in-AOI pixel count (int), or a boolean array whose .sum()
    gives it -- the denominator for the percentage shown in the title, NOT
    pca_mask.mean() (which would include out-of-AOI pixels).

    Should read ~3.68% on the validated data.
    """
    n_valid = int(np.sum(valid_rows))

    overlay = _to_uint8_rgb(after_norm[:, :, ::-1]).astype(np.float32) / 255.0
    overlay[pca_mask == 1] = [1, 0, 0]
    overlay = np.nan_to_num(overlay, nan=0.0)
    overlay_uint8 = (overlay * 255).astype(np.uint8)

    pct_raw = 100 * pca_mask.sum() / n_valid

    fig = go.Figure(go.Image(z=overlay_uint8))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    fig.update_layout(
        title=f"Change mask overlay ({pct_raw:.2f}% of AOI flagged)",
        height=700, width=700,
        margin=dict(l=0, r=0, t=40, b=0)
    )
    return fig


# --- 4. Change mask overlay AFTER cleaning artifacts -------------------------

def build_cleaned_change_fig(
    after_norm: np.ndarray, pca_mask_opened: np.ndarray, valid_rows
) -> go.Figure:
    """Change mask overlay after artifact cleaning (morphological opening).

    pca_mask_opened: (H, W) uint8, the final cleaned binary mask, 0 outside
    the AOI. Same NaN-safe display handling as build_raw_change_fig.

    Should read ~2.08% on the validated data.
    """
    n_valid = int(np.sum(valid_rows))

    overlay = _to_uint8_rgb(after_norm[:, :, ::-1]).astype(np.float32) / 255.0
    overlay[pca_mask_opened == 1] = [1, 0, 0]
    overlay = np.nan_to_num(overlay, nan=0.0)
    overlay_uint8 = (overlay * 255).astype(np.uint8)

    pct_clean = 100 * pca_mask_opened.sum() / n_valid

    fig = go.Figure(go.Image(z=overlay_uint8))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    fig.update_layout(
        title=f"Cleaned change mask ({pct_clean:.2f}% of AOI)",
        height=700, width=700,
        margin=dict(l=0, r=0, t=40, b=0)
    )
    return fig


# --- 5. AOI + Vectorized change polygons (primary deliverable) --------------

def build_polygons_on_raster_fig(
    after_norm: np.ndarray, change_gdf, aoi_gdf, base_profile: dict
) -> go.Figure:
    """AOI boundary + vectorized change polygons overlaid on the raster, in
    the raster's native CRS (no reprojection of the raster/change polygons).

    Three layers: the true-color raster (base64-embedded PNG, AOI-clipped,
    NaN resolved to black), the actual AOI boundary from aoi.geojson (yellow
    dashed, reprojected to base_profile["crs"] if not already), and the
    change polygon boundaries from change_gdf (red). All positioned using
    base_profile["transform"].
    """
    transform = base_profile["transform"]
    west = transform.c
    east = transform.c + base_profile["width"] * transform.a
    north = transform.f
    south = transform.f + base_profile["height"] * transform.e  # transform.e is negative

    rgb = _to_uint8_rgb(after_norm[:, :, ::-1])
    img = Image.fromarray(rgb)
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    img_source = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

    fig = go.Figure()

    fig.add_layout_image(
        source=img_source,
        xref="x", yref="y",
        x=west, y=north,
        sizex=east - west, sizey=north - south,
        xanchor="left", yanchor="top",
        sizing="stretch",
        layer="below"
    )

    # AOI boundary: the actual aoi.geojson polygon, reprojected into the
    # raster's native CRS if it isn't already.
    if aoi_gdf.crs != base_profile["crs"]:
        aoi_gdf = aoi_gdf.to_crs(base_profile["crs"])
    for i, geom in enumerate(aoi_gdf.geometry):
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        for poly in polys:
            x, y = poly.exterior.xy
            fig.add_trace(go.Scatter(
                x=list(x), y=list(y),
                mode="lines",
                line=dict(color="yellow", width=2, dash="dash"),
                name="AOI",
                showlegend=(i == 0),
                hoverinfo="skip"
            ))

    # Change polygons: boundary traces, in the raster's native CRS.
    for geom in change_gdf.geometry:
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        for poly in polys:
            x, y = poly.exterior.xy
            fig.add_trace(go.Scatter(
                x=list(x), y=list(y),
                mode="lines",
                line=dict(color="red", width=1.5),
                showlegend=False,
                hoverinfo="skip"
            ))

    fig.update_xaxes(range=[west, east], visible=False)
    fig.update_yaxes(range=[south, north], visible=False, scaleanchor="x", scaleratio=1)

    total_ha = change_gdf["area_m2"].sum() / 1e4
    fig.update_layout(
        title=f"AOI + Vectorized Change Polygons (n={len(change_gdf)}, {total_ha:.1f} ha)",
        height=800, width=800,
        margin=dict(l=0, r=0, t=40, b=0)
    )
    return fig


# --- Combine into a single dashboard HTML file -------------------------------

def build_dashboard(figures: list, output_path) -> Path:
    """Combine all figures into a single scrollable HTML file with section
    headers, loading plotly.js once at the top rather than once per figure.

    Parameters
    ----------
    figures : list of (section_title, go.Figure) tuples, in display order.
    output_path : where to write the combined HTML file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    html_parts = [
        "<html><head><meta charset='utf-8'>"
        "<script src='https://cdn.plot.ly/plotly-2.35.2.min.js'></script></head><body>"
    ]
    for title, fig in figures:
        html_parts.append(f"<h2>{title}</h2>")
        html_parts.append(fig.to_html(full_html=False, include_plotlyjs=False))
    html_parts.append("</body></html>")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(html_parts))

    return output_path


def export_dashboard(
    output_dir: Path,
    arrays: dict,
    stacked: dict,
    DATES: list,
    BANDS: list,
    after_norm: np.ndarray,
    pca_mask: np.ndarray,
    pca_mask_opened: np.ndarray,
    change_gdf,
    aoi_gdf,
    base_profile: dict,
    valid_rows,
) -> Path:
    """Build all 5 figures and combine them into the single
    visualization_reports/html/dashboard.html file -- the only output of this module.
    """
    figures = [
        ("Raw Bands Per Date", build_raw_bands_fig(arrays, DATES, BANDS)),
        ("Stacked True Color Comparison", build_true_color_fig(stacked, DATES)),
        ("Change Mask Overlay (Before Cleanup)",
         build_raw_change_fig(after_norm, pca_mask, valid_rows)),
        ("Change Mask Overlay (After Cleanup)",
         build_cleaned_change_fig(after_norm, pca_mask_opened, valid_rows)),
        ("AOI + Vectorized Change Polygons",
         build_polygons_on_raster_fig(after_norm, change_gdf, aoi_gdf, base_profile)),
    ]

    output_dir = Path(output_dir)
    dashboard_path = output_dir / "dashboard.html"
    return build_dashboard(figures, dashboard_path)
