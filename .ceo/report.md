STATUS: DONE

## Changed
- idgent/graphdata.py: `GraphIndex.folder(prefix)` (files below a folder + links among them), `GraphIndex.empty()`.
- idgent/graphview.py: per-view `graph_type` (global|local|note), `set_graph_type()`, `graphType` bridge message, `setGraphType` page command (type + empty-state message), worker picks build/local/folder/empty. Note type uses `notes.NOTES_DIR`/`notes_dir`. Existing show_local/show_global/options-local keep the type in sync.
- idgent/graph_web/{graph.html,graph.js,graph.css}: "Select graph type" icon button next to the cog (chip and open panel), radio-style 3-item popover (keyboard, Esc, outside click), empty-state messages, accent colour on button when not global. Transitions reuse existing status fade (reduce-motion aware).
- tests: test_graphdata (folder, empty), test_graphview (types, missing notes folder), test_graph_web (menu has 3 options, switch posts message, empty state).
- window.py not changed (active file and root were already passed).

## Verified
- Targeted graph tests: 58 tests, all OK after fixing two failures of my own (rerun test_graphview: 15 OK).
- Full suite once: 909 tests OK (skipped=2), no segfault.
- Screenshots (not committed): .ceo/type-menu.png, type-global.png, type-local.png, type-note.png, type-local-empty.png — checked visually.

## Notes
- Local graph with no active file shows "Open a file to see its local graph"; note graph with no folder shows "No notes yet. Create a note to see the note graph"; empty folder shows "No notes in the notes folder".
- Selected type is per graph pane, session only (not persisted).
