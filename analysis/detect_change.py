#!/usr/bin/env python3
"""
EcoSentinel AI: find vegetation that turned into bare ground between two Sentinel-2 L2A scenes.

Input: two folders, each holding the files downloaded from the Copernicus Browser
(Analytical tab, TIFF 32-bit float, WGS 84) for the SAME box:
    ..._B04_(Raw).tiff        red
    ..._B08_(Raw).tiff        near infrared
    ..._True_color.tiff       visualised colour picture (used only to find clouds)

Output (in --out): hotspots.csv, summary.json, change_map.png

Usage:
    python analysis/detect_change.py --old data/2023 --new data/2026 --out results
"""
import argparse
import csv
import glob
import json
import os

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from scipy import ndimage as ndi

# ---- settings (all in one place so they are easy to explain and to tune) ----
VEG_MIN = 0.55          # NDVI at or above this counts as dense vegetation
BARE_MAX = 0.30         # NDVI at or below this counts as bare ground
DROP_MIN = 0.30         # NDVI must also fall by at least this much
MIN_PATCH_PX = 20       # ignore patches smaller than about 0.24 ha
CLUSTER_GAP_PX = 14     # patches closer than about 150 m join one hotspot
CLOUD_BRIGHT = 0.72     # colour brightness above which a pixel is cloud
CLOUD_RED = 0.35        # red reflectance above which a pixel is cloud
CLOUD_GROW_PX = 9       # widen the cloud mask to catch thin cloud edges
SHADOW_NIR_MAX = 0.17   # dark in near infrared ...
SHADOW_NDVI_MIN = 0.40  # ... but still green: looks like a cloud shadow on forest
SHADOW_SEARCH_PX = 70   # only look for shadows this close to a cloud
TOWN_WINDOW_PX = 31     # window used to find places that were already mostly open ground
TOWN_VEG_FRACTION = 0.45


def read(folder, key):
    path = glob.glob(os.path.join(folder, f"*{key}*.tif*"))
    if not path:
        raise FileNotFoundError(f"No file matching *{key}* in {folder}")
    with rasterio.open(path[0]) as ds:
        return ds.read().astype("float32"), ds.transform


def load_scene(folder):
    red, transform = read(folder, "B04")
    nir, _ = read(folder, "B08")
    colour, _ = read(folder, "True_color")
    red, nir = red[0], nir[0]
    ndvi = (nir - red) / (nir + red + 1e-9)
    return dict(red=red, nir=nir, colour=colour, ndvi=ndvi, transform=transform)


def cloud_and_shadow(scene):
    """Rule-based mask: bright, whitish pixels are cloud; dark green patches near a cloud are shadow."""
    r, g, b = scene["colour"]
    brightness = np.minimum(np.minimum(r, g), b)
    core = (brightness > CLOUD_BRIGHT) | (scene["red"] > CLOUD_RED)
    opened = ndi.binary_opening(core, iterations=1)
    core = opened | (core & ndi.binary_dilation(opened, iterations=2))
    cloud = ndi.binary_dilation(core, iterations=CLOUD_GROW_PX)
    near_cloud = ndi.binary_dilation(cloud, iterations=SHADOW_SEARCH_PX)
    shadow = (scene["nir"] < SHADOW_NIR_MAX) & (scene["ndvi"] > SHADOW_NDVI_MIN) & near_cloud
    shadow = ndi.binary_dilation(ndi.binary_opening(shadow, iterations=1), iterations=4)
    return cloud | shadow


def pixel_area_m2(transform, height):
    lat = transform.f + transform.e * height / 2
    dy = abs(transform.e) * 111320
    dx = abs(transform.a) * 111320 * np.cos(np.radians(lat))
    return dx * dy


def detect(old_dir, new_dir):
    """Run the whole detection and return everything the outputs and the dashboard need."""
    old, new = load_scene(old_dir), load_scene(new_dir)
    if old["ndvi"].shape != new["ndvi"].shape:
        raise SystemExit("The two scenes must cover exactly the same box and size.")
    height, width = old["ndvi"].shape
    T = old["transform"]
    px_m2 = pixel_area_m2(T, height)

    invalid_old, invalid_new = cloud_and_shadow(old), cloud_and_shadow(new)
    valid = ~(invalid_old | invalid_new)

    # 1. Detect: dense vegetation before, bare ground now, clearly lower NDVI, not under cloud
    veg_old = old["ndvi"] >= VEG_MIN
    bare_new = new["ndvi"] <= BARE_MAX
    drop = new["ndvi"] - old["ndvi"]
    loss = veg_old & bare_new & (drop < -DROP_MIN) & valid
    loss = ndi.binary_opening(loss, structure=np.ones((2, 2)))
    labels, n = ndi.label(loss)
    sizes = ndi.sum(loss, labels, index=np.arange(1, n + 1))
    loss = np.isin(labels, np.where(sizes >= MIN_PATCH_PX)[0] + 1)

    # 2. Assess: separate town and already-open land from forest, then group patches into hotspots
    veg_fraction = ndi.uniform_filter((veg_old & ~invalid_old).astype("float32"), size=TOWN_WINDOW_PX)
    town = ndi.binary_opening(ndi.binary_closing(veg_fraction < TOWN_VEG_FRACTION, iterations=6), iterations=6)
    groups, g = ndi.label(ndi.binary_dilation(loss, iterations=CLUSTER_GAP_PX))
    hotspots = []
    for i in range(1, g + 1):
        m = (groups == i) & loss
        px = int(m.sum())
        if not px:
            continue
        ys, xs = np.where(m)
        cy, cx = ys.mean(), xs.mean()
        hotspots.append(dict(
            id=i, px=px, ha=px * px_m2 / 1e4,
            cx=float(cx), cy=float(cy),
            x0=int(xs.min()), x1=int(xs.max()), y0=int(ys.min()), y1=int(ys.max()),
            lat=T.f + (cy + 0.5) * T.e,
            lon=T.c + (cx + 0.5) * T.a,
            zone="town / open land" if town[int(cy), int(cx)] else "forest / stream zone",
        ))
    hotspots.sort(key=lambda h: -h["ha"])
    return dict(old=old, new=new, T=T, height=height, width=width, px_m2=px_m2, valid=valid,
                invalid_old=invalid_old, invalid_new=invalid_new, veg_old=veg_old, loss=loss,
                town=town, groups=groups, hotspots=hotspots)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", required=True, help="folder with the earlier scene")
    ap.add_argument("--new", required=True, help="folder with the later scene")
    ap.add_argument("--out", default="results")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    r = detect(args.old, args.new)
    old, new, valid, loss, town = r["old"], r["new"], r["valid"], r["loss"], r["town"]
    hotspots, px_m2, height, width, veg_old = r["hotspots"], r["px_m2"], r["height"], r["width"], r["veg_old"]

    with open(os.path.join(args.out, "hotspots.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "latitude", "longitude", "new_bare_ground_ha", "zone"])
        for k, h in enumerate(hotspots, 1):
            w.writerow([k, round(h["lat"], 5), round(h["lon"], 5), round(h["ha"], 1), h["zone"]])

    total_ha = loss.sum() * px_m2 / 1e4
    veg_ha = (veg_old & valid).sum() * px_m2 / 1e4
    summary = dict(
        checked_share_pct=round(100 * float(valid.mean()), 1),
        vegetation_before_ha=round(float(veg_ha)),
        new_bare_ground_ha=round(float(total_ha)),
        new_bare_ground_pct_of_vegetation=round(100 * float(total_ha / veg_ha), 1),
        hotspots=len(hotspots),
        hotspots_over_5_ha=int(sum(h["ha"] >= 5 for h in hotspots)),
        hotspots_over_20_ha=int(sum(h["ha"] >= 20 for h in hotspots)),
        top10_share_pct=round(100 * sum(h["ha"] for h in hotspots[:10]) / float(total_ha)),
    )
    with open(os.path.join(args.out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    # 3. Picture for a human to check: the newer scene with the change in red
    base = (np.clip(np.transpose(new["colour"], (1, 2, 0)), 0, 1) * 255).astype(np.uint8)
    img = base.copy()
    img[loss & ~town] = [255, 0, 60]
    img[loss & town] = [255, 150, 0]
    img[~valid] = (0.5 * img[~valid] + 0.5 * 120).astype(np.uint8)
    out_w = 1800
    out_h = int(height * out_w / width)
    canvas = Image.new("RGB", (out_w, out_h + 50), (255, 255, 255))
    canvas.paste(Image.fromarray(img).resize((out_w, out_h), Image.LANCZOS), (0, 0))
    d = ImageDraw.Draw(canvas)
    x = 16
    for colour, label in [((255, 0, 60), "New bare ground in forest / stream areas"),
                          ((255, 150, 0), "New bare ground in town / open land"),
                          ((150, 150, 150), "Not checked (cloud or shadow)")]:
        d.rectangle([x, out_h + 16, x + 22, out_h + 34], fill=colour)
        d.text((x + 30, out_h + 18), label, fill=(0, 0, 0))
        x += 30 + 8 * len(label) + 36
    canvas.save(os.path.join(args.out, "change_map.png"))

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
