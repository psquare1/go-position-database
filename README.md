# Go Position DB

Go Position Database is a desktop library for organizing Go study entries using
hierarchical tags, efficiently searching for entries based on tags, and
analyzing their primary positions and variations in detail.

The PySide6 desktop app is the primary interface. It includes a visual browser,
an entry editor with SGF tools, and a tag manager.

## What you can do

- Build a personal collection of entries from existing SGFs or screenshots of
  Go positions.
- Add image-based or SGF-based variations of each entry's primary position,
  each with its own annotations and commentary.
- Describe primary positions and variations with free-form notes, scores,
  structured metadata, and any number of tags.
- Organize tags into parent/child hierarchies. An entry tagged
  `3-3-joseki`, for example, also appears in a search for `joseki`.
- Search with Boolean expressions such as `joseki AND NOT ko`.
- Keep the collection in ordinary folders that can be inspected, synchronized,
  or versioned with Git independently of the app.

## Quick start on Windows

Install [Python 3](https://www.python.org/downloads/) and enable the installer
option that adds Python to `PATH`.

1. Download the repository using **Code → Download ZIP**, or clone it:

   ```powershell
   git clone <repository-url>
   cd <repository-folder>
   ```

2. Double-click `launch_gui.bat`.

The launcher creates a private Python environment, installs the required
packages, initializes an empty collection on first launch, and opens the app.
Later launches reuse the same environment and collection.

### Manual installation

For a manual or non-Windows setup:

```powershell
python -m venv .venv
```

Activate the environment on Windows:

```powershell
.\.venv\Scripts\Activate.ps1
```

On macOS or Linux:

```bash
source .venv/bin/activate
```

Then install, initialize, and launch:

```powershell
python -m pip install -r requirements-gui.txt
python go_db.py --root . init
python go_db.py --root . gui
```

`--root` selects the folder containing the collection. Omit it to use the
repository folder, or point it to a separate folder to keep personal data apart
from the application code.

## Core workflow

The app is organized around **Browse entries**, **New entry**, and
**Manage tags**.

### Create and edit entries

Each entry contains one primary position and any number of variations. A primary
position can start from an SGF, an image, or both. The app can also create an SGF
from an existing image using its built-in LizGoban-based converter.

- SGFs can be loaded from a file, created as a blank 19×19 game, or created from
  the current image.
- Images can be chosen from a file or pasted from the clipboard.
- When both forms exist, either the image or the native SGF board can be used as
  the starting view.

The editor also stores descriptions, scores, metadata, and tags. Changes are
saved automatically. Open any result from the browser to return to the editor.

Each entry can have any number of variations. Each variation is associated with
its own image or a node in the entry's shared SGF tree.

The primary position and every variation can have its own description and score.

### Work with SGFs

The native board can navigate and edit the SGF game tree. It supports ordinary
moves and passes, adding and erasing setup stones, branches, and number, letter,
triangle, circle, square, and cross annotations. The primary position and each
variation can use any node in the SGF as its starting view. This is useful when
the full game provides context but only a later board position is relevant.

### Built-in image-to-SGF conversion

Image recognition is included with the desktop application. The conversion
dialog is adapted from
[LizGoban's SGF from Image](https://github.com/kaorahi/lizgoban/tree/master/src/sgf_from_image),
a semi-automatic GPLv3 converter. The app loads a pinned copy locally in Qt
WebEngine. The converter is primarily intended for clean screenshots. Photographs,
perspective distortion, partial boards, unusual themes, glare, and overlays may
require additional manual calibration.

The dialog guides you through identifying the board grid, shows all detected
stones, and lets you correct misidentified intersections.

Use **Set starting view → Set image** to choose an image from a file or the
clipboard for the selected primary position or variation.

Use **Set starting view → Set SGF** to choose the SGF shared by the entry. You can
load an SGF file, create a blank SGF, or convert the selected image or clipboard
image to an SGF.

### Organize with tags

Tags provide the main vocabulary for classifying the collection. They can have
descriptions and can be arranged in a customizable hierarchy. A tag can have
multiple parents.

The app automatically removes redundant ancestor tags from entries.

### Browse and search

Leave the query empty to browse the whole collection, or combine tags with
`AND`, `OR`, `NOT`, and parentheses:

```text
joseki
joseki AND reverse-sente
(joseki OR tesuji) AND NOT ko
```

Search includes tag inheritance, so a query for a parent tag also finds entries
using its descendants. For example, an entry explicitly tagged `3-3-joseki` can
be found through either `3-3-joseki` or its parent `joseki`.

Results can be viewed as a compact gallery or expanded, with more context.

## How the collection is stored

Each entry has its own folder containing its media and YAML record. Its SGF can
supply the primary position and any number of variation views, with images
stored alongside it when used:

```text
positions/
  p000001/
    position.png
    position.sgf
    metadata.yaml
    solutions/
      solution-001.png
tags.yaml
generated/
  tag_index.yaml
```

Only the files actually used by an entry need to be present. The generated tag
index can always be rebuilt from the entry records and `tags.yaml`.

The on-disk names `positions`, `position.*`, `solutions`, and `solution_images`
are retained for compatibility with existing collections. In the app and this
guide they correspond to entries, primary positions, and variations.

The collection paths and canonical filenames can be changed in `config.yaml`:

```yaml
positions_directory: positions
tags_file: tags.yaml
generated_index: generated/tag_index.yaml

files:
  sgf: position.sgf
  image: position.png
  metadata: metadata.yaml
```

The repository ignores its default collection files, so a fresh clone starts
empty. To version a personal collection, the safest arrangement is a separate
folder and Git repository launched with:

```powershell
python go_db.py --root <database-folder> gui
```

This keeps application updates and private collection history independent.

## Command-line tools

The desktop app is recommended for everyday use, while the CLI supports
scripting, inspection, validation, and bulk maintenance. Global options appear
before the command:

```text
--root PATH       use a particular database folder
--config PATH     use a particular config.yaml
```

### Initialize and launch

```powershell
python go_db.py --root . init
python go_db.py --root . init --force
python go_db.py --root . gui
```

`init` creates the collection folders and starter configuration. `init --force`
overwrites the starter configuration and tag files, but does not erase entry
folders.

### Search and inspect

```powershell
python go_db.py --root . search "joseki AND NOT ko"
python go_db.py --root . search "joseki" --verbose
python go_db.py --root . search "joseki" --json
python go_db.py --root . search "joseki" --limit 20

python go_db.py --root . position show p000001
python go_db.py --root . position show p000001 --json
```

Verbose and structured output include descriptions, scores, metadata, variation
records, and media paths where applicable.

### Validate and maintain the collection

```powershell
python go_db.py --root . check
python go_db.py --root . rebuild-index
python go_db.py --root . clean --dry-run
python go_db.py --root . clean
python go_db.py --root . clean p000001 p000002
```

`check` reports inconsistencies in entry records, media, tags, hierarchy links,
or the generated index. `rebuild-index` regenerates the search index from the
canonical records. `clean` normalizes unambiguous image and SGF filenames; use
`--dry-run` to preview the changes.

### Create and edit entries

```powershell
python go_db.py --root . position create p000001 --sgf C:\path\position.sgf
python go_db.py --root . position create p000002 --image C:\path\board.png
python go_db.py --root . position create p000003 --image C:\path\board.png --sgf C:\path\game.sgf --description "Corner variation" --tag joseki --meta "move=38"

python go_db.py --root . position add-tag p000001 joseki reverse-sente
python go_db.py --root . position remove-tag p000001 reverse-sente
python go_db.py --root . position set-tags p000001 joseki tesuji
python go_db.py --root . position set-description p000001 "Black should connect first."
```

Creation requires an image, an SGF, or both. `--tag` and `--meta KEY=VALUE` can
be repeated. The legacy CLI command name `position` is retained for
compatibility and can be shortened to `pos`; it operates on entries.

Metadata supports nested keys and values parsed as JSON when possible, so
numbers, booleans, arrays, and objects retain their types:

```powershell
python go_db.py --root . position meta-set p000001 source "study notes"
python go_db.py --root . position meta-set p000001 source.page 127
python go_db.py --root . position meta-show p000001
python go_db.py --root . position meta-show p000001 source.page
python go_db.py --root . position meta-delete p000001 source.page
```

The CLI reads and preserves scores and variation records, but those fields are
currently edited through the desktop app.

### Manage tags

```powershell
python go_db.py --root . tag list
python go_db.py --root . tag list --tree
python go_db.py --root . tag add joseki --description "Established corner sequences"
python go_db.py --root . tag add 3-3-joseki --parent joseki
python go_db.py --root . tag add invasion-joseki --parent joseki --parent invasion
python go_db.py --root . tag add-parent 3-3-joseki invasion
python go_db.py --root . tag remove-parent 3-3-joseki invasion
python go_db.py --root . tag remove unused-tag
python go_db.py --root . tag remove unused-parent --force
```

`tag` can also be written as `tags`. Removing a tag is blocked while entries
refer to it directly. `--force` permits removal from child parent-lists; it does
not discard entry references.

Run `python go_db.py --help`, or add `--help` after a command or subcommand, for
the complete accepted syntax.

## License

Except where an included component states otherwise, Go Position Database is
free software licensed under the GNU General Public License, version 3 or (at
your option) any later version (`GPL-3.0-or-later`). See the [license](LICENSE).

The bundled image-to-SGF converter is adapted from
[LizGoban](https://github.com/kaorahi/lizgoban) at the pinned revision documented
in `go_position_db/assets/lizgoban_sgf_from_image/UPSTREAM.md`. Those adapted
files retain their upstream `GPL-3.0-only` terms and attribution; this is
compatible with distributing the combined application under GPLv3.

## Transparency

This project was heavily **vibe-coded with AI assistance**. Back up an important
collection before large imports or bulk file operations.

## Future updates

Possible future directions include:

- a preset vocabulary of common Go tags;
- publishing, sharing, or merging collections; and
- integration with KataGo analysis.

Pull requests are welcome.
