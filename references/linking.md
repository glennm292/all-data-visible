# Linking: intra-domain and out of the data

A DACTAL-based app links in two directions, and they use different mechanisms.
Keep them straight:

- **Intra-domain** links stay *inside the data*: from one record to related records
  (a person to their messages, a channel to its messages). These are **qlinks** and
  DACTAL's same-name auto-join. Clicking runs a DACTAL query.
- **Out-of-domain** links leave *to the real world*: from a record back to its
  external source (open the email in Gmail, jump to the Slack thread, open a native
  app). Clicking navigates to a URL. This is how "humans stay in charge" is realized
  -- the user goes to the source and acts there.

## Intra-domain (staying in the data)

DACTAL **auto-joins a property value to a same-named dataset**: if a record has a
`team` property whose value matches a row in the `teams` dataset (by `id`/`name`,
singular/plural tolerant), `x.team.teams` resolves the record. **Join on a stable
key, never a display name** -- name drift silently drops rows (the "only 46 of 48"
bug). Canonicalize keys in shaping; derive display names from them.

A cell becomes a clickable *query* link when its value carries a `qlink`
sub-property; clicking runs `id` concatenated with `query`. Build them the
idiomatic way -- `id` is the value, `query` is a traversal -- never by stitching a
query prefix into `id`:

- **Forward** (a value links to the record it names): `id` = the value, `query` =
  `.<dataset>`.
- **Reverse** (a record links to the records that point back at it): `id` = the
  value, `query` = `|<prop>.<dataset>` (convert the value to an item carrying
  `<prop>`, then join into `<dataset>`).

The exact qlink recipes, sub-operators, and the orphan-check query live in this
skill's `references/dactal-query-language.md`. Use them; do not re-derive.

## Out of the data (linking to external resources)

Qlinks run DACTAL queries -- they cannot open an external URL. To link a record
back to its source you put an actual link *value* in a field, and rely on how the
DACTAL front-end renders cell values. Three render forms, cheapest first (verified
against `dactal_utils.js`'s `render()`):

1. **A bare URL string** -> auto-linked. A cell whose value starts with `http:` or
   `https:` renders as `<a href="…" target=link>…</a>`. Simplest for a permalink;
   the visible text is the URL (front-end truncates a long `?query`).
   - Use for: a source permalink you don't need to relabel (a Slack message
     permalink, a generic URL).

2. **An object `{name, uri}`** -> a labeled link. A cell value that is an object
   with exactly `name` and `uri` (an optional `id` is allowed, nothing else)
   renders as `<a href="uri">name</a>`. Clean label + href, but no control over
   `target`.
   - Use for: a labeled link where same-tab navigation is fine.

3. **A raw `<a …>…</a>` HTML string** -> emitted verbatim. A cell value that starts
   with `<a ` and ends with `</a>` is passed through untouched, so you control the
   label, `target`, `rel`, and the URL scheme.
   - Use for: **native-app deep links and new-tab links** -- e.g. open a Slack
     channel in the desktop app:
     `<a href="slack://channel?team=<TEAM>&id=<CID>" target="_blank">open in Slack »</a>`.
     Also the right form when you want `target="_blank"` so the link opens out of
     the app's iframe.

**Precompute the link in shaping**, as a field on the record (e.g. `link`, `open`).
Then it is *in the data* and shows up in the exposed query too -- consistent with
the ethos (the out-link is part of the record, not a UI-only decoration). The
custom surface reads the same field.

### Native-app vs web fallbacks

- `slack://channel?team=<team_id>&id=<channel_or_dm_id>` opens the Slack desktop
  app directly. `https://<workspace>.slack.com/archives/<cid>` opens web Slack,
  which then hands off to the app if installed -- a safer fallback if the deep link
  may not resolve. Pick per the user's stated preference; default to the native
  scheme when they asked to "open the app."
- Email: a Gmail message opens at `https://mail.google.com/mail/u/0/#all/<id>` (or
  `#search/rfc822msgid:<mid>`). `mailto:` composes a new message -- but sending is
  the *user's* action, consistent with humans-in-charge.
- Platform-internal endpoints and app URL schemes are fair game; inspect what the
  official client actually uses and reuse the same endpoints with the same
  user-session auth.

### Do not confuse the two

- A **qlink** (`{qlink:{id, query}}`) keeps the user inside the explorer, running a
  DACTAL query -- use it for record-to-record navigation.
- A **URL / anchor field** takes the user out to an external resource -- use it for
  "go act at the source."

A record often has both: intra-domain qlinks to explore related records, and an
out-link field to jump to the origin. In the Data app this skill installs, a chat row
carries an `open` field (form 3, a raw anchor to the app's `/open` route, which asks the
workspace to open that chat as a window and answers 204 so the query page stays put), and
the conversations view adds a reverse qlink (`messages »`) to pivot to that chat's
messages within the data.
