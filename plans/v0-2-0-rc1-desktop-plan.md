# v0.2.0-rc.1 Desktop Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Assemble the desktop half of `v0.2.0-rc.1` on `release/0.2.0`. Studio is the base. Onto it go the native menu bar, PowerPoint and PDF review restyled for studio, the leak-check sheet, linked findings, studio's open fixes and the version bump.

**Architecture:** Electron + React + Zustand renderer, with the Python engine as a sidecar. Each task is one PR from a short-lived `integrate/dN-*` branch into `release/0.2.0`. The format views are *ported*, not merged. Their files are checked out from the overnight branches, then their integration points (App, DropZone, sidebar, CSS) are rewritten against studio's components.

**Tech Stack:** Electron 33, electron-vite 3, Vite 6, React, TypeScript, Zustand, Vitest, Playwright (`_electron`), pdfjs-dist 6.3.289.

**Spec:** `plans/v0-2-0-rc1-integration-design.md` in the `sanctum` repo. Engine counterpart: `plans/v0-2-0-rc1-engine-plan.md` in the same repo; this plan is also kept there. Tasks D5–D7 need the engine's `release/0.2.0` with E2, E5 and E6 merged respectively.

## Global Constraints

- Work in `/Users/filippo/Desktop/sanctum-desktop` (a separate worktree per task is fine; run `npm ci` in a new worktree).
- Security settings stay as they are:
  - `sandbox: true`, `contextIsolation: true`, `nodeIntegration: false`, `webSecurity: true`;
  - the CSP in `src/renderer/index.html` is not loosened;
  - no new runtime network calls;
  - the only new dependency allowed is `pdfjs-dist@6.3.289`, already chosen by the pdf-view lane.
- Only the `replace` operator is exposed. The mapping store and per-detection operator picker stay hidden.
- Gate for every task (all must pass before the PR):
  - `npm run typecheck && npm run lint && npm test && npm run build`
  - `env -u ELECTRON_RUN_AS_NODE npx playwright test` (e2e smoke)
  - a real-app walkthrough against the engine's `release/0.2.0` checkout:
    - `SANCTUM_REPO=/Users/filippo/Desktop/sanctum`, with the engine branch checked out there or in a worktree that `SANCTUM_REPO` points to;
    - launched with an isolated user-data dir (Task D2);
    - always walks the .docx flow: open `~/Desktop/sanctum-overnight/fixtures/nda_contract.docx`, review with keys and one bulk action, Settings, save;
    - plus each format added so far (.pptx from D4, PDF from D5);
    - screenshots go in the PR body.
- PR protocol for every task:
  1. `git fetch origin && git switch -c integrate/<task-slug> origin/release/0.2.0`
  2. Commit in small steps. Messages are imperative sentences ending in `(v0.2.0-rc.1 D<n>)`, plus the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  3. `git push -u origin integrate/<task-slug>`, then `gh pr create --base release/0.2.0 --title "<D-n: title>" --body-file <file>`. The body has sections **What and why** (link the spec decision), **How it was checked** (exact commands and results, walkthrough screenshots) and **Judgement calls**. It ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
  4. Wait for the product owner's approval, then `gh pr merge --merge`.
- Never push to `main`, never force-push, never merge without approval.

## Review Focus

1. **Opening a second document while a review is open** (⌘O from the new menu, or a drop) → the same "discard or keep?" flow as studio's close button, never a silent discard. Test in D3.
2. **A tail piece of a linked finding is clicked in the document** → focus lands on the group's single row and the whole name is highlighted as focused. Test in D7.
3. **"Redact these too" when one remaining value appears in a segment the reviewer already decided** → the new finding is added and the retry still succeeds, with no duplicate rows and no 409. Test in D6.
4. **A PDF whose page sizes differ** (A4 and Letter in one file) → highlights stay aligned on every page at every zoom. Test in D5.
5. **The app relaunches with a settings file written by rc.3** → studio settings load with defaults for new fields, and nothing crashes. Test in D2.

---

### Task D0: Publish the integration branch

**Files:** none. This plan lives in the `sanctum` repo (`plans/v0-2-0-rc1-desktop-plan.md`) next to the spec, because `docs/superpowers/` is gitignored in this repo.

- [ ] **Step 1: Create and push the branch**

```bash
cd /Users/filippo/Desktop/sanctum-desktop
git fetch origin
git switch -c release/0.2.0 origin/main
git branch --unset-upstream   # created from origin/main; it must not track main
git push -u origin release/0.2.0
```

---

### Task D1: Merge `overnight/ux-studio` and fix its smoke test

**Files:**
- Merge only, plus `tests/e2e/smoke.spec.ts`

**Interfaces:**
- Produces studio's components: `Sidebar`, `Inspector`, `ReviewToolbar`, `CommandPalette`, `ConfirmDialog`, `SettingsView` and `Icon`. Also `review/bulk.ts` (`idsOfType`, `decideAllPendingOfType`, `countDetections`) and `review/entities.ts`, and the action registry consumed by the palette (in `App.tsx`, the `keywords:`-tagged command list).

- [ ] **Step 1: Branch and merge**

```bash
git fetch origin && git switch -c integrate/d1-studio origin/release/0.2.0
git merge --no-ff origin/overnight/ux-studio -m "Adopt the studio redesign (v0.2.0-rc.1 D1)"
```

Expected: no conflicts.

- [ ] **Step 2: Run the smoke test.** `npm run build && env -u ELECTRON_RUN_AS_NODE npx playwright test`. Expected: FAIL, because it still asserts `h1` = "Sanctum Desktop" and `.tagline`, which studio removed.

- [ ] **Step 3: Point the smoke test at studio's home screen.** Replace the two assertions:

```ts
  // Both strings are rendered by components/DropZone.tsx (studio home screen).
  // The heading names the accepted formats, which D4/D5 extend, hence the regex.
  await expect(win.getByRole('heading', { level: 1 })).toHaveText(/^Drop a .+ to review$/)
  await expect(win.getByText(/never leaves this computer/)).toBeVisible()
```

- [ ] **Step 4: Gate.** Expected: all pass.

- [ ] **Step 5: PR** (`D1: Adopt the studio redesign`). The body links the product owner's choice of direction C and the studio lane report.

---

### Task D2: Isolate Electron user data in tests and walkthroughs

**Files:**
- Modify: `tests/e2e/smoke.spec.ts`
- Create: `scripts/launch-isolated.mjs`
- Test: `tests/unit/main/settings.test.ts` (extend)

**Interfaces:**
- Produces: `node scripts/launch-isolated.mjs [--home <dir>]`. It launches the built app with `--user-data-dir=<home>/userData` and `HOME=<home>`, prints `{"pid":…,"userData":…}` on stdout, and every later walkthrough uses it.

- [ ] **Step 1: Failing settings test.** Add a case to `tests/unit/main/settings.test.ts`: load a settings JSON in rc.3's shape (copy the field set from `origin/main:src/main/settings.ts` defaults) and assert it parses into studio's settings with defaults for every new field and no throw.

- [ ] **Step 2: Run it.** If it already passes, keep it as a regression test. If it fails, fix the loader in `src/main/settings.ts` to merge the parsed file over defaults field by field.

- [ ] **Step 3: Isolate the smoke test.** In `smoke.spec.ts`:

```ts
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

  const home = mkdtempSync(join(tmpdir(), 'sanctum-e2e-'))
  const app = await electron.launch({
    args: [resolve(__dirname, '../../out/main/index.js'), `--user-data-dir=${join(home, 'userData')}`],
    env: { ...process.env, HOME: home, ELECTRON_DISABLE_SANDBOX_WARNING: '1', SANCTUM_SKIP_SIDECAR: '1' },
  })
```

- [ ] **Step 4: Create `scripts/launch-isolated.mjs`** by copying `~/Desktop/sanctum-overnight/reports/ux-native/_scripts/launch-isolated.mjs`, which the native lane already proved. Make it standalone: no paths into `sanctum-overnight`, it reads `SANCTUM_REPO` from the environment, and it defaults `--home` to a fresh `mkdtemp`. Add a short header comment saying why it exists: Electron resolves `userData` under `~/Library/Application Support/Electron` regardless of `HOME`, so concurrent runs shared one `settings.json` during the overnight run.

- [ ] **Step 5: Gate, then PR** (`D2: Give every test and walkthrough its own Electron user data`).

---

### Task D3: Native menu bar

**Files:**
- Modify: `src/main/index.ts`, `src/preload/index.ts`, `src/preload/sanctum.d.ts`, `src/renderer/src/sanctum.d.ts`, `src/renderer/src/App.tsx`
- Create: `src/main/menu.ts`
- Test: `tests/unit/main/menu.test.ts` (create)

**Interfaces:**
- Consumes: studio's command handlers in `App.tsx`: open picker, request close, open the commit panel, open settings, undo, toggle sidebar and the palette.
- Produces: `buildMenuTemplate(send: (cmd: MenuCommand) => void, platform: NodeJS.Platform): MenuItemConstructorOptions[]` and `type MenuCommand = 'open' | 'close' | 'save' | 'settings' | 'undo' | 'toggle-sidebar' | 'palette'`. Preload exposes `window.sanctum.onMenuCommand(cb: (cmd: MenuCommand) => void): () => void`.

- [ ] **Step 1: Failing test** `tests/unit/main/menu.test.ts`:

```ts
import { describe, expect, it, vi } from 'vitest'
import { buildMenuTemplate } from '../../../src/main/menu'

function find(template: Electron.MenuItemConstructorOptions[], label: string) {
  for (const top of template) {
    const items = (top.submenu ?? []) as Electron.MenuItemConstructorOptions[]
    const hit = items.find((i) => i.label === label)
    if (hit) return hit
  }
  throw new Error(`no menu item ${label}`)
}

describe('menu', () => {
  it('wires the document shortcuts to renderer commands', () => {
    const send = vi.fn()
    const t = buildMenuTemplate(send, 'darwin')
    for (const [label, accel, cmd] of [
      ['Open…', 'CmdOrCtrl+O', 'open'],
      ['Close', 'CmdOrCtrl+W', 'close'],
      ['Save Redacted Copy…', 'CmdOrCtrl+S', 'save'],
      ['Command Palette…', 'CmdOrCtrl+K', 'palette'],
    ] as const) {
      const item = find(t, label)
      expect(item.accelerator).toBe(accel)
      ;(item.click as () => void)()
      expect(send).toHaveBeenLastCalledWith(cmd)
    }
  })

  it('puts Settings in the app menu on macOS', () => {
    const t = buildMenuTemplate(vi.fn(), 'darwin')
    expect(find(t, 'Settings…').accelerator).toBe('CmdOrCtrl+,')
  })
})
```

- [ ] **Step 2: Run.** Expected: FAIL (module missing).

- [ ] **Step 3: Create `src/main/menu.ts`** by porting the template from `origin/overnight/ux-native:src/main/index.ts` (search for `Menu.buildFromTemplate`). Change three things:
  - export it as `buildMenuTemplate(send, platform)`;
  - remove the native-only items (Show Original / Show Redacted);
  - add `Command Palette… ⌘K` under View.

  Edit's Undo item must send `'undo'` and must *not* use the `role: 'undo'` default, because studio's undo reverts a review decision. Keep `role` items for cut, copy, paste, selectAll, hide, quit, minimize and zoom.

- [ ] **Step 4: Wire it up.**
  - In `src/main/index.ts`, after the window is created: `Menu.setApplicationMenu(Menu.buildFromTemplate(buildMenuTemplate((cmd) => win.webContents.send('sanctum:menu-command', cmd), process.platform)))`.
  - In preload, add `onMenuCommand` with `ipcRenderer.on('sanctum:menu-command', …)`. It returns an unsubscribe function and validates `cmd` against the `MenuCommand` list before calling back.
  - In `App.tsx`, add a `useEffect` that subscribes and dispatches each command to the handler studio already uses for that action (the same handlers the command palette calls). `close` and `open` go through studio's existing confirm-before-discard flow (Review Focus 1). Remove studio's renderer-side `keydown` handling for any shortcut the menu now owns, so it can't fire twice. Grep `App.tsx` for `metaKey` and keep only the keys that aren't menu accelerators.

- [ ] **Step 5: Walkthrough check for Review Focus 1.** With a review open and one decision made, press ⌘O. Expected: studio's confirm dialog appears, and Cancel keeps the review. Screenshot it for the PR.

- [ ] **Step 6: Gate, then PR** (`D3: Add the native menu bar`).

---

### Task D4: PowerPoint review on studio

**Files:**
- Bring over from `origin/overnight/pptx`: `src/renderer/src/components/PptxView.tsx`, `src/renderer/src/review/pptx-render.ts`, `src/renderer/src/review/use-review-surface.ts`, `tests/unit/renderer/pptx-render.test.ts`
- Merge by hand: `src/renderer/src/api/types.ts`, `src/renderer/src/api/sessions.ts`, `src/renderer/src/App.tsx`, `src/renderer/src/components/DropZone.tsx`, `src/renderer/src/components/Sidebar.tsx` (studio's findings list), `src/renderer/src/index.css`
- Test: `tests/unit/renderer/pptx-grouping.test.ts` (create)

**Interfaces:**
- Consumes: the engine's `GET /review-sessions/<id>/layout` (pptx), already on engine `release/0.2.0` after E1.
- Produces: `SessionsClient.getLayout(id: string, signal?: AbortSignal): Promise<ReviewSessionLayout>`; layout wire types in `api/types.ts`; `groupDetectionsBySlide(detections: readonly Detection[]): { slide: number; detections: Detection[] }[]` (pure, in `review/pptx-render.ts` or a new `review/grouping.ts`).

- [ ] **Step 1: Bring the self-contained files over**

```bash
git fetch origin && git switch -c integrate/d4-pptx origin/release/0.2.0
git checkout origin/overnight/pptx -- \
  src/renderer/src/components/PptxView.tsx \
  src/renderer/src/review/pptx-render.ts \
  src/renderer/src/review/use-review-surface.ts \
  tests/unit/renderer/pptx-render.test.ts
```

- [ ] **Step 2: Port the API additions.** From `git diff origin/main...origin/overnight/pptx -- src/renderer/src/api/`, apply the layout types and `getLayout` to studio's `types.ts` and `sessions.ts` by hand. Keep studio's client shape (it serialises decision updates). Keep the field names exactly as the engine's `ReviewSessionLayoutResponse` (the pptx-side schema).

- [ ] **Step 3: Failing grouping test** `tests/unit/renderer/pptx-grouping.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { groupDetectionsBySlide } from '../../../src/renderer/src/review/pptx-render'

const d = (id: string, segmentId: string) =>
  ({ id, segmentId, start: 0, end: 1, text: 'x', entityType: 'PERSON', status: 'pending' }) as const

describe('groupDetectionsBySlide', () => {
  it('groups by the slide index in the segment id, in slide order', () => {
    const groups = groupDetectionsBySlide([
      d('a', 'slide2/shape0/p0/r0'),
      d('b', 'slide0/notes/p0/r0'),
      d('c', 'slide2/shape1/alt'),
    ])
    expect(groups.map((g) => [g.slide, g.detections.map((x) => x.id)])).toEqual([
      [0, ['b']],
      [2, ['a', 'c']],
    ])
  })
})
```

If the pptx lane already exported an equivalent function from its `DetectionSidebar.tsx` changes, move that logic here under this name instead of rewriting it.

- [ ] **Step 4: Implement `groupDetectionsBySlide`** (parse `/^slide(\d+)\//`, put non-matching ids in a trailing group with `slide: -1`, sort by slide). Run the test. Expected: PASS.

- [ ] **Step 5: Integrate into studio.**
  - **DropZone and the file picker:** accept `.pptx` (studio's `accept=".docx"` in `App.tsx` becomes `".docx,.pptx"`, and the drop handler's extension check gets the same change). The home heading becomes "Drop a Word or PowerPoint file to review" (D5 makes it "Drop a Word, PowerPoint or PDF file to review"). The smoke test's regex already allows this.
  - **App:** where studio renders `<DocxView …/>`, choose the view by extension. A `.pptx` renders `<PptxView …/>` with the same props studio passes to `DocxView` (`detections`, `focusedId`, `onRendered`, `onFocusDetection`, `onUnwrappable`), plus `sessionId` and the sessions client for `getLayout`.
  - **Sidebar:** for pptx sessions, studio's findings list renders slide headers ("Slide 3 · 4 to review") using `groupDetectionsBySlide`. The "to review" count uses `countDetections`.
  - **CSS:** port the pptx lane's `index.css` additions into a `/* PowerPoint review */` section. Replace its colours, fonts, radii and spacing with studio's CSS variables (read them from the `:root` block of studio's `index.css`). Keep the slide-geometry rules (percentage positioning, `cqw` font sizing, `container-type`) unchanged.

- [ ] **Step 6: Walkthrough** with `~/Desktop/sanctum-overnight/reports/pptx/synthetic_briefing.pptx` and `fixtures/internal_memo.pptx`. Check that:
  - all segments render;
  - the sidebar is grouped by slide;
  - Shift+A and the palette's bulk actions work;
  - the save succeeds.

  Then run `python3 ~/Desktop/sanctum-overnight/reports/pptx/verify_output.py <output>` with the engine venv. Expected: 0 leaks.

- [ ] **Step 7: Gate, then PR** (`D4: Review PowerPoint decks in the studio layout`), with screenshots of a slide under review and the grouped sidebar.

---

### Task D5: PDF review on studio

**Files:**
- Bring over from `origin/overnight/pdf-view`: `src/renderer/src/components/PdfView.tsx`, `src/renderer/src/review/pdf-layout.ts`, `src/renderer/src/review/pdfjs.ts`, `src/renderer/src/pdfjs-assets.d.ts`, `tests/unit/renderer/pdf-layout.test.ts`
- Merge by hand: `package.json` / `package-lock.json` (only `pdfjs-dist@6.3.289`), `api/types.ts`, `App.tsx`, `DropZone.tsx`, `index.css`

**Interfaces:**
- Consumes: D4's `getLayout` and layout types (the `textline` item kind); engine `release/0.2.0` with E2 merged (the real PDF reader).

- [ ] **Step 1: Bring files and dependency over**

```bash
git fetch origin && git switch -c integrate/d5-pdf origin/release/0.2.0
git checkout origin/overnight/pdf-view -- \
  src/renderer/src/components/PdfView.tsx src/renderer/src/review/pdf-layout.ts \
  src/renderer/src/review/pdfjs.ts src/renderer/src/pdfjs-assets.d.ts \
  tests/unit/renderer/pdf-layout.test.ts
npm install --save-exact pdfjs-dist@6.3.289
```

- [ ] **Step 2: Reconcile types.** D4 already brought in the layout types from the pptx side. Make sure `LayoutTextline` keeps `font?: string` as optional. The engine does not send it, and nothing may require it.

- [ ] **Step 3: Failing test for mixed page sizes (Review Focus 4).** Append to `tests/unit/renderer/pdf-layout.test.ts`:

```ts
it('maps points to pixels per page when page sizes differ', () => {
  const layout = {
    format: 'pdf',
    pages: [
      { index: 0, width: 595.28, height: 841.89, items: [line('page0/line0', 72, 72, 100, 12)] },
      { index: 1, width: 612, height: 792, items: [line('page1/line0', 72, 72, 100, 12)] },
    ],
    unscanned: [],
  }
  const a = boxFor(layout, 'page0/line0', { pageWidthPx: 1000 })
  const b = boxFor(layout, 'page1/line0', { pageWidthPx: 1000 })
  expect(a.left).toBeCloseTo((72 / 595.28) * 1000)
  expect(b.left).toBeCloseTo((72 / 612) * 1000)
})
```

`line` and `boxFor` stand for the helper and mapping function `pdf-layout.ts` already exports. Use its real names (read the file), and keep the assertions.

- [ ] **Step 4: Run it.** If it fails, fix the mapping to use each page's own `width`, not page 0's.

- [ ] **Step 5: Integrate into studio** the same way as D4: `.pdf` is added to accept, the view is chosen by extension, and the CSS goes in a `/* PDF review */` section using studio tokens. PdfView's own toolbar controls (zoom, thumbnails) move into studio's `ReviewToolbar` as a view-specific slot. Add a `children` or `extra` prop to `ReviewToolbar` if it has none, and render PdfView's zoom control there.

- [ ] **Step 6: Walkthrough** against the real engine with:
  - `~/Desktop/sanctum-overnight/reports/pdf-engine/samples/rich_letter.pdf`;
  - `fixtures/engagement_letter.pdf`;
  - a mixed-size PDF you generate with reportlab (A4 page + Letter page).

  At 100% and 150% zoom and fit-width, the highlights must sit on the text. Save. The output must open in Preview.

- [ ] **Step 7: Gate, then PR** (`D5: Review PDFs in the studio layout`), with a zoomed alignment crop in the body.

---

### Task D6: Leak-check sheet with "Redact these too"

**Files:**
- Create: `src/renderer/src/components/LeakSheet.tsx`, `src/renderer/src/review/leaks.ts`
- Modify: `src/renderer/src/components/CommitPanel.tsx`
- Test: `tests/unit/renderer/leaks.test.ts` (create)

**Interfaces:**
- Consumes: engine 422 body `{ error: string, details: { leak: string, occurrences: number }[] }` (E5); `SessionsClient.addUserAdded`; `ApiError` (`status`, `body`).
- Produces: `parseLeakError(err: unknown): LeakReport | null` with `type LeakReport = { leaks: { value: string; occurrences: number }[] }`; `findOccurrences(segments: ReadonlyMap<string, string>, value: string): { segmentId: string; start: number; end: number }[]`.

- [ ] **Step 1: Failing tests** `tests/unit/renderer/leaks.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { ApiError } from '../../../src/renderer/src/api/sessions'
import { findOccurrences, parseLeakError } from '../../../src/renderer/src/review/leaks'

describe('parseLeakError', () => {
  it('reads the 422 leak details', () => {
    const err = new ApiError(422, { error: 'x', details: [{ leak: 'Priya', occurrences: 2 }] }, 'HTTP 422')
    expect(parseLeakError(err)).toEqual({ leaks: [{ value: 'Priya', occurrences: 2 }] })
  })
  it('ignores other errors', () => {
    expect(parseLeakError(new ApiError(500, null, 'boom'))).toBeNull()
    expect(parseLeakError(new Error('x'))).toBeNull()
  })
})

describe('findOccurrences', () => {
  it('finds whole-word occurrences across segments', () => {
    const segs = new Map([
      ['page2/line4', 'Priya Raghunathan, Director'],
      ['page2/line5', 'Priyanka attended'],
    ])
    expect(findOccurrences(segs, 'Priya')).toEqual([{ segmentId: 'page2/line4', start: 0, end: 5 }])
  })
})
```

(Match `ApiError`'s real constructor signature in `api/sessions.ts`.)

- [ ] **Step 2: Run.** Expected: FAIL (module missing).

- [ ] **Step 3: Implement `review/leaks.ts`.** `parseLeakError` checks `err instanceof ApiError && err.status === 422` and that `details` is an array of `{leak: string, occurrences: number}`. `findOccurrences` uses the same word-edge rule as the engine (`(?<![\p{L}\p{N}])value(?![\p{L}\p{N}])` with the `u` flag, value regex-escaped). It skips spans that already have a detection in the store with the same `segmentId`/`start`/`end` (Review Focus 3).

- [ ] **Step 4: Implement `LeakSheet.tsx`** in studio's sheet/dialog style (reuse `ConfirmDialog`'s structure and CSS classes):
  - title "Some redacted text is still in the document";
  - one row per leak: "**Priya** — 2 more places";
  - buttons **Redact these too** (primary) and **Back to review**.

  **Redact these too**:
  1. For each value, calls `findOccurrences` over the session's segment texts (from the store's segment map; add a selector if there is none).
  2. For each hit, calls `client.addUserAdded(sessionId, {segment_anchor, entity_type: 'USER_ADDED', original, start, end})` one at a time, through the same path `actions.addMissed` uses, so the store, previews and undo stay consistent.
  3. Retries the commit. If a value has no hits in the segments (it survives in metadata or a part the reviewer can't see), the sheet says so and keeps the save blocked.

- [ ] **Step 5: Wire into `CommitPanel.tsx`.** In the `catch` around `client.commitSession`, `const leak = parseLeakError(err)`. If it is non-null, open `LeakSheet` instead of the generic error state.

- [ ] **Step 6: Walkthrough** on `rich_letter.pdf`: accept every finding, then reject one of the "Priya" findings so the value survives. Save, and the sheet shows "Priya — n more places". Press Redact these too, and the save succeeds. Screenshot the sheet.

- [ ] **Step 7: Gate, then PR** (`D6: Explain leak-check refusals and offer to redact what remains`).

---

### Task D7: Linked findings

**Files:**
- Modify: `src/renderer/src/review/types.ts`, `review/from-session.ts`, `review/store.ts`, `review/bulk.ts`, `review/actions.ts`, `review/click-focus.ts`, `components/Sidebar.tsx`, `components/Inspector.tsx`, `api/types.ts`
- Test: `tests/unit/renderer/linked-findings.test.ts` (create)

**Interfaces:**
- Consumes: engine proposals with `group_id`, `group_index` and `group_original` (E6). Deciding any piece decides the whole group server-side.
- Produces:
  - `Detection.groupId?: string`, `Detection.groupIndex?: number`, `Detection.groupText?: string`;
  - `headOf(detections, id): Detection` — the group's head piece, or the detection itself when it has no group;
  - `membersOf(detections, id): Detection[]`.

  Lists, counts and focus navigation operate on heads only.

- [ ] **Step 1: Failing tests** `tests/unit/renderer/linked-findings.test.ts`:

```ts
import { beforeEach, describe, expect, it } from 'vitest'
import { countDetections } from '../../../src/renderer/src/review/bulk'
import { sessionToDetections } from '../../../src/renderer/src/review/from-session'
import { useReviewStore } from '../../../src/renderer/src/review/store'

const piece = (id: string, seg: string, i: number) => ({
  detection_id: id, entity_type: 'PERSON', score: 0.9, original: 'x',
  segment_anchor: seg, start: 0, end: 1,
  group_id: 'g1', group_index: i, group_original: 'Jennifer Martin',
})

describe('linked findings', () => {
  beforeEach(() => useReviewStore.getState().clear())

  it('maps group fields and counts a group once', () => {
    const dets = sessionToDetections(sessionWith([piece('a', 'p0/r0', 0), piece('b', 'p0/r2', 1)]))
    expect(dets.map((d) => [d.id, d.groupId, d.groupIndex, d.groupText])).toEqual([
      ['a', 'g1', 0, 'Jennifer Martin'],
      ['b', 'g1', 1, 'Jennifer Martin'],
    ])
    expect(countDetections(dets).pending).toBe(1)
  })

  it('setStatus on any piece updates every piece', () => {
    useReviewStore.getState().setDetections(sessionToDetections(sessionWith([piece('a', 'p0/r0', 0), piece('b', 'p0/r2', 1)])))
    useReviewStore.getState().setStatus('b', 'accepted')
    expect(useReviewStore.getState().detections.map((d) => d.status)).toEqual(['accepted', 'accepted'])
  })

  it('focus navigation and focusing a tail land on the head', () => {
    useReviewStore.getState().setDetections(sessionToDetections(sessionWith([piece('a', 'p0/r0', 0), piece('b', 'p0/r2', 1)])))
    useReviewStore.getState().setFocused('b')
    expect(useReviewStore.getState().focusedId).toBe('a')
  })
})
```

`sessionWith(proposals)` is a local helper that builds a minimal `ReviewSessionResponse` (copy the shape from `tests/unit/renderer/from-session.test.ts`).

- [ ] **Step 2: Run.** Expected: FAIL.

- [ ] **Step 3: Implement.**
  - `types.ts`/`api/types.ts`: the optional fields.
  - `from-session.ts`: map `group_id`, `group_index` and `group_original`.
  - `store.ts`: `setStatus`, `setCustomReplacement` and `setPreview` apply to every member when the target has a `groupId`. `setFocused(id)` stores the head's id. `focusNext`/`focusPrev`/`focusNextPending` skip non-head pieces. Undo entries for a group restore every member.
  - `bulk.ts`: `countDetections` and `idsOfType` count or return heads only. `decideAllPendingOfType` decides heads; the engine expands each to its group.
  - `actions.ts`: `accept`/`reject`/`setCustomReplacement` send one request using the head id, then update the group through the store.
  - `click-focus.ts`: clicking any piece's highlight focuses the head.
  - `Sidebar.tsx` and `Inspector.tsx`: render heads only, labelled with `groupText ?? text`. Highlights still paint every piece. When the head is focused, every member's highlight gets the focused style (Review Focus 2).

- [ ] **Step 4: Run the tests, then the gate.** Expected: PASS.

- [ ] **Step 5: Walkthrough** with a .docx where "Jennifer Martin" is split into three runs. Generate it with python-docx as in the engine plan's `make_docx`. Expect one row reading "Jennifer Martin", both runs highlighted, and output reading `<PERSON>`. Then click the second run's highlight: the row focuses. Also check `rich_letter.pdf`: "Dr Evelyn / Marchetti" shows as one row.

- [ ] **Step 6: PR** (`D7: Show a finding split across runs or lines as one row`).

---

### Task D8: Studio fixes

**Files:**
- Modify: `src/renderer/src/components/Sidebar.tsx`, `src/renderer/src/index.css`, `src/renderer/src/App.tsx` (findings overlay toggle)
- Modify: `src/renderer/src/components/RecentSessions.tsx:79-90`
- Modify: `electron.vite.config.ts` (renderer `build.assetsInlineLimit`)
- Modify: `src/renderer/src/components/Inspector.tsx` (Redact button state)
- Test: `tests/unit/renderer/recent-sessions.test.ts` (extend), `tests/unit/renderer/inspector.test.ts` (create if absent)

- [ ] **Step 1: Recents only abandons open sessions.** Failing test in `recent-sessions.test.ts`: given stale sessions with statuses `open`, `committed` and `abandoned`, the cleanup calls `abandonSession` only for the `open` one. Then fix the filter at line ~90:

```ts
    stale.filter((s) => s.status === 'open').map((s) => client.abandonSession(s.id, signal).catch(() => undefined)),
```

Also correct the comment above it, which claims terminal sessions accept the call. They return 409.

- [ ] **Step 2: Fonts without CSP violations.** Vite inlines small font files as `data:` URIs, and the CSP's `font-src` blocks them. In `electron.vite.config.ts`, under `renderer.build`:

```ts
      // Fonts must be files: the CSP's font-src does not allow data: URIs.
      assetsInlineLimit: (filePath: string) => (/\.(woff2?|ttf|otf)$/.test(filePath) ? false : undefined),
```

Verify: `npm run build && grep -c "data:font" out/renderer/assets/*.css` prints `0`. Then launch the app: the devtools console has no CSP font errors.

- [ ] **Step 3: Redact button reflects state.** Failing test: render `Inspector` with a focused detection whose status is `accepted`. The Redact button has `aria-pressed="true"` and the `is-active` (or studio's equivalent) class, and its label reads "Redacted". Keep Original mirrors this for `rejected`. Then implement it in `Inspector.tsx`.

- [ ] **Step 4: Findings list at narrow widths.**
  - Below studio's sidebar-collapse breakpoint (read it from `index.css` `@media` rules), a "Findings (n)" button in `ReviewToolbar` toggles the findings list as an overlay panel anchored to the left edge.
  - The panel reuses `Sidebar`'s findings list component.
  - It closes on Esc, on outside click and on choosing a row (which focuses that finding).
  - Add a unit test for the toggle state if the logic lives in the store; otherwise cover it in the walkthrough at 1024×720 with a screenshot.

- [ ] **Step 5: Gate, then PR** (`D8: Fix studio's open issues before release`). The body has a before/after screenshot for each fix, and the console log from a fresh launch showing no 409s and no CSP errors.

---

### Task D9: Version, release notes, release

**Files:**
- Modify: `package.json` (`"version": "0.2.0-rc.1"`), `package-lock.json`
- Create: `docs/release-notes/0.2.0-rc.1.md` (or wherever `release.yml` reads notes from; check the workflow first)

- [ ] **Step 1: Bump the version** with `npm version 0.2.0-rc.1 --no-git-tag-version`.

- [ ] **Step 2: Write the release notes** for testers, in plain language:
  - **New:** the new design, PowerPoint and PDF review, the leak-check sheet, email on internal domains, names split across lines or formatting, and hidden data removed from Word/PowerPoint output.
  - **Fixed:** the five studio items.
  - **Known issues:** copied verbatim from the spec's "Known issues" section.
  - **How to install:** the same unsigned-app instructions as rc.3, including the corrected quarantine command (no `-r`).

- [ ] **Step 3: PR** (`D9: Prepare 0.2.0-rc.1`).

- [ ] **Step 4: Release gate** after D1–D9 and engine Tasks 1–11 are merged into both `release/0.2.0` branches.
  1. Run one walkthrough with the engine bundled the way the release workflow bundles it (`npm run build` plus the packaging step `release.yml` uses, then launch the packaged app via `scripts/launch-isolated.mjs`).
  2. In it, open, review (with a bulk action) and save a .docx, a .pptx and a PDF. The PDF goes through a leak refusal and "Redact these too".
  3. For each output, check that no original, metadata or comment survives (engine venv: `verify_output.py` for pptx, python-docx for docx, pdfplumber + pypdf metadata for PDF).
  4. Post the results to the product owner.

- [ ] **Step 5: Merge to main and release, only after the product owner approves.**
  1. Open the two PRs (`release/0.2.0` → `main` in each repo). The product owner merges them.
  2. Run `gh workflow run release.yml -f version=0.2.0-rc.1` in `sanctum-desktop`, then watch it with `gh run watch`.
  3. Confirm the release is **not** marked pre-release. The download site reads `releases/latest`, as it did for rc.3.
  4. Confirm all six assets are present and that `https://<site>/` shows `v0.2.0-rc.1`.
  5. Offer the optional `sanctum-site` copy PR ("Word, PowerPoint and PDF").
