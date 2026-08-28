# Go Position DB

Go Position Database is a desktop library for collecting, annotating, organizing, 
and searching Go positions. Its flexible tagging and Boolean search make it easy 
to rediscover a specific position from only a vague memory, compare recurring 
shapes across different games, and recognize patterns in how similar positions are 
played.

The PySide6 app is the primary interface. It provides a visual browser for the
collection, a board-focused editor for individual positions, and a tag manager
for classifying positions using a customizable vocabulary.

## What you can do

- Build a personal database of Go positions from screenshots or other board images.
- Keep an optional SGF with each position.
- Open attached SGFs as native, scalable Go boards, step through moves, explore
  variations, and add standard point annotations directly on the board.
- Add a description, score, structured metadata, and any number of explicit tags.
- Attach multiple solution images or SGF-backed board instances to explain
  variations and alternative lines. Every solution can have its own description
  and score.
- Organize tags into parent/child hierarchies. A position tagged
  `3-3-joseki`, for example, can also appear in a search for its parent tag
  `joseki` without storing both tags on the position.
- Search with Boolean expressions such as `joseki AND NOT ko`, including `AND`,
  `OR`, `NOT`, parentheses, and autocomplete.
- Paste images directly from the clipboard when capturing a position is faster
  than saving it first.
- Keep the complete collection in ordinary folders that are easy to inspect,
  manually open SGFs with, copy, synchronize, or back up with Git.

## Quick start on Windows

You need [Python 3](https://www.python.org/downloads/) installed. When installing
Python, enable the option that adds it to `PATH`.

1. Download the repository from GitHub using **Code → Download ZIP**, or clone it:

   ```powershell
   git clone <repository-url>
   cd <repository-folder>
   ```

2. Double-click `launch_gui.bat`.

The launcher handles the rest. It creates a private `.venv`, installs PySide6 and
the other required packages, initializes an empty database on first launch, and
opens the app. Running the same file later checks the dependencies and launches
the existing collection; it does not overwrite it.

The downloaded repository contains no positions. Your `positions`, `tags.yaml`,
`config.yaml`, and generated index are created on your machine.

### Manual installation

For a manual or non-Windows setup, run these commands from the repository folder:

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

The `--root` option chooses where the position collection is stored. Omit it to
use the repository folder, or point it at a separate folder if you prefer to keep
your collection away from the application code.

## Using the desktop app

The top navigation separates the three main tasks: **Browse positions**,
**New position**, and **Manage tags**.

### Browse and search

Leave the query empty to see the whole collection, or combine tags in a Boolean
query:

```text
joseki
joseki AND reverse-sente
(joseki OR tesuji) AND NOT ko
NOT whole-board
```

Searches for a parent tag include positions using its descendants. Tag names
autocomplete inside complete Boolean expressions; press **Tab** to accept a
suggestion. Tags are normalized to lowercase with hyphens, so `Reverse Sente`
and `reverse_sente` become `reverse-sente`. `and`, `or`, and `not` are reserved
for queries and cannot be tag names.

Choose a result layout according to how much context you need:

- **Compact** shows images only, four per row.
- **Standard** shows images, filenames, and tags, three per row.
- **Detailed** adds the score, description, and metadata.

When a position has an attached SGF, its result shows a small read-only board
rendered at the stored start node instead of the main image.

Click a result's board or image, or double-click the result, to open the full
position editor.

### Create and edit positions

Choose **New position** to open a blank position in the same editor used for the
rest of the collection. You can then:

- choose, replace, or paste the main board image;
- attach, replace, or remove an SGF;
- view an attached SGF directly on the large board surface, move through its
  game tree, and inspect its labels and shape annotations;
- write a description;
- add tags;
- record metadata as descriptor/value pairs, such as source, game, move, player,
  difficulty, or review status;
- add, paste, replace, and remove solution images or board instances; and
- open the position folder when direct file access is useful.

The editor displays one large item at a time. Use the segmented **Main / S1 / S2**
selector to move between the main position and its solutions. The adjacent
**Set baseline** menu can attach an image from a file or the clipboard, store the
current SGF node, and choose whether an item with both forms displays its image
or its native SGF board. The visible description belongs to the selected item.

When the main position has an SGF, the editor uses that same large display area
for a native Go board instead of the static main image. The upper square contains
only the board or image; a divided rectangular toolbar sits below it. Use `<` and
`>` for single moves, the adjacent ten-move buttons for larger jumps, and `<<`
or `>>` for the ends of the selected line. Left/Right, Page Up/Page Down, Home,
and End provide the same keyboard navigation. The **V ↑** and **V ↓** buttons—or
the Up/Down keys—cycle through alternative branches. The stone and eraser tools
play moves or edit setup stones directly. To annotate the current SGF node,
select `1`, `A`, triangle, circle, square, or cross, then click an intersection.
Clicking an existing annotation with its matching tool removes it. Number and
letter labels are assigned in order, while selecting a different markup for the
same point replaces the old one. These changes use the editor's normal autosave.
Board coordinates appear along the top and left edges. Setup stones, passes, and
captures are replayed. **Refresh** returns to the SGF node stored for the current
item; **Current SGF node** in the baseline menu replaces that stored node.

The Solutions menu can add ordinary images or an **Add board** instance. A board
instance begins at the SGF root and behaves like an independent solution view.
Navigate to the desired node and click **Set** to store its opening point. Solution
images and boards remain available through the outer carousel.

Changes save automatically after a short pause. They are also flushed when you
return to Browse or close the app. Deleting
a populated position always requires confirmation.

### Tag system

Choose **Manage tags** to create and maintain the vocabulary used to organize the
collection.

- Type a new tag name and press **Enter** to create and select it.
- Type an existing name and press **Enter** to select it.
- Press **Tab** to accept autocomplete.
- Optionally, add a description explaining how the tag should be used; descriptions save
  automatically.
- Add or remove parents and children. Editing either side keeps the reverse
  relationship synchronized.
- See how many positions match a tag through inheritance and how many use it
  directly.

A tag can have more than one parent, while cycles are rejected. When tagging a
position, the app avoids redundant ancestors: adding a more specific descendant
removes its explicitly stored ancestor.

## How the collection is stored

Each position has its own folder. The main image, optional SGF, YAML annotations,
and solution images stay together:

```text
positions/
  p000001/
    position.png
    position.sgf                 # optional
    metadata.yaml
    solutions/
      solution-001.png
      solution-002.png
tags.yaml
generated/
  tag_index.yaml
```

A typical position record looks like this:

```yaml
description: White can take reverse sente before tenuki.
score: B +3.5
main_media_kind: board
sgf_start_path: [0, 1]
tags:
  - 3-3-joseki
  - reverse-sente
metadata:
  source: Cho Chikun encyclopedia
  game: Kitani vs Seigen
  move: 38
solution_images:
  - file: solutions/solution-001.png
    kind: image
    description: White resists immediately.
    score: W +1.5
    sgf_start_path: [0, 1, 2]
  - file: ''
    kind: board
    description: Quiet continuation shown on the native board.
    score: B +0.5
    sgf_start_path: [0, 2]
```

An `sgf_start_path` identifies a node by listing the child index taken at each
branch from the SGF root. An empty list means the root node. Paths remain
unambiguous even when separate variations contain the same move number.
`main_media_kind` and each solution's `kind` choose whether the app displays the
SGF board or image when both are available.

The app maintains its search index automatically. On startup it also checks the
collection and normalizes unambiguous image and SGF filenames. The generated
index can always be rebuilt from the position records and `tags.yaml`.

Your database files are ignored by this repository's `.gitignore`, so cloning the
application starts with an empty collection. If you want to version your own
collection, place it in a separate folder, initialize a Git repository there,
and launch with `python go_db.py --root <database-folder> gui`.

## Optional command-line interface

The CLI still works with the current data format. It is useful for scripting,
bulk maintenance, validation, and machine-readable searches. The GUI remains the
recommended way to work with scores and solution images: the CLI displays and
preserves those fields but does not yet provide dedicated commands to edit them.

Global options, written before the command:

```text
--root PATH       use a particular database folder
--config PATH     use a particular config.yaml
```

### Launch and initialize

```powershell
python go_db.py --root . init
python go_db.py --root . init --force
python go_db.py --root . gui
```

`init --force` overwrites the starter configuration and tag files; it does not
erase position folders.

### Search and inspect

```powershell
python go_db.py --root . search "joseki AND NOT ko"
python go_db.py --root . search "joseki" --verbose
python go_db.py --root . search "joseki" --json
python go_db.py --root . search "joseki" --limit 20
python go_db.py --root . position show p000001
python go_db.py --root . position show p000001 --json
```

JSON search and `position show` include descriptions, scores, metadata, and
solution-image records.

### Validate and maintain files

```powershell
python go_db.py --root . check
python go_db.py --root . rebuild-index
python go_db.py --root . clean --dry-run
python go_db.py --root . clean
python go_db.py --root . clean p000001 p000002
```

`check` reports missing images or metadata, ambiguous files, unknown or duplicate
tags, invalid hierarchy links or cycles, and an inconsistent generated index.
`clean` gives uniquely identifiable images and SGFs their canonical filenames.

### Create and edit positions

```powershell
python go_db.py --root . position create p000001 --image C:\path\board.png
python go_db.py --root . position create p000002 --image C:\path\board.png --sgf C:\path\game.sgf --description "Corner variation" --tag joseki --meta "move=38"

python go_db.py --root . position add-tag p000001 joseki reverse-sente
python go_db.py --root . position remove-tag p000001 reverse-sente
python go_db.py --root . position set-tags p000001 joseki tesuji
python go_db.py --root . position set-description p000001 "Black should connect first."

python go_db.py --root . position meta-set p000001 source "study notes"
python go_db.py --root . position meta-set p000001 source.page 127
python go_db.py --root . position meta-show p000001
python go_db.py --root . position meta-show p000001 source.page
python go_db.py --root . position meta-delete p000001 source.page
```

`position` can be shortened to `pos`. `--tag` and `--meta KEY=VALUE` are
repeatable during creation. Metadata values are parsed as JSON when possible, so
numbers, booleans, arrays, and objects retain their types. CLI creation requires
at least an image or an SGF; the desktop workflow is designed around positions
with a main image.

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

`tag` can be written as `tags`. Removing a tag is blocked while positions use it
directly. `--force` permits removal from child parent-lists; it does not silently
remove the tag from positions.

## Configuration

`config.yaml` controls the collection paths and canonical filenames. Relative
paths are interpreted from the selected database root:

```yaml
positions_directory: positions
tags_file: tags.yaml
generated_index: generated/tag_index.yaml

files:
  sgf: position.sgf
  image: position.png
  metadata: metadata.yaml
  sgf_extensions: [.sgf]
  image_extensions: [.png, .jpg, .jpeg, .webp, .bmp, .gif]
```

## Transparency and limitations

This project was heavily **vibe-coded with AI assistance**. Back up an
important collection before large imports or bulk file operations.

The native SGF board can edit numbered labels, triangles, circles, squares, and
crosses. Moves and the structure of the game tree are still read-only.

## Future Updates

Some things this project wants to add include

- Preset list of common tags, might publish my own position collection
- Possibility to publish, share, or merge positions with others
- Customizable color scheme/layout. Greater compatibility with different screen sizes.
- Expand the native SGF board with move and game-tree editing
- Integrate the native board with KataGo analysis

Any pull requests implementing these would be extremely welcome.
