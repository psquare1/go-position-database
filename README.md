# Go Position DB

Go Position Database is a PySide6 desktop application for storing, editing, and
searching Go study positions. Entries may contain images, SGFs, variations,
descriptions, scores, metadata, and hierarchical tags.

## Features

- Image- and SGF-based primary positions and variations.
- Native SGF board editing and variation navigation.
- Free-form descriptions, scores, structured metadata, and hierarchical tags.
- Boolean tag searches such as `joseki AND NOT ko`, including inherited tags.
- Optional analysis through a locally installed KataGo engine.
- Folder-based collection storage that can be inspected or versioned separately
  from the application.

## Quick start on Windows

Install [Python 3](https://www.python.org/downloads/) and enable the installer
option that adds Python to `PATH`.

1. Download the repository using **Code → Download ZIP**, or clone it:

   ```powershell
   git clone <repository-url>
   cd <repository-folder>
   ```

2. Double-click `launch_gui.bat`.

The launcher creates a project-local Python environment, installs the required
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

Then install and launch:

```powershell
python -m pip install -r requirements-gui.txt
python go_db_gui.py
```

The application uses the repository folder as its collection by default. A
different collection folder can be selected with the `root` setting in
`config.yaml`.

## Core workflow

The app is organized around **Browse entries**, **New entry**, and
**Manage tags**.

### Create and edit entries

Each entry contains one primary position and any number of variations. A primary
position can use an SGF, an image, or both.

- SGFs can be loaded from a file or created as a blank 19×19 game.
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
variation can use any node in the SGF as its starting view.

An SGF describes an entire game tree, so the file alone does not identify which
node is the position relevant to an entry or variation. Navigate the native board
to that position, then choose **Set starting view → Set current SGF node**. The
selected node is stored with that primary position or variation and is displayed
first when it is opened again; the rest of the SGF remains available for context
and navigation.

### Keyboard shortcuts

Shortcuts apply in the entry editor. Standard editing behavior takes precedence
while a text, metadata, or tag field has focus.

| Shortcut | Action |
| --- | --- |
| `Ctrl+V` | Paste a clipboard image or SGF; confirm before replacing an existing one |
| `Ctrl+C` | Copy the currently displayed image or SGF |
| `Ctrl+Left` / `Ctrl+Right` | Select the previous or next primary/variation view |
| `Alt+D` | Switch between the selected image and SGF view |
| `Ctrl+R` | Return the board to the saved starting node |
| `Ctrl+S` | Set the current SGF node as the selected starting view |
| `Space` | Toggle KataGo analysis |
| `Backspace` / `Delete` | Delete the current SGF node after confirmation |
| `Shift+A` | Select alternating-move editing |
| `Shift+B` / `Shift+W` | Select black or white setup stones |
| `Shift+E` | Select the eraser |
| `Shift+P` | Play a pass |
| `Ctrl+Shift+1` | Select numbered annotations |
| `Ctrl+Shift+A` | Select letter annotations |
| `Ctrl+Shift+T` | Select triangle annotations |
| `Ctrl+Shift+C` | Select circle annotations |
| `Ctrl+Shift+S` | Select square annotations |
| `Ctrl+Shift+X` | Select cross annotations |

### AI analysis with KataGo

The application supports analysis through a local KataGo installation. KataGo,
its neural-network model, and its analysis configuration are not bundled or
downloaded by the application. Their paths are configured and validated on the
**KataGo settings** page.

The **AI** control below the native board toggles analysis for the displayed SGF
position. It remains enabled while navigating within the same SGF and is disabled
when leaving the entry. Suggested moves are drawn on the board, while score, win
rate, and visit count are shown in the right panel. Analysis of a primary starting
position also updates its stored score automatically.

Configurable options include startup and response timeouts, reporting frequency,
the number and point-loss range of board suggestions, root policy temperature,
analysis and search thread counts, and neural-network cache size. KataGo runs as
a managed background process and is stopped when the application closes.

### Image-to-SGF conversion

Image recognition is included with the desktop application. The conversion
dialog is adapted from
[LizGoban's SGF from Image](https://github.com/kaorahi/lizgoban/tree/master/src/sgf_from_image),
a semi-automatic GPLv3 converter. The app loads a pinned copy locally in Qt
WebEngine. The converter is primarily intended for clean screenshots. Photographs,
perspective distortion, partial boards, unusual themes, glare, and overlays may
require additional manual calibration.

The dialog identifies the board grid, displays the detected stones, and allows
corrections before producing the SGF. For the selected primary position or
variation, use **Set starting view → Set SGF → From selected image** to convert
its current image, or **From clipboard** to convert an image from the clipboard.

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

Application settings are stored in `config.yaml` beside the application code.
They include collection directory and filename settings as well as KataGo
configuration, including the selected collection root.

The repository ignores its default collection files, so a fresh clone starts
empty. Keeping a collection in a separate folder allows application updates and
private collection history to remain independent.

## License

Go Position Database is free software licensed under the GNU General Public
License, version 3 or (at your option) any later version
(`GPL-3.0-or-later`). See the [license](LICENSE).

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
- additional KataGo analysis visualizations.

Pull requests are welcome.
