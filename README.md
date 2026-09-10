# filmatch

Match the filament colors in a 3MF project to the spools you already own, using
perceptual color distance (CIEDE2000, ΔE). Point it at a sliced project and it
tells you, slot by slot, which spool on your shelf is the closest match, how
good that match is, and — optionally — what to buy when nothing you own is close
enough.

Single file, Python standard library only, Python 3.8+.

## What it does

- **Reads the project.** Bambu Studio / OrcaSlicer 3MFs (per-plate filament
  usage, including MMU-painted regions) and PrusaSlicer 3MFs (slot colors plus
  which slots are actually used).
- **Reads your inventory.** A [3DFilamentProfiles](https://spooldb.com) *Export
  My Spools* file, JSON or CSV. See [`my-spools.example.json`](my-spools.example.json).
- **Assigns spools to slots.** Each used slot is matched to its nearest spool by
  ΔE. By default one spool is not assigned to two slots (a Hungarian assignment
  minimizes total squared ΔE); pass `--allow-reuse` to drop that constraint.
  Matches are restricted to the slot's material family (PLA to PLA, PETG to
  PETG) unless `--any-material`.
- **Refines the target color.** Each slot's requested hex from the 3MF is often
  the vendor's marketing RGB. When the same filament (same vendor, same material
  family, nearest color within `--measured-tolerance` ΔE) exists on
  [filamentcolors.xyz](https://filamentcolors.xyz), its spectrophotometer-measured
  color is used as the match target instead. `--no-measured` disables this.
- **Suggests purchases.** With `--suggest`, any slot whose best owned spool is
  worse than `--threshold` gets the three closest swatches from the
  filamentcolors.xyz library, each with a link. `--brands` narrows the
  suggestions to given makers.
- **Reports.** Colorized terminal output, and an optional standalone HTML report
  with color chips (`--html report.html`).

The filamentcolors.xyz swatch library is downloaded once and cached for ~30 days
under `$XDG_CACHE_HOME/filmatch/` (`~/.cache/filmatch/` by default). `--refresh`
forces a re-download.

## Install

```sh
git clone https://github.com/liutang/filmatch.git
cd filmatch
```

No dependencies. Optionally put it on your PATH:

```sh
chmod +x filmatch.py
ln -s "$PWD/filmatch.py" ~/.local/bin/filmatch
```

## Usage

```sh
python3 filmatch.py Project.3mf                       # --spools defaults to my-spools.json
python3 filmatch.py Project.3mf --suggest --brands polymaker,bambu
python3 filmatch.py Project.3mf --spools other.json --html report.html
```

### Options

| Flag | Default | Meaning |
| --- | --- | --- |
| `project` | — | the `.3mf` file (required) |
| `--spools FILE` | `my-spools.json` | 3DFilamentProfiles export, `.json` or `.csv` |
| `--threshold N` | `5.0` | ΔE at or below which a match is "OK" |
| `--top N` | `3` | inventory candidates to weigh / show per slot |
| `--any-material` | off | don't restrict matches to the slot's material family |
| `--allow-reuse` | off | let two slots map to the same spool |
| `--exclude LIST` | `Translucent,Glow,Silk` | comma list of finishes to ignore (`''` for none) |
| `--min-grams N` | `1` | skip spools with less remaining |
| `--suggest` | off | list filaments to buy for slots above threshold |
| `--brands LIST` | — | limit suggestions to these makers, e.g. `polymaker,bambu` |
| `--no-measured` | off | match the 3MF's requested hex as-is (skip filamentcolors.xyz lookup) |
| `--measured-tolerance N` | `3.0` | max ΔE from the requested hex to adopt a measured swatch |
| `--refresh` | off | re-download the filamentcolors.xyz cache |
| `--html FILE` | — | also write a visual HTML report |
| `--no-color` | off | no ANSI color chips in the terminal (also respects `NO_COLOR`) |

## Reading the output

```
Slot  9 #BB3D43  PLA  Bambu PLA Matte -> #C13A3D measured (filamentcolors #1952, ΔE 2.0)   [plates 6]
    OK   3.6  #BB2C2E  ELEGOO PLA Matte Ruby Red  [Storage]
          alt: Printed Solid Glitter Blood Red Light Glitter 4.7 | Polymaker Panchroma Matte Pastel Watermelon 10.4
```

- **Slot line** — the slot number, its requested hex, material, and slicer
  profile. `-> #… measured` appears when the target was refined to a
  filamentcolors.xyz measurement.
- **Match line** — a grade (`OK` ≤ threshold, `~` ≤ 2×threshold, `X` worse), the
  ΔE, the spool's hex, its label, and where it's stored.
- **alt** — the next-closest spools that were not assigned to this slot.
- **buy** (with `--suggest`) — closest filaments to purchase, with links.

## Color math

sRGB → linear → CIE XYZ (D65) → CIELAB, then CIEDE2000 for every color distance.
Rough guide: ΔE < 1 is imperceptible, 1–2 is a close match, 2–5 is noticeable
but often acceptable, > 5 is an obvious difference.

## License

MIT — see [LICENSE](LICENSE).
