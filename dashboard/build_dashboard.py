#!/usr/bin/env python3
"""
Build the EcoSentinel dashboard (one self-contained index.html) from two Sentinel-2 scenes.

It runs the detection in analysis/detect_change.py, prepares the images and numbers the page needs,
and drops them into dashboard/index.template.html.

Usage (from the repo root):
    python dashboard/build_dashboard.py --old data/2023 --new data/2026 --out index.html

The words on the page (place name, dates, "281 km2", the note on hotspot #1) are written for the
Obuasi pilot. Edit dashboard/index.template.html to change them for another area.
"""
import argparse
import base64
import io
import json
import os
import sys

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "analysis"))
from detect_change import detect  # noqa: E402

TOP_HOTSPOTS = 12        # how many hotspots get a pin, a thumbnail and a place in the list
THUMB_PX = 220           # size of the before and after thumbnails

# colour ramp for the "vegetation health" view: NDVI value -> colour
RAMP_X = np.array([-0.1, 0.1, 0.25, 0.4, 0.55, 0.7, 0.85])
RAMP_RGB = np.array([[92, 54, 30], [150, 98, 52], [214, 176, 98], [205, 205, 90],
                     [140, 190, 60], [52, 150, 60], [10, 90, 42]], float)


def data_uri(img, fmt, **kw):
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    mime = "image/jpeg" if fmt == "JPEG" else "image/png"
    return f"data:{mime};base64," + base64.b64encode(buf.getvalue()).decode()


def colour_picture(tc):
    return (np.clip(np.transpose(tc, (1, 2, 0)), 0, 1) * 255).astype(np.uint8)


def ndvi_picture(ndvi):
    rgb = np.stack([np.interp(ndvi, RAMP_X, RAMP_RGB[:, c]) for c in range(3)], -1)
    return Image.fromarray(rgb.astype(np.uint8))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--template", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.template.html"))
    ap.add_argument("--out", default="index.html")
    ap.add_argument("--mine-rank", type=int, default=1,
                    help="rank of the hotspot that sits beside a licensed mine (shown with a licence-check note); 0 for none")
    args = ap.parse_args()

    r = detect(args.old, args.new)
    H, W, T, px_m2 = r["height"], r["width"], r["T"], r["px_m2"]
    loss, town, valid = r["loss"], r["town"], r["valid"]
    hotspots, groups = r["hotspots"], r["groups"]
    old, new = r["old"], r["new"]
    A, B = colour_picture(old["colour"]), colour_picture(new["colour"])

    # pictures: colour and vegetation health for both dates
    img = {
        "tc23": data_uri(Image.fromarray(A), "JPEG", quality=80, optimize=True, progressive=True),
        "tc26": data_uri(Image.fromarray(B), "JPEG", quality=80, optimize=True, progressive=True),
        "ndvi23": data_uri(ndvi_picture(old["ndvi"]), "JPEG", quality=80, optimize=True, progressive=True),
        "ndvi26": data_uri(ndvi_picture(new["ndvi"]), "JPEG", quality=80, optimize=True, progressive=True),
    }

    # change layer: soft red fill with a bright 2 px edge
    edge = loss & ~ndi.binary_erosion(loss, iterations=2)
    ov = np.zeros((H, W, 4), np.uint8)
    ov[loss] = [255, 64, 96, 95]
    ov[edge] = [255, 214, 222, 255]
    img["change"] = data_uri(Image.fromarray(ov, "RGBA"), "PNG", optimize=True)

    # "not checked" layer: hatched grey where cloud or shadow hid the ground
    invalid = r["invalid_old"] | r["invalid_new"]
    yy, xx = np.mgrid[0:H, 0:W]
    mk = np.zeros((H, W, 4), np.uint8)
    mk[invalid] = [170, 190, 210, 70]
    mk[invalid & (((xx + yy) % 14) < 3)] = [210, 224, 238, 150]
    img["mask"] = data_uri(Image.fromarray(mk, "RGBA"), "PNG", optimize=True)

    # the biggest hotspots, each with a before and after thumbnail
    hs = []
    for k, h in enumerate(hotspots[:TOP_HOTSPOTS], 1):
        m = (groups == h["id"]) & loss
        cy, cx = (h["y0"] + h["y1"]) // 2, (h["x0"] + h["x1"]) // 2
        half = max(80, max(h["y1"] - h["y0"], h["x1"] - h["x0"]) // 2 + 36)
        y0, y1, x0, x1 = max(0, cy - half), min(H, cy + half), max(0, cx - half), min(W, cx + half)
        before = Image.fromarray(A[y0:y1, x0:x1]).resize((THUMB_PX, THUMB_PX), Image.LANCZOS)
        after = B[y0:y1, x0:x1].copy()
        mm = m[y0:y1, x0:x1]
        after[mm ^ ndi.binary_erosion(mm)] = [255, 64, 96]
        after = Image.fromarray(after).resize((THUMB_PX, THUMB_PX), Image.LANCZOS)
        hs.append(dict(rank=k, ha=round(h["ha"], 1), lat=round(h["lat"], 5), lon=round(h["lon"], 5),
                       cx=round(h["cx"]), cy=round(h["cy"]), x0=h["x0"], x1=h["x1"], y0=h["y0"], y1=h["y1"],
                       kind="mine" if k == args.mine_rank else "stream",
                       before=data_uri(before, "JPEG", quality=76, optimize=True),
                       after=data_uri(after, "JPEG", quality=76, optimize=True)))

    # headline numbers
    def share(nd):
        x = nd[valid]
        return dict(dense=float((x >= 0.55).mean()), mixed=float(((x > 0.30) & (x < 0.55)).mean()),
                    bare=float((x <= 0.30).mean()))

    total_ha = loss.sum() * px_m2 / 1e4
    veg_ha = (r["veg_old"] & valid).sum() * px_m2 / 1e4
    mine_ha = next((h["ha"] for h in hs if h["kind"] == "mine"), 0.0)
    town_ha = float((loss & town).sum() * px_m2 / 1e4)
    stats = dict(
        boxKm2=round(H * W * px_m2 / 1e6), checkedPct=round(100 * float(valid.mean()), 1),
        notCheckedPct=round(100 * (1 - float(valid.mean())), 1), vegHa23=round(float(veg_ha)),
        lossHa=round(float(total_ha)), lossPct=round(100 * float(total_ha / veg_ha), 1),
        hotspots=len(hotspots), over5=int(sum(h["ha"] >= 5 for h in hotspots)),
        over20=int(sum(h["ha"] >= 20 for h in hotspots)),
        topShare=round(100 * sum(h["ha"] for h in hotspots[:10]) / float(total_ha)),
        mineHa=mine_ha, townHa=round(town_ha),
        share23=share(old["ndvi"]), share26=share(new["ndvi"]),
    )
    stats["streamHa"] = stats["lossHa"] - round(stats["mineHa"]) - stats["townHa"]

    bounds = dict(l=T.c, r=T.c + W * T.a, t=T.f, b=T.f + H * T.e)
    data = dict(W=W, H=H, bounds=bounds, stats=stats, hotspots=hs, img=img)

    template = open(args.template, encoding="utf-8").read()
    if template.count("__DATA__") != 1:
        raise SystemExit("The template must contain __DATA__ exactly once.")
    html = template.replace("__DATA__", json.dumps(data))
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {args.out} ({len(html) / 1e6:.1f} MB): {stats['lossHa']} ha, {stats['hotspots']} hotspots")


if __name__ == "__main__":
    main()
