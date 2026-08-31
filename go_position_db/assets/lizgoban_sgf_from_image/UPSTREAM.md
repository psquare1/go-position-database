# LizGoban SGF from Image

These files are adapted from LizGoban's semiautomatic SGF-from-image tool:

https://github.com/kaorahi/lizgoban/tree/master/src/sgf_from_image

Pinned upstream revision: `20944051392479082d7c54793917d3150bc6e01d`

Upstream license: GNU General Public License version 3 (`GPL-3.0-only` as
declared by the upstream package manifest). See `LICENSE.txt`.

Local changes:

- load the selected application image instead of an upstream demonstration image;
- remove Electron-specific integration requirements;
- replace the TWGL helper calls with equivalent browser WebGL operations;
- remove links to demonstration images that are not bundled; and
- remove standalone download, clipboard, and image-ingestion controls in favor
  of the application's own media workflow;
- present calibration and correction instructions as concise lists; and
- host the page locally in the application's Qt WebEngine review dialog.
