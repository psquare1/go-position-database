# Go Position DB

Go Position Database is a desktop library for collecting, studying, organizing,
and searching Go positions. SGF is the preferred format when available because
it preserves an editable game tree, while images remain fully supported for
screenshots, diagrams, and sources without an SGF. Either form—or both together—
can be combined with descriptions, scores, metadata, solutions, and hierarchical
tags so that useful positions remain easy to find.

The PySide6 desktop app is the primary interface. It includes a visual browser,
a position and SGF editor, and a tag manager.

## What you can do

- Build a personal collection from existing SGFs, new blank SGFs, screenshots,
  or pasted board images.
- View and edit SGFs on a scalable native Go board, including moves, branches,
  setup stones, passes, and common point annotations.
- Add image-based or SGF-based solutions and variations, each with its own
  description and score.
- Describe positions with free-form notes, scores, structured metadata, and any
  number of tags.
- Organize tags into parent/child hierarchies. A position tagged
  `3-3-joseki`, for example, also appears in a search for `joseki`.
- Search with Boolean expressions such as `joseki AND NOT ko`.
- Keep the collection in ordinary folders that can be inspected, synchronized,
  backed up, or versioned independently of the app.

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

The app is organized around **Browse positions**, **New position**, and
**Manage tags**.

### Create and edit positions

A main position can start from an SGF, an image, or both. SGFs provide the
richest editing and study experience, while images are useful when only a visual
reference is available:

- SGFs can be loaded from a file or created as a blank 19×19 game.
- Images can be chosen from a file or pasted from the clipboard.
- When both forms exist, the position can be viewed as either the image or the
  native SGF board.

The editor also stores a description, score, metadata, and tags. Changes save
automatically. Open any result from the browser to return to the same editor.

### Work with SGFs

The native board can navigate and edit the SGF game tree. It supports ordinary
moves and passes, setup stones, erasing stones, branches, and numbered, letter,
triangle, circle, square, and cross annotations. A position can use any node in
the SGF as its starting view, which is useful when the full game provides context
but only a later position is relevant.

### Add solutions and variations

A position can have multiple solutions. SGF board views are especially useful
for interactive variations, while images can preserve external diagrams or
analysis. A solution may be:

- an image loaded from a file or pasted from the clipboard; or
- a board view backed by the position's SGF.

A new board solution starts at the SGF node currently being viewed, or at the
root when no board node is active. Its starting node can be changed later. This
makes it possible to present several branches of one SGF as separate named
solutions without duplicating the game file.

The main position and every solution can carry its own description and score.

### Organize with tags

Tags provide the main vocabulary for classifying the collection. They can have
descriptions and multiple parents, allowing broad categories and more specific
subcategories to coexist. Cycles are rejected, and redundant ancestor tags are
cleaned up automatically when tags or their hierarchy change.

For example, a position explicitly tagged `3-3-joseki` can be found through
either `3-3-joseki` or its parent `joseki`.

### Browse and search

Leave the query empty to browse the whole collection, or combine tags with
`AND`, `OR`, `NOT`, and parentheses:

```text
joseki
joseki AND reverse-sente
(joseki OR tesuji) AND NOT ko
```

Search includes tag inheritance, so a query for a parent tag also finds
positions using its descendants. Results can be viewed as a compact gallery or
with progressively more descriptive context.

## How the collection is stored

Each position has its own folder containing its media and YAML record. Its SGF
can supply the main board and any number of solution board views, with images
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

Only the files actually used by a position need to be present. The generated tag
index can always be rebuilt from the position records and `tags.yaml`.

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
overwrites the starter configuration and tag files, but does not erase position
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

Verbose and structured output include descriptions, scores, metadata, solution
records, and media paths where applicable.

### Validate and maintain the collection

```powershell
python go_db.py --root . check
python go_db.py --root . rebuild-index
python go_db.py --root . clean --dry-run
python go_db.py --root . clean
python go_db.py --root . clean p000001 p000002
```

`check` reports inconsistent position records, media, tags, hierarchy links, or
the generated index. `rebuild-index` regenerates the search index from the
canonical records. `clean` normalizes uniquely identifiable image and SGF
filenames; use `--dry-run` to preview the changes.

### Create and edit positions

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
be repeated. `position` can be shortened to `pos`.

Metadata supports nested keys and values parsed as JSON when possible, so
numbers, booleans, arrays, and objects retain their types:

```powershell
python go_db.py --root . position meta-set p000001 source "study notes"
python go_db.py --root . position meta-set p000001 source.page 127
python go_db.py --root . position meta-show p000001
python go_db.py --root . position meta-show p000001 source.page
python go_db.py --root . position meta-delete p000001 source.page
```

The CLI reads and preserves scores and solution records, but those fields are
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

`tag` can also be written as `tags`. Removing a tag is blocked while positions
refer to it directly. `--force` permits removal from child parent-lists; it does
not discard position references.

Run `python go_db.py --help`, or add `--help` after a command or subcommand, for
the complete accepted syntax.

## Transparency

This project was heavily **vibe-coded with AI assistance**. Back up an important
collection before large imports or bulk file operations.

## Future updates

Possible future directions include:

- a preset vocabulary of common Go tags;
- publishing, sharing, or merging collections; and
- integration with KataGo analysis.

Pull requests are welcome.
