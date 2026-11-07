#!/usr/bin/env python3
"""Verify the coverage binaries decode exactly as the browser reads them.

Matrix bins (radii 5/10/20 km): header <4sB H III 5x = 24 bytes, then one
u32 per pair (chargerIndex << 18 | companyIndex).
Mask bins (radii 50/100 km): header <4sBIHIHB3x = 21 bytes, then nClasses x
5 bytes, then nCountries x 2 bytes, zero-padded to a 4-byte boundary, then
nRecords x wordsPerRecord u32. The record array MUST start at offset % 4 == 0
or `new Uint32Array(buf, off)` throws in the browser.

Cross-checks coverage_meta.json nPairs against the bin headers.
Exit 0 = every file verified.
"""
import json
import struct
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "webapp" / "public" / "data"
MATRIX_MAGIC = b"DCDX"
MASK_MAGIC = b"DCDM"

fails = []

meta = json.loads((OUT / "coverage_meta.json").read_text())
meta_radii = set(meta.get("radii", []))

for radius in (5000, 10000, 20000):
    path = OUT / f"coverage_{radius}.bin"
    b = path.read_bytes()
    magic, ver, r, n_ch, n_co, n_pairs = struct.unpack_from("<4sBHI I I", b, 0)
    size_ok = len(b) == 24 + n_pairs * 4
    ok = magic == MATRIX_MAGIC and r == radius and size_ok
    if meta_radii:
        ok = ok and radius in meta_radii
    print(f"coverage_{radius}.bin      pairs={n_pairs:>10,}  "
          f"chargers={n_ch} companies={n_co}  size_ok={size_ok}")
    if not ok:
        fails.append(path.name)

for radius in (50000, 100000):
    path = OUT / f"coverage_mask_{radius}.bin"
    b = path.read_bytes()
    magic, ver, r, n_cls, n_rows, n_ctry, words = struct.unpack_from("<4sBIHIHB3x", b, 0)
    off = 21 + n_cls * 5 + n_ctry * 2
    off += (4 - off % 4) % 4
    size_ok = len(b) == off + n_rows * (words + 1) * 4  # record = countryIdx + words u32
    aligned = off % 4 == 0
    ok = magic == MASK_MAGIC and r == radius and size_ok and aligned
    print(f"coverage_mask_{radius}.bin rows={n_rows:>8,}  cls={n_cls} ctry={n_ctry}  "
          f"rec@{off} aligned={aligned} size_ok={size_ok}")
    if not ok:
        fails.append(path.name)

print("meta: chargers=%s companies=%s" % (len(meta["chargers"]), meta["companiesTotal"]))
if fails:
    print("FAIL:", ", ".join(fails))
    sys.exit(1)
print("ALL BINS OK")
