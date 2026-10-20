# IDgent project graph

This specification records IDgent product decisions for exploring a project of
notes, source files and their references. Values are selected for a readable
project overview and straightforward controls. No external graph reference or
external parameter table is used. `tests/graph_params.json` is the executable
settings contract shared by Python and JavaScript tests.

## Initial view and settings

A new graph includes notes, source files, missing references and unlinked files.
Tag nodes and referenced assets are opt-in to avoid crowding a code project.
Direction markers start enabled because import and reference direction matters.
The controls panel starts open, with the scope section expanded; other sections
start collapsed. Search starts empty, colour rules start empty, zoom starts at
1, and reduced motion starts disabled (the host accessibility preference can
override it). Incoming and outgoing traversal both start enabled. Local views
start at two hops, showing immediate dependencies and their neighbours.

| Setting / control label | Default | Range | Control step |
| --- | --- | --- | --- |
| `textFadeMultiplier` / Label detail | 0.2 | -1 to 2 | 0.1 |
| `nodeSizeMultiplier` / File marker size | 1.15 | 0.5 to 3 | 0.05 |
| `lineSizeMultiplier` / Connection width | 1.25 | 0.5 to 4 | 0.25 |
| `centerStrength` / Pull toward centre | 0.12 | 0 to 0.6 | 0.02 |
| `repelStrength` / Space between files | 6 | 0 to 24 | 0.5 |
| `linkStrength` / Connection tension | 0.35 | 0 to 1.5 | 0.05 |
| `linkDistance` / Preferred spacing | 160 | 48 to 480 | 8 |
| `scale` / Camera zoom | 1 | 0.04 to 6 | continuous |
| `localDepth` / Neighbourhood hops | 2 | 1 to 6 | 1 |

The 160-unit preferred spacing leaves room for short filenames. Modest centre
pull keeps disconnected files nearby. Linear force controls make the effect of
a slider change predictable: centre and link strengths are used directly;
repulsion is minus 120 times its control value, including zero for disabled
repulsion. The spacing range permits both compact and loose layouts.

Existing host bridge keys above remain stable for saved IDgent preferences;
redundant local-view keys are dropped. Unknown settings are discarded. Invalid
numbers and wrong types use defaults, finite numbers clamp to bounds. Only hop
counts are rounded to integers; other persisted numbers remain continuous.
Slider steps affect interaction, not saved-value quantization. Up to twelve
ordered colour rules are accepted. New rules use hex colours, stepping around
the hue wheel by 137.5 degrees from a blue starting hue (210 degrees), with
65% saturation and 64% dark / 42% light luminance. Existing packed RGBA colours
remain accepted for saved preferences and host bridge compatibility.

## Page text and accessibility

The canvas is labelled “Project files and references”. Before data arrives the
status reads “Mapping this project…”; an empty result reads “No files match this
view”. A capped result announces “View limited to N files with the most
connections”, where N is the visible file count.

Scope controls use “Filter project files”, “Tag markers”, “Referenced assets”,
“Hide missing targets”, “Unlinked files”, and “Source and imports”. In local
mode, “Neighbourhood hops”, “Referenced by”, and “References to” replace the
unlinked-file control. Numeric controls use the labels in the settings table.
“Reset graph preferences” restores filter, display, force and colour-rule
defaults, plus traversal defaults in local mode, while retaining search text,
zoom and section visibility. “Replay project history” is the timeline button's
accessible label.

A file's menu offers “Open file”, “Open another tab”, “Open in right pane”,
“Locate in project tree”, and “Copy file location”, in that order. These labels
express project actions; host action identifiers remain stable.

## Project data and filters

Notes use Markdown references and tags; source files contribute imports. Assets
appear only when referenced and enabled. A missing reference is a synthetic
node, and can be hidden. Search supports AND terms, OR clauses, negation,
quoted text, regular expressions and path/file/tag/content fields. Strict UI
filtering tests each file or tag; missing targets remain until their references
are removed. A local focus always survives search. Disabling unlinked files
removes nodes with no surviving edges. The first nonempty matching colour rule
colours a file; tags and missing targets retain their kind colours.

Resolution is case insensitive. Wiki paths try project root then source folder;
Markdown paths try source folder then root. Python imports try root, source,
then src. Filename matches precede aliases. Ambiguous names prefer the source
folder, shortest path, then lexical order. References with the same endpoints
and kind collapse with a count; self references remain in data. The renderer
uses distinct directed endpoint pairs for degree, counting a self pair twice,
and draws one line per unordered non-self pair with direction markers.
Local traversal uses breadth-first layers and the chosen incoming/outgoing
directions. These are IDgent file-exploration policies.

## Drawing and motion

Marker radius before zoom is `multiplier * min(24, 6 + 2*log2(1 + degree))`.
Negative degree is treated as zero. Logarithmic growth keeps hubs legible without
hiding nearby files. Screen radius multiplies this by the square root of zoom,
so markers shrink more slowly than spacing when zooming out. Label opacity is
`clamp((zoom - 0.12)/0.7 - detail*0.25, 0, 1)`. Connection width stays constant
in screen pixels. Direction opacity is `clamp((zoom - 0.18)/0.62, 0, 1)`;
marker length is `6*sqrt(width multiplier)` pixels. Label font size is
`12 + radius*0.2`, with a 224-unit wrap width and 12-unit hover offset.

Simulation runs at 60 ticks/second. Cooling reaches 0.002 after 240 ticks from
1; a structural edit reheats to 0.4. Velocity loses 45% per tick. Barnes-Hut
opening threshold is 0.65 and minimum squared repulsion distance is 64.
Collision uses a 32-unit radius and 0.7 correction strength, reserving a
64-unit gap for labels. New files start in a disc with 72 squared units of area
per file (5184 square units); files next to surviving neighbours use at most
24 units of jitter on either axis. Existing positions survive updates.

Alpha/colour easing approaches by 16% per 60 Hz frame, zoom by 18%. Pan velocity
filters with 25% new input and retains 86% per frame. Frame-independent easing
caps elapsed time at 50 ms. Wheel zoom is `exp(-pixelDelta/480)`, with line and
page input converted to 24 and 640 pixels; small control-wheel deltas double.
Zoom keeps the world point under the pointer fixed. Hover leaves unrelated
items at 28% opacity. A click allows six pixels of movement. Local marker
weight is `12/(layer+1)`, keeping even the outer hop visible. Timeline reveal
uses chronological file order when timestamps exist, at `clamp(items/12,8,72)`
items per second. Reduced motion skips visual easing.

The host supplies theme roles. A standalone page falls back to IDgent slate
background (#17212b), pale text (#e4edf5), blue accent (#63b3ed), green tags,
yellow assets, muted missing targets and cyan code markers. Focus and hover
colours precede rule colours, then kind colours, then ordinary file colour.

## Verification

Python tests validate persistence, bounds, fixture agreement, strict filtering
and the actual GTK/WebKit bridge. JavaScript tests use the IDgent fixture and
independent expected examples plus invariants: finite convergent layouts,
deterministic placement, stable updates, pinned nodes, repulsion accuracy,
zoom anchoring, directional traversal and timeline endpoints. Neither suite
loads an external specification or parameter fixture.
