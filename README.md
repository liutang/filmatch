# filmatch

Match the filament colors in a 3MF project to the spools you already own, using
perceptual color distance (CIEDE2000, ΔE). Point it at a sliced project and it
tells you, slot by slot, which spool on your shelf is the closest match, how
good that match is, and — optionally — what to buy when nothing you own is close
enough.

Python standard library only, Python 3.8+. The CLI is a single file; the
optional drag-and-drop web UI adds `web/index.html`.

## What it does

- **Reads the project.** Bambu Studio / OrcaSlicer 3MFs (per-plate filament
  usage, including MMU-painted regions) and PrusaSlicer 3MFs (slot colors plus
  which slots are actually used).
- **Reads your inventory.** A [3DFilamentProfiles](https://spooldb.com) *Export
  My Spools* file, JSON or CSV (see [`my-spools.example.json`](my-spools.example.json)),
  or your [Spoolman](https://github.com/Donkie/Spoolman) server. See
  [Spoolman](#spoolman) below.
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
python3 filmatch.py Project.3mf --spoolman http://spoolman.local:7912
```

### Options

| Flag | Default | Meaning |
| --- | --- | --- |
| `project` | — | the `.3mf` file (required) |
| `--spools FILE` | `my-spools.json` | 3DFilamentProfiles export, `.json` or `.csv` |
| `--spoolman URL` | `$SPOOLMAN_URL` | read spools from a Spoolman server instead of `--spools`; `''` turns it off |
| `--threshold N` | `5.0` | ΔE at or below which a match is "OK" |
| `--top N` | `2` | alternate spools to show per slot, in addition to the pick |
| `--any-material` | off | don't restrict matches to the slot's material family |
| `--allow-reuse` | off | let two slots map to the same spool |
| `--include LIST` | — | specialty finishes to allow back in (silk, glitter, wood, …); every one is skipped by default. `all` keeps them all |
| `--min-grams N` | `1` | skip spools with less remaining (spool exports only) |
| `--suggest` | off | list filaments to buy for slots above threshold |
| `--brands LIST` | — | limit suggestions to these makers, e.g. `polymaker,bambu` |
| `--no-measured` | off | match the 3MF's requested hex as-is (skip filamentcolors.xyz lookup) |
| `--measured-tolerance N` | `3.0` | max ΔE from the requested hex to adopt a measured swatch |
| `--refresh` | off | re-download the filamentcolors.xyz cache |
| `--html FILE` | — | also write a visual HTML report |
| `--no-color` | off | no ANSI color chips in the terminal (also respects `NO_COLOR`) |
| `--serve` | off | run the drag-and-drop web UI on localhost (see below) |
| `--port N` | `8765` | port for `--serve` (falls back to a free port if taken) |
| `--no-browser` | off | with `--serve`, don't open a browser tab |

## Spoolman

Point filmatch at a Spoolman server with `--spoolman URL` or the
`SPOOLMAN_URL` environment variable. The address works with or without
`/api/v1`. filmatch reads the spool list on each run, and the web UI reuses it
for 30 seconds, so changes you make in Spoolman show up without a restart.
Spoolman doesn't need a CORS setting for this, because the request comes
from filmatch's server rather than from the browser.

filmatch uses only Spoolman's standard fields, not custom extra fields, so it
works with any Spoolman install:

| filmatch | Spoolman |
| --- | --- |
| brand | filament vendor |
| material | filament material |
| color and label | filament name |
| hex | filament color (single-color filaments only) |
| location | spool location |

- **Only single-color filaments are matched.** Spools of multi-color filaments,
  and spools with no color set, are skipped and counted as skipped in the report.
- **Remaining weight is ignored.** filmatch matches colors, and every spool
  Spoolman lists counts. Archived spools are left out by Spoolman itself.
  `--min-grams` applies to spool exports only.
- **Finishes are guessed from the filament name.** Spoolman has no finish
  field, so a specialty finish is recognized from a word in the name: "Silk
  Gold" is silk, "Glitter Galaxy Black" is glitter and galaxy. A name that
  says *basic* or *matte* counts as a plain finish whatever else it says, so
  "Basic Wood Colour" and "Matte Wood Brown" are matched normally.

> **This can give false positives.** It's a technical limitation: Spoolman
> doesn't store a finish, so a color name that happens to contain a finish
> word, such as "Wood Brown" or "Galaxy Black", is taken as that finish and
> skipped. If a spool you expected is missing, include that finish
> (`--include wood`, or its chip in the web UI). The report also notes when
> finishes were guessed.

## Web UI

```sh
filmatch --serve                 # or: python3 filmatch.py --serve
```

Starts a local server on `127.0.0.1` and opens the page in your browser. Drop a
`.3mf` anywhere on the window (or click the drop area) and the report appears
below it. Every option is a switch, or a slider paired with a number box, so you
can drag a value or type an exact one. The report re-renders as you change them,
so you can sweep the ΔE threshold and watch grades change. You can
also drop your spool export (`.json` / `.csv`) to swap inventories; by default
the server uses `--spoolman` if set, otherwise `--spools` from where you started
it. The inventory's name is a link: it opens Spoolman, or the loaded file, in a
new tab. On Spoolman, **Replace…** asks for another Spoolman address, which is
used once it answers; a spool file can still be dropped on the window. After you
drop a file, **Use Spoolman** switches back to the server.
The min grams slider is hidden while Spoolman is the inventory. **Download HTML report**
saves the same standalone file `--html` writes. **Reload filamentcolors.xyz
library**, at the bottom of the options, downloads the swatch library again
(like `--refresh`) and shows when the cached copy was downloaded.

The hex in the **Requested** column is an editable box: type a different color
and press Enter to re-match that slot against it, or click the wanted swatch to
choose one from a color picker. An edited slot is matched to
exactly what you typed (no measured-color lookup); **reset** next to the box
returns to the 3MF's color, and loading another project clears all edits.

Flags passed with `--serve` become the page's defaults (for example,
`filmatch --serve --suggest --threshold 3`), and a project path preloads it:
`filmatch Project.3mf --serve`. Your slider settings are remembered in the
browser; **Reset to defaults** returns to the server's.

The analysis runs in the same Python code as the CLI, not in the browser, so
the two always agree. filamentcolors.xyz sits behind Cloudflare and sends no
CORS headers, so a page running only in the browser couldn't query it anyway.

## Self-hosting with Docker

The image is standard library only, so there is nothing to install in it. Every
push to `main` publishes a multi-arch image (amd64 + arm64, so it runs on a NAS,
a mini PC or a Pi) to GitHub Container Registry:

```sh
mkdir -p data && cp my-spools.json data/
docker run -d --name filmatch -p 8765:8765 -v "$PWD/data:/data" \
  ghcr.io/liutang/filmatch:latest
```

The package is public, so the pull needs no login. (GHCR often creates new
packages private; if a future package of yours lands private, flip it at the
package page → *Package settings* → *Danger Zone* → *Change visibility*, or pull
with `docker login ghcr.io -u <you>` using a token with `read:packages`.)

Or with compose / Portainer — paste this as a stack, no paths to edit:

```yaml
services:
  filmatch:
    image: ghcr.io/liutang/filmatch:latest
    container_name: filmatch
    restart: unless-stopped
    ports: ["8765:8765"]
    volumes: ["filmatch-data:/data"]
volumes:
  filmatch-data:
```

**Don't give a Portainer stack a `build:` section.** A stack has no build context,
and the build fails with a BuildKit error like `failed to list workers ... frame
too large`. Pull the published image instead, as above.

Getting your inventory into a named volume, once:

```sh
docker cp my-spools.json filmatch:/data/my-spools.json && docker restart filmatch
```

Dropping a `.json`/`.csv` on the page also works, but only for the running
container. For a file you edit on the host, swap the named volume for an
absolute bind mount (`/volume1/docker/filmatch:/data`) owned by uid 1000.

To build from a clone instead:

```sh
mkdir -p data && cp my-spools.json data/
# edit docker-compose.yml: uncomment `build: .`, drop `image:`, use ./data:/data
docker compose up -d
```

`./data` is the only volume:

| Path | What it holds |
| --- | --- |
| `data/my-spools.json` | your inventory, loaded at startup |
| `data/cache/` | the filamentcolors.xyz swatch library, so a restart doesn't re-download it |

To change inventory permanently, replace `data/my-spools.json` and restart.
Dropping a `.json`/`.csv` on the page still works, but it applies to the running
container only and is lost on restart, so the mounted file stays the source of
truth.

To use Spoolman instead of a spool file, set `SPOOLMAN_URL`. It takes over from
the image's `--spools /data/my-spools.json`:

```yaml
    environment:
      SPOOLMAN_URL: http://spoolman:7912
```

Extra flags are appended to the entrypoint, and a later flag wins, so compose can
override defaults without repeating the command:

```yaml
command: ["--suggest", "--include", "silk"]
```

Without compose:

```sh
docker build -t filmatch .
docker run -d --name filmatch -p 8765:8765 -v "$PWD/data:/data" filmatch
```

The image runs as uid 1000 and needs to write `data/cache`; if your host
directory is owned by someone else, set `user:` in compose (or `chown` the
directory) or the swatch cache can't be written.

**There is no authentication.** Anyone who can reach the port can upload a
project, read your inventory, make the server fetch from filamentcolors.xyz,
and, when Spoolman is set up, point it at another Spoolman address.
Keep it on your LAN or behind a reverse proxy / VPN that does the auth. Related:
the server holds one project and one inventory at a time, so two people using it
at once will overwrite each other's uploads — it's built for one user.

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

## Development

A pre-commit hook guards against accidentally committing a gitignored file
(e.g. `git add -f my-spools.json`). Enable it once per clone:

```sh
git config core.hooksPath githooks
```

It refuses the commit and lists the offending path(s); override deliberately
with `git commit --no-verify`.

## License

MIT — see [LICENSE](LICENSE).
