# Phase 2 — Analysis, Correction & Verification Service

A FastAPI service that takes a chapter PDF and its HTML rendition, finds every
place they disagree, fixes what it can prove, verifies the result and publishes
the corrected document.

```
PDF  ─┐
      ├─▶ analyze ─▶ compare (6 levels) ─▶ correct ─▶ verify ─▶ publish ─▶ MongoDB
HTML ─┘
```

## Quick start

```bash
cd python-services
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium          # needed for JavaScript-rendered HTML
cp .env.example .env                 # fill in MongoDB + Cloudinary credentials
python main.py                       # http://localhost:8000  (docs at /docs)
```

```bash
curl http://localhost:8000/health
# {"status":"ok","version":"0.2.0","storage":"mongodb","cloudinary":true,"activeJobs":0}
```

Run the tests (no network or database required — they use the in-memory store):

```bash
pip install -r requirements-dev.txt
pytest                               # 97 tests
```

## How work starts

The frontend creates a job with status `QUEUED` and polls it every two seconds;
nothing tells this service to begin. So the service **watches the `jobs`
collection itself**. A background worker claims the oldest `QUEUED` job
atomically (`findOneAndUpdate` to `PROCESSING`, so two workers can never take
the same job), runs the pipeline, and writes progress back as it goes. The UI's
*Retry* button simply sets a job back to `QUEUED`, which the next poll picks up;
its *Cancel* button sets `CANCELLED`, which the pipeline checks between stages
and honours mid-run.

Set `JOB_WORKER=0` to disable the worker and drive the service purely through
`POST /process/{jobId}`.

## What the frontend reads

The Next.js app owns the schema for `jobs`, `issues` and `corrections`, and its
vocabulary is deliberately smaller than the engine's — eight issue types rather
than nineteen, four severities rather than three, five job stages rather than
nine. [`services/job_sync.py`](services/job_sync.py) translates on the way out:

| Engine | Frontend |
| --- | --- |
| `MISSING_IMAGE`, `BROKEN_IMAGE_SRC` | `IMAGE_MISSING` |
| `MISSING_SECTION`, `MISSING_QUESTION`, `MISSING_ANSWER` | `MISSING_TEXT` |
| `WATERMARK`, `DUPLICATE_QUESTION` | `EXTRA_TEXT` |
| `HEADING_LEVEL_MISMATCH`, `ALIGNMENT`, `MISSING_ALT_TEXT`, … | `FORMATTING` |
| severity `HIGH` / `MEDIUM` / `LOW` | `CRITICAL` / `MAJOR` / `MINOR` (`INFO` when unsure) |
| stages `downloading` … `publishing` | `ANALYZING_PDF` … `VERIFYING` |

Each issue document also carries an `engine` sub-document with the full internal
record. Mongoose ignores it, and it is what lets a fix approved days later be
replayed without re-analyzing the PDF.

Because the UI writes approve/reject straight into `issues.status`, that column
is the source of truth: `POST /jobs/{jobId}/rebuild` reads the reviewer's
current decisions back out and regenerates the corrected HTML from them.

## API

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness, storage backend, asset store, and whether the worker is polling |
| `POST` | `/process/{jobId}` | Start the pipeline. `?wait=true` runs it inline and returns the result |
| `POST` | `/process` | Phase 1 compatible form, job id in the body |
| `GET` | `/jobs/{jobId}` | Status, stage, progress, scores, corrected document URL |
| `GET` | `/jobs/{jobId}/issues` | Issues found, filterable by `severity`, `status`, `type` |
| `GET` | `/jobs/{jobId}/report` | Full verification report |
| `POST` | `/jobs/{jobId}/approve-issue` | Apply a correction the reviewer approved, and republish |
| `POST` | `/jobs/{jobId}/reject-issue` | Undo a correction the reviewer rejected, and republish |
| `POST` | `/jobs/{jobId}/rebuild` | Rebuild the corrected HTML from the current decisions (async; `?wait=true` to block) |
| `DELETE` | `/jobs/{jobId}` | Cancel a job running in this process |

Starting a job:

```bash
curl -X POST http://localhost:8000/process/671f0c8e2a1b4d0012ab34cd \
  -H 'Content-Type: application/json' \
  -d '{"autoFix": true, "autoFixThreshold": 0.95}'
```

URLs may be passed as `pdfUrl` / `htmlUrl`; anything omitted is resolved from the
job's `pdfDocumentId` / `htmlDocumentId` in MongoDB. The call returns `202`
immediately and the frontend polls `GET /jobs/{jobId}` for `progress`.

## How it works

### 1. Analysis

`PDFAnalyzer` (pdfplumber) merges text lines into paragraph blocks with their
page, bounding box, font, size and colour; infers a heading hierarchy from
typography and numbering; extracts exercises, tables and per-page layout; and
renders every figure to pixels. Figures include **clustered vector drawings**,
not just embedded bitmaps — textbook diagrams are usually vector art and would
otherwise look absent on the PDF side.

`HTMLAnalyzer` (BeautifulSoup + Playwright) loads the page in Chromium, so
JavaScript-rendered content is analyzed as a reader sees it. From the live DOM it
takes each element's box, computed font, alignment and real visibility, then
parses that rendered markup: text blocks with CSS-selector DOM paths, images
(including `srcset`, lazy-load attributes, CSS backgrounds and inline SVG),
headings, exercises, embedded JSON and metadata. Without Chromium it falls back
to static parsing and says so in `warnings`.

### 2. Comparison — six levels

| Level | What it checks | Issues raised |
| --- | --- | --- |
| Text | Fuzzy block alignment (rapidfuzz, ligature/hyphenation aware) | `MISSING_TEXT`, `EXTRA_TEXT`, `TEXT_MISMATCH` |
| Images | pHash + dHash + SSIM, one-to-one best-first matching | `MISSING_IMAGE`, `IMAGE_MISMATCH`, `EXTRA_IMAGE`, `BROKEN_IMAGE_SRC`, `MISSING_ALT_TEXT` |
| Structure | Heading titles, levels and sequence | `MISSING_SECTION`, `HEADING_LEVEL_MISMATCH`, `STRUCTURE_MISMATCH` |
| Order | Longest-increasing-subsequence over matched elements | `ORDER_MISMATCH` |
| Questions | Exercise text, numbering, duplicates, answers | `MISSING_QUESTION`, `DUPLICATE_QUESTION`, `QUESTION_MISMATCH`, `MISSING_ANSWER` |
| Layout | Normalized position, alignment and width share | `ALIGNMENT`, `LAYOUT_MISMATCH` |

Two design choices keep the review queue honest:

* **One defect, one issue.** A dropped exercise would otherwise surface as both a
  missing question and missing text; a wrong figure as a missing image *and* an
  extra one. Overlapping reports are collapsed to the most specific.
* **Sections move as a unit.** A swapped section is one issue whose fix moves the
  heading with all its content, rather than a dozen "out of sequence" reports.

Each issue carries a severity, a calibrated confidence, the evidence behind it,
and — where a safe machine fix exists — a ready-to-apply correction.

### 3. Correction

`CorrectionEngine` only executes corrections the comparison engine attached, and
only when `confidence >= AUTO_FIX_CONFIDENCE` (0.95) or a reviewer approved the
issue explicitly. It inserts missing figures at the right anchor, repoints wrong
images, sets alt text from PDF captions, retags headings, inserts missing
exercises at their PDF position, moves sections, adjusts alignment and removes
watermarks.

Corrections are applied in a fixed order (content → structure → cosmetics), and
every target carries a **content fingerprint**. An `nth-of-type` DOM path is only
valid for the document it was computed from — once an earlier fix inserts or
retags an element the path can silently address the wrong node, so each target is
verified against the text or image source it should contain, and falls back to
finding the element by content.

Figures referenced by *any* correction are uploaded to Cloudinary during the run,
not just the ones applied, so an issue approved days later can still be fixed
after the temp files are gone. With Cloudinary unavailable, figures are embedded
as data URIs and the document stays self-contained.

### 4. Verification

The corrected HTML is re-analyzed and re-compared against the PDF. Issues are
matched across the two runs by content signature (the second pass produces fresh
ids), which is what makes it possible to say which defects were genuinely
resolved, which survived, and which were *introduced*. New observations of a kind
that is still unfixed are reported as side effects rather than counted as
regressions. The report carries before/after scores, a nine-point checklist, a
quality score and concrete recommendations.

## Review workflow

Fixes below the auto-fix threshold wait for a person. Approving one applies it;
rejecting one undoes it. Both rebuild the corrected document **from the original
markup** using every current decision, so a rejection genuinely reverts rather
than patching over.

```bash
curl -X POST http://localhost:8000/jobs/$JOB/approve-issue \
  -H 'Content-Type: application/json' -d '{"issueId": "iss_5c1f...", "note": "checked"}'
```

## The Complete HTML deliverable (add-on)

Patching an uploaded HTML has a ceiling when that HTML is an *enriched* study
guide rather than a mirror of the PDF. So every run publishes a second
deliverable. Its preferred form is a **template merge**
([`services/html_merger.py`](services/html_merger.py)): the uploaded page's own
banner, tabs, cards and enrichment are kept exactly as rendered, and only the
PDF content it *dropped* is added — one clearly labelled "📘 From the textbook"
card per PDF section, placed in the right tab. Placement uses three signals in
order: panel names (the PDF's SUMMARY pins to a tab called Summary), weighted
votes from where each section's matched content already lives, and a default
"content home" for numbered chapter sections. The template's own scripts are
replaced by a dozen lines of ours that only switch tabs. A prerequisite worth
naming: content behind the page's tab navigation is treated as *visible* during
comparison — five of six panels sit at `display:none` until clicked, and
ignoring them made the comparison blind to most of the document.

Two rendering rules matter for mathematics-heavy chapters. A block whose lines
are mostly equations (`1 = 1` / `1 + 3 = 4` / …) keeps the PDF's own line
breaks and renders as a displayed stack instead of a flattened sentence — and
when the PDF laid a figure beside such a stack, the two render side by side.
When the template itself pasted a stack inline — the glued
`1 = 11 + 3 = 4…` form — the paragraph is cut at the equations and the stack is
re-emitted line by line after it (only plain paragraphs are rebuilt; a MathJax
tree is left alone). Arrow-diagrams whose content is mostly numbers are real
figures: only *alphabetic* words argue that a boxed region is prose; stacked
arc rows merge into one diagram; and the crop extends to every label-like text
line the diagram's band touches — its title, its row labels, the leading and
trailing numbers — instead of growing by blind pixels, which clipped all of
them. A lone thin arcs row (the Perfect-Cubes strip) only clears the size floor
once its numbers are included. And a dead placeholder image (`src="https://"`) is paired with its PDF figure
through *context*: the text block immediately before the placeholder (by DOM
order — geometry is all zeros inside a hidden tab panel) must be the matched
twin of the text block immediately above the figure in the PDF, with displayed
equations skipped as anchors. The merge then repoints the placeholder in place.

When there is no template to merge into, the fallback is a standalone rendition
built straight from the PDF by
[`services/html_generator.py`](services/html_generator.py):

* reading-ordered text with the analyzer's heading hierarchy, grouped lists,
  and a sticky table of contents,
* figures rendered watermark-free, placed exactly where the prose references
  them, with their PDF captions as `<figcaption>`/alt text,
* ruled tables rebuilt as real `<table>` markup (and the words inside a table
  or figure region are never repeated as loose paragraphs),
* self-contained: inline CSS, no scripts, dark-mode aware; figures hosted on
  Cloudinary or inlined as data URIs without it.

The job page offers it as **Complete HTML** (`GET /api/jobs/{id}/generated`,
`?inline=1` to view). Known limits: mathematics arrives as flattened text
(`x²` → `x2`) because that is what PDF text extraction yields, and a watermark
the publisher flattened into the page's ink layer stays faintly visible inside
figures.

## Reaching the corrected document

The corrected HTML is published to Cloudinary, and its URL is mirrored into the
job's `correctedHtmlUrl` field so the frontend can offer it. The UI does not link
Cloudinary directly — it serves the file through `GET /api/jobs/{id}/corrected`,
which fetches it server-side and sends it as an attachment (add `?inline=1` to
view it in the browser instead). That keeps the download working regardless of
cross-origin rules and gives it a sensible filename.

Mongoose only returns fields its schema declares, so `correctedHtmlUrl` had to be
added to `JobSchema` — and a running `next dev` keeps the previously compiled
model, so a schema change needs a dev-server restart to take effect.

A project can hold several processing runs of the same chapter, and each run
publishes its own document. The corrections page therefore has a run picker: it
scopes the list *and* names the run the export downloads, so the file you get is
always the one whose corrections you are looking at.

## Storage

The Next.js app owns `projects`, `documents` and `jobs`; this service reads those
and owns `issues`, `reports` and `jobstates`. Job `status`, `progress` and
`error` are mirrored back into `jobs`, so the existing frontend polling keeps
working unchanged.

If MongoDB is unreachable the service logs the reason and runs on an in-process
store, so the API stays up. Atlas connections use the certifi CA bundle
(Python on macOS has no system CA store of its own).

> The frontend's Mongoose connection resolves to the **`test`** database when the
> URI names none, so `MONGODB_DB=test` keeps both halves on the same data.

## Configuration

All settings live in `.env` (see `.env.example`).

| Variable | Default | Meaning |
| --- | --- | --- |
| `MONGODB_URI` / `MONGODB_DB` | — / `test` | Database connection |
| `CLOUDINARY_*` | — | Asset store; without it figures inline as data URIs |
| `AUTO_FIX_CONFIDENCE` | `0.95` | Confidence needed to fix without a human |
| `MAX_CONCURRENT_JOBS` | `2` | Jobs processed at once per worker |
| `PDF_RENDER_DPI` | `150` | Figure rasterization quality |
| `PDF_MAX_PAGES` | `400` | Guard against runaway documents |
| `HTML_RENDER_TIMEOUT_MS` | `30000` | Chromium page load budget |
| `TEXT_MATCH_THRESHOLD` | `0.75` | Minimum similarity to call two blocks the same |
| `IMAGE_MATCH_THRESHOLD` | `0.75` | Minimum similarity to call two images the same |
| `STRUCTURE_MATCH_THRESHOLD` | `0.80` | Heading title matching |
| `QUESTION_MATCH_THRESHOLD` | `0.72` | Exercise matching |
| `CORS_ORIGINS` | `localhost:3000` | Browsers allowed to call the API |

## Layout

```
python-services/
├── main.py                        FastAPI app and routes
├── models/models.py               Pydantic models shared by every service
├── services/
│   ├── pdf_analyzer.py            PDF text, figures, structure, layout
│   ├── html_analyzer.py           HTML parsing + Chromium rendering
│   ├── comparison_engine.py       Six comparison levels, issue generation
│   ├── correction_engine.py       Applies corrections to the HTML
│   ├── verification_engine.py     Re-checks the result, builds the report
│   ├── cloudinary_client.py       Figure and document uploads
│   ├── db.py                      MongoDB access (+ in-memory fallback)
│   └── pipeline.py                Job orchestration and the review service
├── utils/                         text / image / bbox / file / hash helpers
└── tests/                         97 tests, plus a generated sample chapter
```

## JavaScript-built documents

If a page builds its own content on load — a study-material SPA whose markup is
an empty shell until a script fills it in — then the corrected copy has a
problem the analyzer cannot ignore: reopening it re-runs that script, the DOM is
rebuilt from the page's own data, and every correction disappears. Measured on a
real chapter, 72 of 77 corrections were discarded this way.

So when rendering shows that the content is script-generated, the corrected
document is **frozen**: executable scripts and inline event handlers are removed
(JSON data blocks are kept), leaving the rendered result with its corrections
intact. Sections the page revealed only through its own navigation — the other
tabs of a tabbed chapter — are un-hidden at the same time, otherwise freezing
would leave most of the chapter behind dead buttons. The trade is interactivity — tabs and menus in the corrected copy stop
working — and the job's warnings say so. Set `FREEZE_RENDERED_HTML=0` to keep the
scripts and accept that corrections will not survive.

For this class of document the durable fix is to correct the **data the page
renders from**, not the DOM it produces. The issue list is still the useful
output; treat the corrected HTML as a verification artifact.

## Notes from real documents

Two behaviours exist because real textbook PDFs and real study-material HTML
break the obvious assumptions:

* **Page furniture is not content.** A chapter title and book name reprinted in
  the margin of every page are part of the print layout. Blocks that repeat
  across pages *and* sit in the top/bottom margin on every appearance are tagged
  `furniture` and excluded from comparison, instead of being reported as missing
  text once per page.
* **A bare number is not a heading.** Page numbers are often set larger than
  body text, so a size-based rule promotes them to headings and then reports
  each one as a missing section. Headings must contain at least one letter.
* **Production leftovers are not content.** A footer like
  `Chapter 1.indd 4  10-07-2025 14:06:40` carries a different page number every
  time, so no repetition rule catches it; it is matched by pattern instead.
* **Some PDFs fake bold by drawing every glyph twice**, which extracts as
  `CChhaapptteerr`. Duplicates are identified by their coordinates — two
  identical glyphs at the same spot — so words like "committee" are untouched.
* **A page-sized image is the paper, not a figure** — scanned-page backgrounds
  are excluded by an area ceiling.
* **An image repeated across most pages is a stamp.** This chapter's PDF carries
  the "© NCERT — not to be republished" watermark as the same picture on every
  page. Repeats are recognised by their **source pixel size** (a hash of the
  rendered region differs per page, because different text sits underneath),
  excluded from figure detection, and the stamp object is deleted before figure
  regions are rendered — so extracted figures come out without the watermark
  baked into their pixels. Only the stamp image itself is deleted, never its
  containing form: those forms also hold the page's mask/blend layers, and
  removing one turns the artwork into a black box. A caveat: when the publisher
  has *also* flattened a watermark copy into the page's ink layer, that ghost is
  part of the artwork and cannot be removed.
* **A bordered box full of prose is not a diagram.** Summary and definition
  panels are drawn with vector frames, which look exactly like figures; regions
  containing more than a paragraph's worth of text are skipped.
* **Frozen copies unpin sticky bars.** A sticky tab bar is useful while its
  scripts work; in a frozen copy it can only hover over the text, so it is made
  static and scrolls away with the page.
* **Frozen tab bars become jump links.** When the nav's buttons line up
  one-to-one with the revealed sections, each button is converted to a plain
  `<a href="#…">` — the tabs are clickable again without a single script, so
  nothing can rebuild the page and discard the corrections.
* **Rebuilds repeat the pipeline's document treatment.** The freeze, reveal and
  unpin decisions are stored with the job; a rebuild triggered by an approval
  applies them identically. Without that, the reviewer's first approval would
  replace the frozen document with an unfrozen one whose own scripts throw the
  corrections away.
* **Inserted paragraphs never land between list items**, and a run of blocks
  sharing one anchor keeps its reading order. The text-match fallback scores
  each element on its own text (never a container's subtree) and only accepts
  anchors inside the page's main content — matching a card's full text used to
  drop insertions unstyled beside it.
* **Corrections never go into the page furniture.** An anchor that resolves
  inside a `<header>`, `<nav>` or `<footer>` is refused and the fix is left for
  a human: chapter text injected into a site banner is worse than no fix.

Worth knowing when reading a report on real content:

* If the HTML is an *enriched* rendition — a study guide that adds its own
  overview, icons and navigation rather than mirroring the PDF one-for-one —
  then `EXTRA_TEXT` and `EXTRA_IMAGE` findings are working as designed and are
  not defects. Coverage of the PDF (`MISSING_*`) is the meaningful signal.
* Figure detection counts anything that reads as a picture, including the large
  coloured panels some textbooks use behind every worked example. On such a
  layout the PDF side can report far more figures than the HTML has images.
  `IMAGE_MATCH_THRESHOLD` and the figure-area floor in `pdf_analyzer.py` are the
  knobs for that; calibrate them against a chapter you have already reviewed by
  hand before trusting the count.
* Maths is extracted from PDFs as plain text, so `x²` arrives as `x2`. In a
  maths chapter this produces `MISSING_TEXT`/`TEXT_MISMATCH` findings on
  equations that are in fact present, rendered by MathJax/KaTeX.

## Test fixtures

`tests/make_fixtures.py` builds a three-page chapter PDF and an HTML rendition
that differs from it in exactly five ways — a missing figure, a substituted
figure, a dropped exercise, a wrong heading level and two swapped sections, plus
a watermark. The suite asserts that all six are found, that nothing else is
reported, and that the corrected document matches the PDF afterwards.

## Cloudinary: PDF delivery is blocked by default

New Cloudinary accounts refuse to serve PDFs: the upload succeeds, and the
`res.cloudinary.com` URL then answers **HTTP 401**. Signing the URL does not
help — the restriction is account-wide, not per-asset.

The service works around this by fetching such assets through the authenticated
Admin API instead, so no account change is required. The cleaner fix is to turn
delivery on: **Cloudinary console → Settings → Security → "PDF and ZIP files
delivery"**. If both routes fail, the job's error message says exactly this.

## Run the service from the virtualenv

`python main.py` with a system interpreter will start, serve `/health`, and
process nothing — the analysis dependencies (pdfplumber, pymongo, playwright,
scikit-image) live in the venv:

```bash
source venv/bin/activate && python main.py     # or: ./venv/bin/python main.py
```

## The review loop

Approving or rejecting an issue in the UI (`PATCH /api/issues/[id]`) records the
decision, writes the `corrections` row, and then asks this service to rebuild the
corrected document from **every** current decision — which is why a rejection
genuinely reverts rather than patching over.

The issues page can also decide a whole filtered set at once, in three ways —
approve, reject, or **reset to pending**. A reset undoes *human* decisions only:
each issue returns to the verdict the processing run gave it (auto-fixed where
the engine applied a confident fix, pending otherwise), and manual correction
records are reverted. Setting everything bluntly to "pending" would instead
claim the engine had never fixed anything.

That goes through
a single bulk request (`PATCH /api/issues` with a list of ids) rather than one
call per row, and triggers **one** rebuild for the batch — approving 400 issues
costs one request and one rebuild, not 400 of each.

Rebuilds are asynchronous and coalesced. A reviewer working down a list would
otherwise wait on a download-patch-upload cycle per click, or start a dozen
overlapping runs. Instead the first request starts a rebuild and later ones mark
it stale; one follow-up run then converges on the latest set of decisions. The
route returns `rebuild: "started" | "coalesced" | "unavailable"`, and a service
that is down never fails the review — the decision is already saved and the
document can be rebuilt later with `POST /jobs/{jobId}/rebuild`.

Each rebuild appends a line to the job's `logs`, so the UI's log panel shows when
the corrected document last changed.

Two rules keep the loop honest under concurrent use:

* **The reviewer owns `status`.** A rebuild reads the issue list, runs for
  several seconds, then saves. If it wrote statuses back it would overwrite any
  decision made in that window — the click would silently vanish from the queue.
  Rebuilds therefore update everything about an issue *except* its status.
* **Unchanged issues are not rewritten.** Each document carries a digest of its
  own content, so a rebuild touches only what actually changed instead of
  restamping hundreds of rows and losing when each was really reviewed.

## Delivery conventions (publisher's instructions)

Every image the pipeline **adds** to an HTML follows the delivery spec:

* Downloaded deliverables reference added figures as
  `IMAGE_URL_BASE` + `kerla_new_NN.png` (defaults:
  `https://d1xu9delcvinxy.cloudfront.net/kerala_v2/html-images/` and prefix
  `kerla_new_` — override with `IMAGE_URL_BASE` / `IMAGE_NAME_PREFIX`).
  The stored copies keep their hosted sources so in-app previews render; the
  rewrite happens when the file is downloaded (not with `?inline=1`).
* Numbering is **continuous for the whole book** and never restarts per
  chapter. Set the job's start with
  `PATCH /api/jobs/{id}` body `{"action": "set-image-start", "imageStartNumber": 4}`
  before processing chapter 2 when chapter 1 ended at `kerla_new_03`.
* Right after naming, each figure is **pushed to the publisher's CDN upload
  service** (`CDN_UPLOAD_URL`, multipart POST field `files`; set it to "" to
  disable). Confirmed uploads make the stored documents reference the delivery
  URLs directly, so previews and deliverables both resolve with no manual
  step. A push failure is logged, never fatal.
* `GET /jobs/{jobId}/image-bundle` (proxied as `/api/jobs/{id}/images`, and the
  job page's **Images** button) returns a zip of the added figures under their
  delivery names plus a `manifest.txt` — the manual fallback when the upload
  service was unreachable (entries already pushed are marked in the manifest).
* `POST /compare-structure` with `{"htmlA": <url-or-html>, "htmlB": ...}`
  checks that the English and Malayalam renditions of a chapter share one
  section count/sequence, question numbering and per-section image counts, and
  lists every divergence.
