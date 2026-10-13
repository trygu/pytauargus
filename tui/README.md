# pytauargus-tui

An interactive terminal UI for [Tau-Argus](https://github.com/trygu/tauargus-engine),
the statistical disclosure control engine. It is a thin view over the
`pytauargus` engine; every action is a call the headless CLI already makes.
Installing it also gives you the `tauargus` CLI.

## Screenshots

![Loaded table, every cell coloured by its safety status](https://raw.githubusercontent.com/trygu/tauargus-engine/master/docs/tui-images/02-table0-unsafe.png)

![Suppression preview, inspect the throwaway result before accepting](https://raw.githubusercontent.com/trygu/tauargus-engine/master/docs/tui-images/04-preview.png)

![Accepted; the preview becomes the working session](https://raw.githubusercontent.com/trygu/tauargus-engine/master/docs/tui-images/05-accepted.png)

## What it does

- **Runs supported `.arb` batches.** It runs the engine pipeline (apriori
  safety rules, suppression, rounding, audit), and every computed table is
  browsable. Start without a file and a `.arb` file browser opens.
- **Colours cells by status.** Green safe, red unsafe (a sensitive value),
  cyan protected, yellow hidden by a suppression, dim empty. All 14 engine
  status codes are recognised and named.
- **Shows what any cell means.** The detail pane shows the value, status name,
  pending mark, and, after an audit, the realised feasibility interval for
  the cell under the cursor.
- **Lets you mark cells before solving.** `u` requires suppression, `p`
  protects (keeps), `S` overrides safe. Marks are instant draft edits with
  toggle/replace and undo/redo; the solver never runs until you ask.
- **Previews suppression and rounding.** `s` (OPT, whole table) and `r`
  (RND) run on a throwaway copy of your batch. You inspect the previewed
  grid, then `y` accepts or `n` cancels. Your working table never changes
  behind your back, and a preview that is no longer current is flagged.
- **Audits.** Realised feasibility intervals per cell (`a`).
- **Exports.** Writes the accepted table as a legacy `INTERMEDIATE` `.tab`
  file (`o`).
- **Saves the workspace.** A versioned, fingerprinted JSON manifest of
  the accepted operations and pending cell marks, written next to the `.arb`
  (`w`).
- **Reopens a saved workspace.** Launch `tauargus-tui` on the
  `.workspace.json`, or pick it in the file browser. The accepted result is
  rebuilt through the engine, the pending draft comes back (undoable), and
  the revisions are continuous. If the `.arb` changed since the save, the
  old outcome is not treated as current; the current input opens fresh
  instead, with a clear message.
- **Writes a replay batch.** `R` materialises the accepted recipe as a
  headless-runnable `.arb` (plus apriori files) next to the input.
  `tauargus run <stem>.replay.arb` replays the recorded operator intent. It
  reruns the solver and does not restore or guarantee an identical numerical
  result.

## What it can't do (yet)

- **Inspect all dimensions.** The grid uses the first two variables and
  has no selector for higher-dimensional slices. Use the Python/CLI workflow
  for higher-dimensional inspection; the grid is not a complete view.
- **Restore the exact accepted result.** Reopening reruns the recipe.
  The manifest hashes the `.arb`, not referenced data, metadata, hierarchy
  or apriori files; changes in those dependencies are not detected by that hash.
  Keep the complete inputs and accepted outputs when reproducibility matters.
- **Provide a publication-output workflow.** `o` exports a diagnostic
  INTERMEDIATE `.tab`, not a masked release table. The grid shows original
  responses, including after rounding. Inspect actual rounded values and
  select publication output explicitly through the engine API/CLI.
- **Offer every legacy method.** The headless engine rejects GH/NET/CTA;
  the interactive method shortcuts are OPT and RND.

- **Mark total cells.** That needs the real total code, which the display
  labels do not preserve; the app blocks such marks with a message.
- **Choose the rounding base.** `r` always uses the engine's minimum base.
- **Work on several batches.** One `.arb` per session, and the batch spec
  itself is not editable from the UI.

## Install

```bash
pip install pytauargus-tui
# or, as an extra of the engine package:
pip install "pytauargus[tui]"
```

## Run

```bash
tauargus-tui                          # opens a file browser (.arb + saved workspaces)
tauargus-tui path/to/batch.arb        # opens that .arb batch directly
tauargus-tui path/to/batch.workspace.json   # reopens a saved workspace
```

The full user guide covers every key, every status code and the whole
workflow: [`docs/tui.md`](https://github.com/trygu/tauargus-engine/blob/master/docs/tui.md).

## Develop (monorepo)

```bash
cd bindings/tui
uv sync --extra test
.venv/bin/python -m pytest
```
