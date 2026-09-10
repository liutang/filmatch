#!/usr/bin/env python3
"""
filmatch - match a 3MF project's filament colors to spools you already own.

  python3 filmatch.py Project.3mf                       # --spools defaults to my-spools.json
  python3 filmatch.py Project.3mf --suggest --brands polymaker,bambu
  python3 filmatch.py Project.3mf --spools other.json --html report.html

Reads Bambu Studio / OrcaSlicer 3MFs (per-plate usage, including painted
regions) and PrusaSlicer 3MFs (slot colors + which slots are used).
Inventory: 3DFilamentProfiles "Export My Spools" JSON or CSV.
Color distance: CIEDE2000 (ΔE). Standard library only, Python 3.8+.

Each slot's target color defaults to filamentcolors.xyz's spectrophotometer-
measured Lab for the same filament (matched by vendor + material family, nearest
color within a small ΔE tolerance), falling back to the slot's requested hex
from the 3MF otherwise. Pass --no-measured to always use the requested hex.
"""
import argparse, csv, html, json, math, os, re, sys, time, zipfile
import urllib.error, urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------- color math
HEX_RE = re.compile(r"^#?([0-9A-Fa-f]{6})([0-9A-Fa-f]{2})?$")


def norm_hex(h):
    """'#aabbcc' / 'AABBCCFF' -> '#AABBCC', or None if not a single color."""
    m = HEX_RE.match((h or "").strip())
    return "#" + m.group(1).upper() if m else None


def hex_to_lab(h):
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    x = (0.4124564 * lin[0] + 0.3575761 * lin[1] + 0.1804375 * lin[2]) / 0.95047
    y = (0.2126729 * lin[0] + 0.7151522 * lin[1] + 0.0721750 * lin[2])
    z = (0.0193339 * lin[0] + 0.1191920 * lin[1] + 0.9503041 * lin[2]) / 1.08883
    f = [t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116 for t in (x, y, z)]
    return 116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])


def de2000(lab1, lab2):
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cb7 = ((C1 + C2) / 2) ** 7
    G = 0.5 * (1 - math.sqrt(Cb7 / (Cb7 + 25 ** 7)))
    a1p, a2p = a1 * (1 + G), a2 * (1 + G)
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if C1p else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if C2p else 0.0
    dLp, dCp = L2 - L1, C2p - C1p
    dh = h2p - h1p
    if C1p * C2p == 0:
        dh = 0
    elif dh > 180:
        dh -= 360
    elif dh < -180:
        dh += 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dh / 2))
    Lbp, Cbp = (L1 + L2) / 2, (C1p + C2p) / 2
    if C1p * C2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2
    else:
        hbp = (h1p + h2p + 360) / 2 if h1p + h2p < 360 else (h1p + h2p - 360) / 2
    T = (1 - 0.17 * math.cos(math.radians(hbp - 30)) + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6)) - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    Sl = 1 + 0.015 * (Lbp - 50) ** 2 / math.sqrt(20 + (Lbp - 50) ** 2)
    Sc, Sh = 1 + 0.045 * Cbp, 1 + 0.015 * Cbp * T
    Cbp7 = Cbp ** 7
    Rt = (-2 * math.sqrt(Cbp7 / (Cbp7 + 25 ** 7))
          * math.sin(math.radians(60 * math.exp(-(((hbp - 275) / 25) ** 2)))))
    return math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2
                     + Rt * (dCp / Sc) * (dHp / Sh))


def family(material):
    """'PLA+/Pro' -> 'PLA', 'PETG-HF' -> 'PETG'."""
    return re.split(r"[\s\-+/]", (material or "").strip().upper())[0]

# --------------------------------------------------------------- 3MF parsing
PAINT_RE = re.compile(rb'(?:paint_color|mmu_segmentation)="([0-9A-Fa-f]+)"')
OBJ_RE = re.compile(rb'<object\s+id="(\d+)"')


def paint_states(code):
    """Decode a TriangleSelector string (Prusa/Bambu/Orca MMU painting) into leaf states.
    Nibbles are read from the end of the string; state 0 = unpainted, N = filament N."""
    nib = [int(c, 16) for c in reversed(code)]
    out, i = set(), 0

    def node():
        nonlocal i
        c = nib[i]; i += 1
        split = c & 0b11
        if split:
            for _ in range(split + 1):
                node()
        elif c & 0b1100 == 0b1100:
            out.add(nib[i] + 3); i += 1
        else:
            out.add(c >> 2)
    try:
        node()
    except IndexError:
        pass
    return out


def scan_paint(z, name):
    """{object_id: set(paint states)} for one .model file."""
    data = z.read(name)
    starts = [(m.start(), m.group(1).decode()) for m in OBJ_RE.finditer(data)]
    res = {}
    for k, (pos, oid) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(data)
        states = set()
        for code in set(PAINT_RE.findall(data, pos, end)):
            states |= paint_states(code.decode())
        res[oid] = states
    return res


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _attr(el, name):
    for k, v in el.attrib.items():
        if _local(k) == name:
            return v
    return None


def _meta(el):
    return {m.get("key"): m.get("value") for m in el.findall("metadata")}


def read_project(path):
    z = zipfile.ZipFile(path)
    names = set(z.namelist())
    slots, source = [], "unknown"

    if "Metadata/project_settings.config" in names:           # Bambu / Orca
        cfg = json.loads(z.read("Metadata/project_settings.config"))
        colors = cfg.get("filament_colour") or []
        types = cfg.get("filament_type") or []
        ids = cfg.get("filament_settings_id") or []
        vendors = cfg.get("filament_vendor") or []
        source = cfg.get("printer_settings_id") or "Bambu/Orca"
    elif "Metadata/Slic3r_PE.config" in names:                 # PrusaSlicer
        ini = {}
        for line in z.read("Metadata/Slic3r_PE.config").decode(errors="replace").splitlines():
            m = re.match(r";\s*([\w]+)\s*=\s*(.*)$", line)
            if m:
                ini[m.group(1)] = m.group(2)
        split = lambda k: [s.strip().strip('"') for s in ini.get(k, "").split(";")] if ini.get(k) else []
        colors = split("filament_colour")
        ext = split("extruder_colour")
        colors = [e if norm_hex(e) else c for e, c in zip(ext + [""] * len(colors), colors)] or ext
        types, ids = split("filament_type"), split("filament_settings_id")
        vendors = split("filament_vendor")
        source = ini.get("printer_model") or "PrusaSlicer"
    else:
        sys.exit("No slicer config found in 3MF (expected Bambu/Orca or PrusaSlicer project).")

    for i, c in enumerate(colors):
        profile = (ids[i] if i < len(ids) else "").split("@")[0].strip()
        vendor = (vendors[i] if i < len(vendors) else "") or profile.split(" ")[0]
        slots.append({
            "slot": i + 1,
            "hex": norm_hex(c),
            "type": types[i] if i < len(types) else "",
            "profile": profile,
            "vendor": vendor,
            "used_on": [],
        })

    # Paint states per (model file, object id)
    paint = {}
    for n in names:
        if n.endswith(".model"):
            for oid, st in scan_paint(z, n).items():
                paint[(n, oid)] = st

    plates = []
    if "Metadata/model_settings.config" in names and "3D/3dmodel.model" in names:
        ms = ET.fromstring(z.read("Metadata/model_settings.config"))
        root = ET.fromstring(z.read("3D/3dmodel.model"))
        comps = {}
        for obj in root.iter():
            if _local(obj.tag) != "object":
                continue
            cl = [((_attr(c, "path") or "/3D/3dmodel.model").lstrip("/"), c.get("objectid"))
                  for c in obj.iter() if _local(c.tag) == "component"]
            comps[obj.get("id")] = cl or [("3D/3dmodel.model", obj.get("id"))]

        objects = {}
        for obj in ms.findall("object"):
            meta = _meta(obj)
            obj_ext = int(meta.get("extruder") or 1)
            parts = {p.get("id"): p for p in obj.findall("part")}
            used, part_list = set(), list(obj.findall("part"))
            for idx, (fname, sub_id) in enumerate(comps.get(obj.get("id"), [])):
                part = parts.get(sub_id)
                if part is None and idx < len(part_list):
                    part = part_list[idx]
                pext = int((_meta(part).get("extruder") if part is not None else 0) or 0) or obj_ext
                used.add(pext)
                for s in paint.get((fname, sub_id), ()):
                    used.add(s if s else pext)
            if not comps.get(obj.get("id")):
                used.add(obj_ext)
            objects[obj.get("id")] = (meta.get("name", "?"), used)

        for pl in ms.findall("plate"):
            meta = _meta(pl)
            used, objnames = set(), []
            for inst in pl.findall("model_instance"):
                oid = _meta(inst).get("object_id")
                if oid in objects:
                    objnames.append(objects[oid][0]); used |= objects[oid][1]
            plates.append({"id": meta.get("plater_id"),
                           "name": meta.get("plater_name") or ", ".join(objnames),
                           "slots": sorted(s for s in used if 1 <= s <= len(slots))})
    if not plates:  # generic fallback: any extruder reference or painted state anywhere
        used = set()
        for n in names:
            if n.startswith("Metadata/") and n.endswith(".config"):
                used |= {int(v) for v in re.findall(rb'key="extruder"\s+value="(\d+)"', z.read(n))}
        for st in paint.values():
            used |= st
        used = {s for s in used if 1 <= s <= len(slots)} or set(range(1, len(slots) + 1))
        plates = [{"id": "-", "name": "all objects", "slots": sorted(used)}]

    for pl in plates:
        for s in pl["slots"]:
            slots[s - 1]["used_on"].append(pl["id"])
    return {"file": Path(path).name, "source": source, "slots": slots, "plates": plates}

# ----------------------------------------------------------------- inventory
def load_spools(path, min_grams=1, exclude_finishes=()):
    p = Path(path)
    if p.suffix.lower() == ".csv":
        with open(p, newline="", encoding="utf-8-sig") as f:
            rows = [{k.strip().lower().replace(" ", "_"): v for k, v in r.items()} for r in csv.DictReader(f)]
    else:
        data = json.loads(p.read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else next(v for v in data.values() if isinstance(v, list))

    excl = {e.strip().lower() for e in exclude_finishes if e.strip()}
    groups = {}
    skipped = 0
    for r in rows:
        hx = norm_hex(r.get("rgb") or r.get("color_hex") or r.get("hex"))
        finish = (r.get("material_type") or "").strip()
        grams = r.get("remaining_grams")
        try:
            grams = float(grams) if grams not in (None, "") else None
        except ValueError:
            grams = None
        if not hx or (grams is not None and grams < min_grams) or any(e in finish.lower() for e in excl):
            skipped += 1
            continue
        key = (r.get("brand"), r.get("material"), finish, r.get("color"), hx)
        g = groups.setdefault(key, {
            "brand": r.get("brand") or "", "material": r.get("material") or "", "finish": finish,
            "color": r.get("color") or "", "hex": hx, "lab": hex_to_lab(hx),
            "count": 0, "grams": 0.0, "locations": set()})
        g["count"] += 1
        g["grams"] += grams or 0
        if r.get("location"):
            g["locations"].add(r["location"])
    return list(groups.values()), skipped

# ----------------------------------------------------------- assignment
def hungarian(cost):
    """Min-cost assignment of n rows to distinct columns (n <= m). Returns col per row."""
    n, m = len(cost), len(cost[0])
    INF = float("inf")
    u, v, p, way = [0.0] * (n + 1), [0.0] * (m + 1), [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        minv, used = [INF] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta; v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]; p[j0] = p[j1]; j0 = j1
    ans = [None] * n
    for j in range(1, m + 1):
        if p[j]:
            ans[p[j] - 1] = j - 1
    return ans


def match(project, spools, any_material=False, top=3, allow_reuse=False):
    rows = []
    for s in project["slots"]:
        if not s["used_on"] or not s["hex"]:
            continue
        lab = s.get("lab_target") or hex_to_lab(s["hex"])
        fam = family(s["type"])
        ranked = sorted(
            ((de2000(lab, sp["lab"]), k) for k, sp in enumerate(spools)
             if any_material or not fam or family(sp["material"]) == fam),
            key=lambda t: t[0])
        rows.append({**s, "lab": lab, "ranked": ranked[:max(top, 1)], "all": dict((k, d) for d, k in ranked)})

    for r in rows:
        r["pick"] = r["ranked"][0] if r["ranked"] else None
    if not allow_reuse and rows and spools:
        # Squared ΔE so one terrible match costs more than two mediocre ones.
        BIG = 1e9
        cost = [[r["all"][k] ** 2 if k in r["all"] else BIG for k in range(len(spools))] for r in rows]
        if len(rows) <= len(spools):
            for r, k in zip(rows, hungarian(cost)):
                if k is not None and k in r["all"]:
                    r["pick"] = (r["all"][k], k)
    return rows

# ---------------------------------------------------------- filamentcolors.xyz
FC_API = "https://filamentcolors.xyz/api/swatch/?page_size=100"
UA = "filmatch/1.0 (personal spool matcher; caches results locally)"


def cache_path():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "filmatch" / "filamentcolors.json"


def load_filamentcolors(refresh=False, max_age_days=30):
    cp = cache_path()
    if cp.exists() and not refresh and time.time() - cp.stat().st_mtime < max_age_days * 86400:
        cached = json.loads(cp.read_text())
        if cached and "lab" in cached[0]:      # caches from before measured-Lab support: refetch
            return cached
    print("Downloading filamentcolors.xyz library (one-time, cached ~30 days)...", file=sys.stderr)
    url, out = FC_API, []
    while url:
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    page = json.load(resp)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 3:
                    time.sleep(int(e.headers.get("Retry-After") or 30))
                    continue
                raise
        for s in page.get("results", []):
            hx = norm_hex(s.get("hex_color"))
            if not hx:
                continue
            ft = s.get("filament_type") or {}
            lab_meas = [s.get("lab_l"), s.get("lab_a"), s.get("lab_b")]
            out.append({
                "id": s.get("id"), "hex": hx, "color": s.get("color_name") or "",
                "brand": (s.get("manufacturer") or {}).get("name") or "",
                "type": ft.get("name") or "",
                "family": ((ft.get("parent_type") or {}).get("name") or ft.get("name") or ""),
                "buy": s.get("mfr_purchase_link") or s.get("amazon_purchase_link") or "",
                "available": bool(s.get("is_available", True)),
                # spectrophotometer-measured Lab; fall back to the measured hex if absent
                "lab": lab_meas if None not in lab_meas else list(hex_to_lab(hx)),
            })
        print(f"  {len(out)} swatches...", file=sys.stderr)
        url = page.get("next")
        if url:
            time.sleep(2)  # be polite
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps(out))
    return out


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def apply_measured_targets(project, swatches, tol=3.0):
    """Refine each used slot's target color to filamentcolors.xyz's measured Lab for
    the same filament: among swatches whose brand matches the slot's vendor and whose
    material family matches, take the one nearest the slot's requested hex, and adopt
    its measured Lab when within `tol` ΔE (a guard that only refines when there is a
    clear one-to-one product match, rather than snapping to a different color).
    Sets slot['lab_target'] and slot['measured'] = (swatch id, hex, ΔE shift);
    returns the number of slots refined."""
    by_vendor = defaultdict(list)
    for sw in swatches:
        by_vendor[_norm(sw["brand"])].append(sw)
    hits = 0
    for s in project["slots"]:
        if not s["used_on"] or not s["hex"]:
            continue
        want = hex_to_lab(s["hex"])
        fam = family(s["type"])
        cands = [sw for sw in by_vendor.get(_norm(s["vendor"]), [])
                 if not fam or fam in (family(sw["family"]), family(sw["type"]))]
        if not cands:
            continue
        sw = min(cands, key=lambda w: de2000(want, w["lab"]))
        shift = de2000(want, sw["lab"])
        if shift <= tol:
            s["lab_target"] = list(sw["lab"])
            s["measured"] = (sw["id"], sw["hex"], round(shift, 1))
            hits += 1
    return hits


def suggest(rows, swatches, brands=(), n=3, threshold=5.0, any_material=False,
            include_unavailable=False, exclude=()):
    brands = [b.strip().lower() for b in brands if b.strip()]
    excl = {e.strip().lower() for e in exclude if e.strip()}
    labs = [(sw, hex_to_lab(sw["hex"])) for sw in swatches
            if not any(e in f'{sw["type"]} {sw["color"]}'.lower() for e in excl)]
    for r in rows:
        if r["pick"] and round(r["pick"][0], 1) <= threshold:
            r["suggest"] = []
            continue
        fam = family(r["type"])
        cands = [(de2000(r["lab"], lab), sw) for sw, lab in labs
                 if (include_unavailable or sw["available"])
                 and (any_material or not fam or family(sw["family"]) == fam)
                 and (not brands or any(b in sw["brand"].lower() for b in brands))]
        r["suggest"] = sorted(cands, key=lambda t: t[0])[:n]

# ------------------------------------------------------------------- output
def sw(hx, color):
    if not color or not hx:
        return ""
    r, g, b = (int(hx[i:i + 2], 16) for i in (1, 3, 5))
    return f"\x1b[48;2;{r};{g};{b}m    \x1b[0m "


SPECIAL_FINISHES = ("silk", "glitter", "marble", "starlight", "sparkle", "galaxy", "metal",
                    "satin", "wood", "translucent", "glow")


def finish_note(sp):
    f = sp["finish"].lower()
    return f"  [! {sp['finish']} finish]" if any(x in f for x in SPECIAL_FINISHES) else ""


def grade(d, t):
    d = round(d, 1)
    return "OK " if d <= t else ("~  " if d <= 2 * t else "X  ")


def spool_label(sp):
    extra = f" x{sp['count']}" if sp["count"] > 1 else ""
    return f"{sp['brand']} {sp['material']} {sp['finish']} {sp['color']}{extra}".replace("  ", " ")


def print_report(project, rows, spools, threshold, color, skipped):
    print(f"\n{project['file']}  ({project['source']})")
    print(f"{len(project['slots'])} slots, {len(rows)} used, {len(spools)} candidate filaments "
          f"in inventory ({skipped} spools skipped: multi-color/no hex/empty/excluded)\n")
    for pl in project["plates"]:
        print(f"  Plate {pl['id']:>2} {pl['name'][:28]:<28} slots {', '.join(map(str, pl['slots']))}")
    print()
    for r in rows:
        head = f"Slot {r['slot']:>2} {sw(r['hex'], color)}{r['hex']}  {r['type']}  {r['profile']}"
        if r.get("measured"):
            mid, mhex, msh = r["measured"]
            head += f" -> {sw(mhex, color)}{mhex} measured (filamentcolors #{mid}, ΔE {msh})"
        print(head + f"   [plates {', '.join(r['used_on'])}]")
        if not r["pick"]:
            print("    no inventory candidates\n")
            continue
        d, k = r["pick"]
        sp = spools[k]
        best_d, best_k = r["ranked"][0]
        print(f"    {grade(d, threshold)}{d:5.1f}  {sw(sp['hex'], color)}{sp['hex']}  {spool_label(sp)}"
              f"  [{', '.join(sorted(sp['locations']))}]{finish_note(sp)}")
        if best_k != k:
            print(f"          (nearest was {spools[best_k]['color']} ΔE {best_d:.1f}, "
                  f"but it's assigned to another slot)")
        alts = [f"{spools[j]['brand']} {spools[j]['finish']} {spools[j]['color']} {dd:.1f}" for dd, j in r["ranked"] if j != k][:2]
        if alts:
            print(f"          alt: {' | '.join(alts)}")
        for dd, s in r.get("suggest", []):
            print(f"    buy {dd:5.1f}  {sw(s['hex'], color)}{s['hex']}  {s['brand']} {s['type']} {s['color']}"
                  f"  https://filamentcolors.xyz/swatch/{s['id']}/")
        print()


def write_html(path, project, rows, spools, threshold):
    e = html.escape
    chip = lambda hx: f'<span class="c" style="background:{hx}"></span>'
    out = [f"""<!doctype html><meta charset="utf-8"><title>{e(project['file'])} spool match</title>
<style>body{{font:14px system-ui,sans-serif;margin:24px;color:#222}}table{{border-collapse:collapse}}
td,th{{padding:6px 10px;border-bottom:1px solid #ddd;text-align:left;vertical-align:middle}}
.c{{display:inline-block;width:56px;margin-right:2px;height:36px;border-radius:4px;border:1px solid #0002;vertical-align:middle}}
.ok{{color:#1a7f37;font-weight:600}}.mid{{color:#9a6700;font-weight:600}}.bad{{color:#cf222e;font-weight:600}}
small{{color:#666}}td:nth-child(3){{white-space:nowrap}}</style><h2>{e(project['file'])}</h2><p><small>ΔE threshold {threshold}</small></p>
<table><tr><th>Slot</th><th>Requested</th><th>Wanted · Yours</th><th>Your spool</th><th>ΔE</th><th>Buy options</th></tr>"""]
    for r in rows:
        tgt_hex = r["measured"][1] if r.get("measured") else r["hex"]
        if r["pick"]:
            d, k = r["pick"]; sp = spools[k]
            rd = round(d, 1)
            cls = "ok" if rd <= threshold else ("mid" if rd <= 2 * threshold else "bad")
            mine = f"{chip(tgt_hex)}{chip(sp['hex'])}</td><td>{e(spool_label(sp))}{e(finish_note(sp))}<br><small>{sp['hex']} · {e(', '.join(sorted(sp['locations'])))}</small>"
            dcell = f'<span class="{cls}">{d:.1f}</span>'
        else:
            mine, dcell = f"{chip(tgt_hex)}</td><td>-", "-"
        req = f"{r['hex']}<br>{e(r['profile'])}"
        if r.get("measured"):
            mid, mhex, msh = r["measured"]
            req += f'<br>measured <a href="https://filamentcolors.xyz/swatch/{mid}/">{mhex}</a> (ΔE {msh})'
        buys = "<br>".join(
            f'{chip(s["hex"])} <a href="https://filamentcolors.xyz/swatch/{s["id"]}/">{e(s["brand"])} {e(s["color"])}</a> <small>{dd:.1f}</small>'
            for dd, s in r.get("suggest", []))
        out.append(f"<tr><td>{r['slot']}<br><small>plates {e(', '.join(r['used_on']))}</small></td>"
                   f"<td><small>{req}</small></td>"
                   f"<td>{mine}</td><td>{dcell}</td><td>{buys}</td></tr>")
    out.append("</table>")
    Path(path).write_text("\n".join(out), encoding="utf-8")

# --------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Match 3MF filament colors to your spools (CIEDE2000).")
    ap.add_argument("project", help=".3mf file")
    ap.add_argument("--spools", default="my-spools.json",
                    help="3DFilamentProfiles export (.json or .csv; default: my-spools.json)")
    ap.add_argument("--threshold", type=float, default=5.0, help="ΔE you consider a match (default 5)")
    ap.add_argument("--top", type=int, default=3, help="inventory candidates to consider/show per slot")
    ap.add_argument("--any-material", action="store_true", help="don't restrict to the slot's material family")
    ap.add_argument("--allow-reuse", action="store_true", help="let two slots map to the same spool")
    ap.add_argument("--exclude", default="Translucent,Glow,Silk",
                    help="comma list of finishes to ignore (default: Translucent,Glow,Silk; '' for none)")
    ap.add_argument("--min-grams", type=float, default=1, help="skip spools with less remaining")
    ap.add_argument("--suggest", action="store_true", help="suggest filaments to buy for slots above threshold")
    ap.add_argument("--brands", default="", help="limit suggestions, e.g. polymaker,bambu")
    ap.add_argument("--no-measured", dest="measured", action="store_false",
                    help="match the 3MF's requested hex as-is instead of filamentcolors.xyz measured color")
    ap.add_argument("--measured-tolerance", type=float, default=3.0,
                    help="max ΔE from the requested hex to adopt a measured swatch as the target (default 3)")
    ap.add_argument("--refresh", action="store_true", help="re-download the filamentcolors.xyz cache")
    ap.add_argument("--html", help="also write a visual HTML report")
    ap.add_argument("--no-color", action="store_true", help="no terminal color chips")
    a = ap.parse_args()

    if not Path(a.spools).exists():
        sys.exit(f"Spool inventory not found: {a.spools}  (pass --spools <file>)")
    project = read_project(a.project)
    spools, skipped = load_spools(a.spools, a.min_grams, a.exclude.split(","))

    swatches = None
    if a.measured or a.suggest:
        try:
            swatches = load_filamentcolors(a.refresh)
        except (urllib.error.URLError, OSError) as ex:
            print(f"(filamentcolors.xyz unavailable: {ex})", file=sys.stderr)
    if a.measured and swatches:
        used = sum(1 for s in project["slots"] if s["used_on"] and s["hex"])
        n = apply_measured_targets(project, swatches, a.measured_tolerance)
        print(f"Measured target color from filamentcolors.xyz for {n}/{used} used slots "
              f"(requested hex for the rest).", file=sys.stderr)

    rows = match(project, spools, a.any_material, max(a.top, 3), a.allow_reuse)
    if a.suggest and swatches:
        suggest(rows, swatches, a.brands.split(","), 3, a.threshold,
                a.any_material, exclude=a.exclude.split(","))
    color = not a.no_color and sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
    print_report(project, rows, spools, a.threshold, color, skipped)
    if a.html:
        write_html(a.html, project, rows, spools, a.threshold)
        print(f"HTML report: {a.html}")


if __name__ == "__main__":
    main()
