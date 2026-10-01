# The Maker Ecology ethos

This is the *why*. Every rule in the skill is downstream of it. The ethos is Glenn
McDonald's, from furia.com (`https://furia.com/page.cgi?type=log#id520`); DACTAL
(dactal.org) is his tool for living it. If you are building a DACTAL-based app you
are building under this ethos, whether or not the user restates it.

## The core

The world improves when everything that affects people is done "with curiosity and
love and accountability." Tools should amplify human intent responsibly; the more
a tool can do, the more attentive care it must be used with.

Applied to software, the parts that matter for how you build:

1. **Transparency over blind trust.** "By downloading your own data, you don't have
   to *trust* anybody to do invisible math for you correctly." Expose the data AND
   the queries so people can explore for themselves. This is the direct source of
   the two mirror rules (expose what you use; use what you expose) and of
   "see the query" on every view. A derived number the user cannot trace back to
   its inputs is exactly the "dubious mystery" the ethos rejects.

2. **Humans stay responsible; AI cannot love.** Algorithms are "just idea tools,
   and the ideas are still ours." An LLM "does not love," so it "should never be in
   charge of anything that affects people." In practice: the app surfaces,
   organizes, and ranks; the *human* takes any action that touches another person.
   Default connectors to read-only; when action is wanted, prefer linking the user
   OUT to the source to act there themselves over acting for them.

3. **Relationships, not consumption.** He rejects reductive counting: a domain "is a
   social conversation between actual human beings," a "creative ecology." Model and
   present accordingly -- center the people and the relationships (who you are in
   conversation with) over vanity metrics (how many unreads). Let this shape which
   queries you make canonical and how the custom surface reads.

4. **The maker's trinity (Sayers): inspiration -> execution -> evaluation of
   reception.** Build the thing, then *scrutinize the real result and iterate*. In
   this workflow that is: put a real creation in front of the user fast, look at the
   actual output critically (not just "no errors"), and refine. It is why the build
   flow is interactive and why you verify behavior, not just exit codes.

## How the ethos becomes engineering

| Ethos | Rule in this skill |
|---|---|
| No invisible math | Every view is a saved query with a "see the query" link (expose what you use) |
| No parallel hidden logic | Surfaces execute the exposed query, not a reimplementation (use what you expose) |
| Traceable derived values | Scores/signals are fields in the data, computed in the pipeline, and shown |
| Humans in charge | Read-only by default; act by linking out to the source |
| Your private copy | The explorer keeps a modifiable local copy; reimport only on real change, never reset-every-load |
| Relationships over counting | Model people/relationships as first-class; make relationship views canonical |

If a decision is ever ambiguous, resolve it toward *the user being able to see and
control what the machine did on their behalf.*
