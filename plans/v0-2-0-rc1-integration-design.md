# v0.2.0-rc.1: integrating the Phase 3.5 overnight work

Status: approved in conversation on 2026-10-01; this document is for review before the implementation plan.

## Goal

Ship `v0.2.0-rc.1` to testers through the download site. It includes the "Pro studio tool" redesign, PowerPoint review, PDF review, and the fixes listed below. The work is assembled from nine overnight branches in this repo and in `sanctum-desktop`. It is merged step by step so that every step is a working app and the product owner reviews each step as a small PR.

## Inputs

| Repo | Branch | Disposition |
|---|---|---|
| sanctum | `overnight/pptx` | Merge (step E1) |
| sanctum | `overnight/pdf-engine` | Merge (step E2) |
| sanctum | `overnight/pdf-view` | Not merged. Its placeholder PDF reader is superseded by pdf-engine. |
| sanctum | `plan/phase-3-5-formats` | Already on `release/0.2.0` (the plan this builds on) |
| sanctum-desktop | `overnight/ux-studio` | Merge as the new UI base (step D1) |
| sanctum-desktop | `overnight/ux-native` | Only the menu bar is ported (step D3) |
| sanctum-desktop | `overnight/pptx` | Ported onto studio (step D4) |
| sanctum-desktop | `overnight/pdf-view` | Ported onto studio (step D5) |
| sanctum-desktop | `overnight/ux-editorial` | Not merged |

Unmerged branches stay on GitHub for reference. The lane reports are in `~/Desktop/sanctum-overnight/reports/<lane>/REPORT.md`.

## Product decisions

1. **UI:** "Pro studio tool" (ux-studio) is the base: sidebar, document canvas, inspector, ⌘K palette and bulk actions. From ux-native, only the real menu bar is borrowed. Its window chrome and close-review sheet are not part of this release.
2. **Scope:** redesign + PowerPoint + PDF, all in this release.
3. **Fixes from studio's open items, all in:**
   - a per-session engine lock;
   - the findings list as an overlay at 1024 px;
   - the old launch bugs (409s from the Recents cleanup, inlined fonts blocked by the CSP);
   - the Redact button state.
4. **Leak check:**
   - It blocks the save, and the app explains why and offers "Redact these too".
   - Very short originals (1–2 characters, or numbers under 3 digits) only match as whole tokens.
   - An accepted value that also appears somewhere unreviewed still counts as a leak.
   - Matching stays case-sensitive.
5. **Detection gaps, both fixed:**
   - an email recognizer that runs before NER, with overlapping spans merged;
   - PDF names broken across lines (detect on paragraphs, then map back to lines).
6. **Hidden data:** .docx and .pptx outputs have their core and app properties cleared and their comments removed, matching what PDF already does. Unscanned PPTX parts (charts, SmartArt, OLE objects, masters and layouts) stay flagged to the user and are not scanned in this release.
7. **Version:** `0.2.0-rc.1`. It is published as a normal, non-pre-release GitHub release, as rc.3 was, because the site reads `releases/latest`.
8. **Smaller defaults:**
   - Text the user marks by hand gets the session's replacement style, not `<USER_ADDED>`.
   - The sensitivity presets stay at 0.20 / 0.35 / 0.60. The release notes say they are untuned.
   - The "I have reviewed every finding" attestation stays.

## Approach

Each repo gets an integration branch, `release/0.2.0`. Every step below is one PR into it and passes that repo's gate. `main` is untouched until the end, when one PR per repo merges `release/0.2.0` into `main` and the release is cut.

### Engine (`sanctum`)

| Step | Change | Key points |
|---|---|---|
| E1 | Merge `overnight/pptx` | No conflicts with main. |
| E2 | Merge `overnight/pdf-engine` | Resolve conflicts in `api/routes/review_sessions.py`, `api/schemas.py`, `schema/openapi.json` and `scripts/generate_openapi.py`. Both branches implement `GET /review-sessions/<id>/layout`, and pdf-engine's returns 501 for non-PDF. This becomes one route that dispatches by format to the PDF or PPTX layout builder. The layout contract types merge as a superset: the pptx additions (`textbox.anchor`, `image.alt`, `page.notes`, `unscanned[].page`) plus the PDF `textline`. The optional `font` field that pdf-view's desktop reads is checked against the real reader and either added or made optional on the desktop side. |
| E3 | Fix the SIGTERM integration tests | They fail because they need a `sanctum` executable on PATH. Fix the harness, for example by invoking the module through the venv's Python, so the suite is fully green from here on. |
| E4 | Per-session lock | Serialise load → mutate → save for each session id in the review-session service or store. Test: concurrent decision updates on one session, with no lost writes. |
| E5 | Leak-check refinements | Whole-token matching for short originals as in decision 4. The 422 `details` gain an occurrence count per value, `{"leak": "<value>", "occurrences": n}`. Values are still never logged. |
| E6 | Detection | Add an email pattern recognizer with higher priority than NER, and merge overlapping or contained spans so `<URL>` fragments disappear. For PDF: run analysis on paragraph text built from consecutive lines, then project the spans back onto `page{i}/line{j}` segments, splitting a span that crosses lines into per-line pieces that share one decision group. Tests use the fixtures where misses were seen: `rich_letter.pdf` ("Dr Evelyn / Marchetti", "Priya / Raghunathan") and the synthetic pptx deck's emails. |
| E7 | Hidden data and leak check for docx/pptx | Clear core/app properties and remove comments in both writers. Implement `extract_text()` on the docx and pptx writers so they join the post-write leak check through the existing `OutputTextExtractor` port. |
| E8 | Marked-by-hand replacement | User-added spans use the session's replacement parameters instead of `<USER_ADDED>`. |

Engine gate for every step:
- the full pytest suite passes, with no allowed failures from E3 on;
- `ruff check`, `ruff format --check` and `mypy sanctum` are clean;
- `schema/openapi.json` is regenerated and committed when the API changes.

### Desktop (`sanctum-desktop`)

| Step | Change | Key points |
|---|---|---|
| D1 | Merge `overnight/ux-studio` | Fast path, no conflicts with main. |
| D2 | Test-harness isolation | `tools/launch.mjs` passes `--user-data-dir` under the test HOME so concurrent runs cannot share `settings.json`. It lands right after D1 because every later walkthrough depends on it. |
| D3 | Native menu bar | Port the `Menu` template and the single `sanctum:menu-command` IPC channel from ux-native. Commands (Open ⌘O, Close ⌘W, Save Redacted Copy ⌘S, Settings ⌘,, Undo ⌘Z, …) route to studio's existing action handlers, sharing code with the ⌘K palette where they overlap. Keep the security settings (sandbox, contextIsolation, no nodeIntegration, CSP unchanged). |
| D4 | PowerPoint review on studio | Port `PptxView`, `review/pptx-render.ts`, `review/use-review-surface.ts`, and `.pptx` support in DropZone, App and the API client. Studio's sidebar groups findings by slide for pptx sessions. Slide thumbnails, notes and alt-text panels are restyled with studio's tokens. |
| D5 | PDF review on studio | Port `PdfView`, pdfjs-dist 6.3.289 (bundled, worker local), the `/layout` client, thumbnails and zoom into studio's canvas and toolbar. Verify against the E2 engine, not the placeholder. |
| D6 | Leak-check flow | On a 422 from commit, show a sheet listing each remaining value with its occurrence count. "Redact these too" adds them as user findings (through the existing user-added decision endpoint) and retries the commit. Cancel returns to review. |
| D7 | Studio fixes | Findings list as an overlay at narrow widths; Recents only discards sessions that are still open (no 409s); fonts load without CSP violations (no inlined data-URI fonts); the Redact button reflects the redacted state. |
| D8 | Version and notes | `package.json` → `0.2.0-rc.1`; release notes covering what's new and the known issues below. |

Desktop gate for every step:
- `npm run typecheck`, `npm run lint`, `npm test` and `npm run build` pass;
- the e2e smoke test passes;
- a real-app walkthrough runs against the `release/0.2.0` engine with an isolated user-data dir. The .docx flow is walked on every step, and each format from the step that adds it.

## Review workflow

- Each step is a PR into `release/0.2.0`. Its description gives:
  - what changed and why, tied to the decision it implements;
  - the exact checks run and their results;
  - before/after screenshots for visual changes;
  - every judgement call made without asking, flagged for review.
- PRs are opened in order. The product owner approves or comments, then the PR is merged into `release/0.2.0`.
- Engine and desktop steps can interleave. D5 depends on E2, and D6 depends on E5.

## Release gate and shipping

1. **Release-candidate walkthrough** in the real app with the bundled engine. A .docx, a .pptx and a PDF are each opened, reviewed (including a bulk action), taken through a leak-check refusal and "Redact these too", and saved. Each saved file is then checked for surviving originals, metadata and comments.
2. **Merge to main:** one PR per repo, `release/0.2.0` → `main`, merged by the product owner.
3. **Cut the release:** run the `release.yml` workflow with version `0.2.0-rc.1`, published as a normal release, then confirm all six assets are present and the download site shows `v0.2.0-rc.1`.
4. **Optional:** a `sanctum-site` PR updating the copy to mention Word, PowerPoint and PDF.

## Known issues to list in the release notes

- The PowerPoint view is approximate: no theme colours, gradients or rotation, and charts, SmartArt and OLE objects appear as placeholders.
- Not scanned in PowerPoint: chart text, SmartArt, OLE objects, and text on masters and layouts. These are flagged in the app.
- PDF:
  - Pages with redactions are flattened to images at 200 dpi, so they lose links, form fields and tagged structure. Untouched text on those pages stays searchable.
  - The replacement text is always Helvetica.
  - Rotated text near a redaction can be erased.
  - Text drawn as vector paths, and text inside images, is not detected.
  - Scanned PDFs are not supported (no OCR).
- The sensitivity presets are untuned.

## Out of scope

- ux-native's window chrome and close-review sheet; ux-editorial.
- Scanning PowerPoint masters, layouts and charts.
- OCR; palette encoding and choosing the dpi for flattened pages.
- Code signing changes.
