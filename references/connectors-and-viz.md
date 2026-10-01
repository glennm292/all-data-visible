# Connectors and visualizations

DACTAL renderers and data connectors are loaded via the export's `connectors`
dataset (see export-and-serving.md). Each entry names a `.js` file that the stock
front-end loads from dactal.org. `run.py fetch-frontend` downloads the current
`catalog.json` (the full list) into your assets dir; the most useful ones are below.

A **renderer** registers an annotator (e.g. `chart`, `map`) via `add_renderer`.
You produce its input by annotating a synthesized item with a spec object, e.g.
`|chart=(...)`. The renderer reads that spec **only in a browser** — so
`run.py verify-queries` (Node) confirms the query returns the spec rows, and
`run.py verify-visual` (headless Chromium) confirms it actually paints.

## Visualization connectors and their input shapes

Each spec is `[{...}]` (a one-element list) produced by synthesizing (`...`) the
points into one item and annotating the spec on it. `.of` is the collected points.

### `dactal_chart.js` — line/bar/area/scatter (SVG)

Spec: `[{of:[points], x, y, type, height, width, ...}]`. Fields: `x` (property or
`~name`), `y` (property or `~count`), `type` (`bar`/`line`/`area`/`scatter`),
`stacked`, `smooth`, `legend`, `colors`, `trendline`, `height`, `width`.

```
goals/stage#(.of:@1.date)... |chart=(.of#(.of:@1.date)...x=~name,y=~count,type=bar,height=340,width=680)
```

### `dactal_map.js` — geographic point map (Leaflet)

Spec: `[{of:[points], latField, lonField, colorField, weightField, minColor,
maxColor, minRadius, maxRadius, initialLat, initialLon, initialZoom, height,
width}]`. Marker size ← `weightField` (number), colour ← `colorField` (a numeric
value ramps `minColor`→`maxColor`; a CSS colour is used as-is). Coordinates come
from `latField`/`lonField`.

```
stadiums... |map=(.of...latField=~lat,lonField=~lon,colorField=~goals,weightField=~matches,
  minRadius=6,maxRadius=30,minColor=blue,maxColor=red,initialLat=39,initialLon=[-98],initialZoom=3,height=560,width=900)
```

### `dactal_timeline.js` — horizontal timeline (SVG)

Spec: `[{of:[points], dateField, startField, endField, colorField, labels,
rowHeight, minDate, maxDate, width}]`. Single-date events use `dateField`; ranged
events use `startField`/`endField`.

```
matches:played=played... |timeline=(.of...dateField=~date,colorField=~goals_total,width=900,rowHeight=16)
```

### Built-in heatmap (no connector)

A query that yields ONE row with `xvals` / `yvals` / `vvals` renders as a heatmap.
`xvals` and `yvals` are the axes; `vvals` the cell values. Group by the two axes,
count into `v`, then synthesize the three axis lists:

```
players/x=age,y=position |v=(.count)
  ...xvals=(.x#+),yvals=(.y#@=(Goalkeeper,Defender,Midfielder,Forward)),vvals=(.v)
```

### Other renderers (from the catalog)

- `dactal_miller.js` — Miller-column (cascading list) browser.
- `dactal_cytoscape.js` — network graph.
- `dactal_rowtate.js` — cycles a table one row at a time (dissolve).
- `dactal_wordcloud.js` — word cloud.
- `dactal_codemirror.js` — replaces the query box with a syntax-highlighting editor.

## Data connectors (fetch external data live)

The catalog also has read connectors (Spotify, MusicBrainz, Discogs, Wikipedia,
OpenAlex, GitHub, Open-Meteo, REST Countries, US Census, ClinicalTrials, EDGAR,
Yahoo Finance, World Bank, …) that fetch from an external API inside the browser.
For an explorer built from a **bulk snapshot** (this skill's common path) you
inline the data instead and usually only need the *renderer* connectors above.
Loading a data connector is the same mechanism: add it to the `connectors`
dataset. Namespaced connectors (e.g. `spotify`) also carry a `namespace`.

## Writing NEW connectors is out of scope

The original DACTAL manual's steps 4 and 7 cover writing brand-new JS
API/visualization connectors. That is a separate, rarer task and is **out of
scope** for this skill — the skill covers loading existing connectors and
inlining a bulk snapshot, which is the common path. If you genuinely need a new
connector, see the connector authoring docs on dactal.org (`add_renderer` /
`dactal.connect`) rather than this skill.
