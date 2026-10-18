# POPPYS AI: Screen-by-Screen UX Audit

Branch audited: `claude/fervent-babbage-jqo8r4`. Scope: `frontend/index.html`, `frontend/styles.css`, `frontend/js/*.js`, and the backend messages that reach the screen (`backend/main.py`, `backend/insight/engine.py`).
This audit covers documentation only. No product file was changed. It does not claim the product is ready for production.

## Summary

POPPYS AI has six top-level screens: Overview, New order, Saved orders, Ask AI, Database and Setup. Setup has eight subsections. A sign-in or first-run overlay sits in front of all of them, and the screens share one set of dialogs, a drawer, toasts and a busy indicator. The main path (Upload → Reading → Review → Check & approve → Saved) worked end to end in Chromium against the stand-in server. On the desktop it is clear: problems appear on the field they belong to, a "Show me the next one ›" button walks through them, and undo/redo works.

The most important problems found:

1. **A partial save is reported as a full success.** The Saved screen says "The order is saved in Oracle." Only a blue note at the bottom of the page, below the diagram, says that fabric, yarn and trims were **not** saved. The confirm dialog does not mention the excluded tables at all (runtime; `workspace.js` `showSaved`, line 692; `approve`, lines 668-670).
2. **Ask AI answers a question it misunderstood with a number.** Asked "how many table present in database", it showed **"4 orders."**, marked only by a "Low confidence — check" pill (runtime; `chat.js` `draw`, lines 85-100).
3. **Phone layouts break on the most complex screens.** The review screen measured 615 px wide and the layout check 518 px wide in a 390 px viewport, so the whole page scrolls sideways. The data dictionary also overflows by 8 px. On every screen the stacked navigation takes about 370 px before any content (runtime; `styles.css` lines 527-546; `admin.js` line 296).
4. **Ordinary users see raw Oracle table and column names, record IDs, SQL, hashes and JSON** in decision points: the save confirmation lists `BUYMAS`, `ORDSIZE` and others, every saved field shows `ORDMAS.DOCID`, and Audit history prints `plan_hash` and raw JSON.
5. **Role-based UI gaps.** A viewer is offered "＋ Upload an Excel order" and the drop zone, and is refused only after choosing a file. Admins reset passwords through a plain browser `prompt()`, and role changes or disabling an account happen with no confirmation (`app.js`; `workspace.js` line 58; `admin.js` lines 219-223).
6. **The browser Back button and the URL hash do not move between screens.** Only `history.replaceState` is used, and nothing listens for `hashchange` or `popstate` (`app.js` line 63; confirmed at runtime).
7. **Keyboard users cannot reach several actions that only respond to clicks:** Database rows, Ask AI column sorting, sheet cells in the layout check and the trim group rows.

Everything that depends on a real Oracle database, the read-only Oracle account, the local AI (Ollama) or Windows is **NOT VERIFIED**. The stand-in server reports "Oracle offline" in its health check while saving and querying still work, so some of the contradictions in its screens are artefacts of the stand-in (see §0.3).

---

## 0. Method, evidence and labels

### 0.1 Evidence sources
- **Code reading.** I read every frontend module completely: `app.js`, `util.js`, `api.js`, `dashboard.js`, `workspace.js`, `mapping.js`, `chat.js`, `chart.js`, `flowview.js`, `flow.js`, `explorer.js`, `graph.js` (the parts that matter for UX), `admin.js`, `index.html`, and the selectors in `styles.css` that matter for layout and focus.
- **Runtime.** I ran `tests/demo_server.py` on port 8791 with an empty temporary data folder and drove Chromium 1194 with Playwright at 1440×900 (desktop) and 390×844 (phone). Screenshots went to `/tmp/ux_audit/`, outside the repo, and none were committed. Accounts created: an admin, then `viewer1` (viewer) and `operator1` (operator), all with strong throwaway passwords. Fixture workbooks came from `tests/fixtures.py`: `merch_bom()`, `zen_kids_po()` and `unknown_matrix()`.
- **Page width.** I measured `document.documentElement.scrollWidth` against `clientWidth`. When scrollWidth is larger, the page scrolls sideways.

### 0.2 Status labels
IMPLEMENTED = code plus a test or my runtime check. PARTIALLY IMPLEMENTED. DOCUMENTED ONLY. NOT IMPLEMENTED. NOT VERIFIED = only checkable with real Oracle, Ollama or Windows.

### 0.3 Stand-in caveats (important for reading the runtime notes)
- In the stand-in, `/api/health` returned `database.connected: false` with "Local Oracle is not reachable…" (`main.py` `health()`, lines 255-276). The fake Oracle installed by `tests/fake_oracle.py` does not satisfy that check. Even so, previews reported `live: true`, approval succeeded, and Ask AI answered from the SQLite copy. As a result:
  - the sidebar and the Overview "Connections" card said **Oracle offline** while the "Saved in last 7 days" KPI said "Read from Oracle receipts";
  - the Ask AI header pill said "Oracle offline — only uploaded-file questions work" while each answer footer said "Live Oracle data";
  - the Ask AI check text said "on this computer's list of uploaded files". That text is produced for any non-Oracle dialect (`backend/insight/engine.py` line 216).
  These contradictions are **not** counted as product defects. How the real app behaves when Oracle is online or offline is **NOT VERIFIED**.
- The local AI was off, so the "Suggest with local AI" path and real "Ask about this file" answers are **NOT VERIFIED**.

---

## 1. App shell and reusable components

### 1.1 Shell (`index.html`, `app.js`)
- **Navigation.** The sidebar has `Overview`, `New order`, `Saved orders`, `Ask AI`, `Database` and `Setup` (`index.html` lines 18-28). `go()` toggles `.view` sections and calls `history.replaceState` (`app.js` lines 51-63).
- **Status lights.** "Oracle connected / Oracle offline" with "start Docker + oracle26ai", and "Local AI ready / Local AI off" with "optional · rules still work" (`app.js` lines 23-35). They refresh every 30 s (`app.js` line 138).
- **Account.** The account box shows avatar, username and role, plus a Sign out icon button with `aria-label="Sign out"`.
- **Skip link.** A skip link exists (`index.html` line 9; `styles.css` lines 28-29).
- **Navigation problems:**
  - **No real navigation history.** Only `replaceState` is used, with no `hashchange` or `popstate` handler. The grep finds one match, at `app.js:63`. At runtime, setting `#dashboard` while on `#history` left the Saved orders screen showing. Back leaves the app instead of returning to the previous screen.
  - **Nav buttons are not marked as current.** They are plain `<button>`s with a CSS `active` class and no `aria-current` (`app.js` line 52).

### 1.2 Toasts: `notify()` (`util.js` lines 17-24)
- **Behaviour.** At most 4 toasts. Errors stay 12 s and others 6 s, each with a ✕ dismiss button labelled "Dismiss". The container has `role="status" aria-live="polite"` (`index.html` line 160), so error toasts are announced politely and not as alerts.
- **Placement.** Fixed at bottom 104 px, right 24 px (`styles.css` line 110).
- **Runtime:**
  - A 12 s error toast ("Choose the buyer Excel file…") stayed on screen after a later successful upload. On the Review screen it covered the size-table header and the unit question.
  - On the phone, a success toast covered the sticky "Check & continue" bar.
  - Opening "Read this file differently" raised a **success** toast ("Found 8 order details…") even though the user had only opened a panel. The toast comes from `mapping.js` `propose`, line 162, triggered by `autoPropose`.

### 1.3 Busy indicator: `busy()` (`util.js` lines 26-32; `index.html` line 194)
- **What it shows.** A pill at bottom centre with a spinner and a message, such as "Reading the file and finding the order…", "Checking every value against Oracle masters and rules…" or "Saving in one transaction and reading every row back…". The element has `role="status"`.
- **Problem.** While busy, any second action is **silently dropped** (`if (state.busy) return;`, and `app.js` line 119 `if (!b || state.busy) return`). Buttons are not disabled and no "please wait" appears. The Database screen works around this with its own `run()` (`explorer.js` line 20).
- **Errors.** Any exception inside `busy()` becomes an error toast (`util.js` line 30), so many failures appear only in a toast that disappears after 12 s.

### 1.4 Dialogs
- **`confirmDialog()`.** Native `<dialog id="confirm">` with `Go back` and a primary button whose label varies (`index.html` line 171; `util.js` lines 44-47). Native dialogs give Esc-to-close and focus containment.
- **`modal()`.** `<dialog id="modal">` with a ✕ close button; clicking the backdrop also closes it (`app.js` line 116). It is used for the Ask AI table preview.
- **`drawer()`.** `<aside role="dialog" aria-modal="true">` (`index.html` line 162; `util.js` lines 49-54). Focus moves to ✕ on open (runtime: `activeElement` was `drawer-close`). Esc closes it (`app.js` line 117). However:
  - focus is **not trapped**: the background is not made inert;
  - focus is **not returned** to the element that opened the drawer.
- **Native `prompt()`.** Used for password reset (`admin.js` line 223); see §7.6.

### 1.5 Shared visual components
- **Status and kind pills.** `.pill ok/warn/err/info/brand/master/header` (`styles.css` lines 128-135).
- **Source chips.** `util.js` `source()`, lines 35-42, shows where a value came from: `D99001!C2` (sheet!cell), "Company default (Settings)", "Derived: 200 pieces ÷ 100 …", "entered", "automatic".
- **Form view.** `util.js` `formView()`, lines 64-67, shows the business label, the Oracle code `TABLE.COLUMN` in monospace, and the value.
- **Flowchart.** `graph.js` `renderGraph` draws an SVG with zoom buttons (`Zoom out`, `Zoom in`, `Fit the whole diagram in view`, `Reset the view`), keyboard support (+, −, 0, arrow keys) and a live region that announces zoom. Nodes are `role="button" tabindex="0"`. The same component serves the approval view, the Saved view, Ask AI and the Database relationship map.
- **Dead code.** `flow.js` (`renderFlow`) is never imported, and it imports a `record` export that `util.js` does not provide (`flow.js` line 4). Loading it would fail. Nothing references it today. Severity Low (maintenance).

### 1.6 Responsive rules (`styles.css`)

| Breakpoint | Effect |
|---|---|
| `max-width: 1200px` (lines 521-526) | review and chat become one column; the form grid has 3 columns; KPIs have 3 columns |
| `max-width: 920px` (lines 527-542) | sidebar becomes a top bar, nav wraps, status sub-text is hidden, the auth illustration is hidden, upload/explorer/layout check/form view become one column, the table list is capped at 300 px, the form grid has 2 columns, page padding is 16 px |
| `max-width: 560px` (lines 543-546) | form grid, KPIs and stat rows become one column; `.form-row` becomes one column |
| `max-width: 900px` (lines 754-756) | Overview grid becomes one column; KPIs have 2 columns |
| `1240px` / `920px` (lines 729-730) | layout check columns |
| `max-width: 640px` (line 784) | check names wrap |
| `prefers-reduced-motion` (line 547) | animations off |
| `pointer: coarse` (line 612) | larger diagram tool buttons |

- **Sideways scrolling inside elements.** Wide content scrolls inside `.tbl-wrap` (`overflow: auto`, line 179), `.grid-scroll` (line 479), `.sheet-grid` (line 328) and `.flow-wrap`/`.gviewport`. `minmax(0, 1fr)` is used to stop tables from widening the page (comment at line 533).
- **Missing breakpoints.** `.doc-bar`, `.screen-head .tabs`, the layout-check header and the data-dictionary inline grid have no small-screen rules. See the per-screen runtime results.

---

## 2. Sign-in and first-run setup

| Item | Finding |
|---|---|
| Purpose | Create the first administrator; afterwards, sign in. |
| Components | `#auth` overlay (`index.html` lines 174-193), `app.js` `session()` (lines 10-21), `bind()` auth handler (lines 96-104), `showPassword()` (lines 87-91). Backend: `main.py` `/api/session`, `/api/setup`, `/api/login`. |
| Controls | Username, Password, `Show`/`Hide` toggle (`aria-pressed`, `aria-label` "Show password"/"Hide password"), submit button `Create administrator →` (first run) or `Sign in →`. |
| Steps | First run: title "Set up POPPYS AI", text "Create the administrator account for this computer. You can add operators and approvers afterwards." Later: "Welcome back", "Sign in to prepare and approve ERP entries." |
| Error and validation states | Client: the browser's minlength message ("Please lengthen this text to 12 characters or more…", runtime). Server, shown in `#auth-error` (`role="alert"`): "That password is too easy to guess (a common password or a word from this product). Choose another one." (runtime); "Username or password is incorrect." (runtime, `main.py` line 184); "Too many wrong passwords. Wait N seconds, then try again." (`main.py` line 177); "Setup is already complete. Sign in." (line 164). If the server is unreachable: toast "Unable to reach the POPPYS AI server: …" (`app.js` line 144). |
| Loading | The submit button is disabled while the request runs (`app.js` line 97). There is no spinner. |
| Success | The overlay hides and Overview loads. |
| Responsive | At ≤920 px the illustration is hidden and the form is centred. No sideways scroll on the phone (runtime, 390 px). |
| Technical information | None. |
| Accessibility | Labels wrap the inputs, the error uses `role="alert"`, autocomplete is set (`new-password` on first run). Good. |
| UX problems | (a) The 12-character rule is enforced but never explained before submit; only the browser tooltip says it (Low). (b) The setup form has no "repeat password" field, so a typo in the admin password can lock the operator out (Low; `scripts/reset_login.py` exists, but that is outside the UI). |
| Status | IMPLEMENTED (runtime; backend covered by `tests/test_auth.py`). |

---

## 3. Overview (`dashboard.js`)

| Item | Finding |
|---|---|
| Purpose | See waiting drafts, recent Oracle saves and connection health. |
| Components | `#view-dashboard` (`index.html` lines 40-50); `loadDashboard()` (`dashboard.js` lines 8-34); `drawStatus()` (lines 36-46); `/api/overview`, `/api/health`. |
| Controls | `＋ Upload an Excel order` (goes to New order); `All saved orders →`; per queue row `Open →` (whole row clickable); per saved row `Show where it was saved`. |
| KPI tiles | "Needs attention / Orders with fields to correct", "Ready to approve / Checked against live Oracle", "Drafts / Uploaded, not yet checked", "Saved in last 7 days / Read from Oracle receipts" (or "—" / "Oracle offline") (lines 12-17). |
| Empty states | "Nothing waiting. Upload an Excel order to start." (line 27); "No orders have been saved to Oracle yet." (line 32); "Oracle is offline." fallback (line 28). |
| Error state | If `/api/overview` fails, the error message replaces the queue (line 10). |
| Connections card | "Oracle connected / Oracle offline" plus the backend message; "Ask AI account ready (read-only) / Ask AI account not ready" ("Needs Oracle first."); "Local AI ready / Local AI off" ("Optional — known layouts and questions still work"); "Ask AI accuracy: X of Y correct / Ask AI accuracy not measured yet" ("Setup → Answer accuracy → Run the check") (lines 40-44). |
| Runtime | Desktop and phone both render with no sideways scroll. After a save, the "Recently saved to Oracle" table listed the order. The page re-polls every 30 s while visible (`app.js` line 138). |
| Responsive | KPIs 4 → 2 columns at 900 px; grid 2 → 1 column. On the phone the stacked nav (six full-width buttons plus status) takes about 370 px before "Overview". |
| Technical information | Status details mention "Docker Desktop", "oracle26ai container", the schema owner "GARMENT_DEMO" and the read-only DB user name (lines 40-41; sidebar `app.js` line 29). This is useful to an administrator but means little to an operator. |
| Accessibility | Status uses coloured dots **plus** text (good). Queue rows are clickable `<tr>` elements, but each also has a real button (good). |
| UX problems | (a) A **viewer** sees "＋ Upload an Excel order" and "Upload an Excel order to start" even though the role cannot upload (runtime with `viewer1`; the server's `PERMISSIONS` gives a viewer only `view`, `main.py` lines 29-34) — Medium. (b) Queue status pills are colour-coded (`STATUS_PILL`, line 6), but the text label is always shown — OK. |
| Status | IMPLEMENTED (runtime, stand-in). Real Oracle KPI accuracy: NOT VERIFIED. |

---

## 4. New order (`workspace.js`, `mapping.js`, `flowview.js`, `graph.js`)

A stepper at the top shows `1 Upload — 2 Review — 3 Check & approve — 4 Saved` (`index.html` lines 55-60). `stage()` updates the title: "New sample order", "Reading the file", "Review the order entry", "Check before saving" or "Saved in Oracle" (`workspace.js` lines 31-36). The stepper items are not buttons, and the current step has no `aria-current`.

### 4.1 Upload stage

| Item | Finding |
|---|---|
| Purpose | Choose or drop one buyer file. |
| Components | `#stage-upload` (`index.html` lines 62-90); `upload()` (`workspace.js` lines 55-67); `recent()` (lines 43-53); drop handlers (`app.js` lines 107-112). |
| Controls | Drop zone `<label for=file-input tabindex=0>` ("Drag & drop the order file", "or choose a file from this computer"); Enter or Space opens the file picker (`app.js` line 109). The "Recent files" table has an `Open` button per row. |
| Hint text | "Any Excel file — .xlsx .xls .xlsm .xlsb .ods, Excel XML/HTML exports, CSV — or JSON · up to 12 MB · stays on this computer". |
| Errors | Wrong type: "Choose the buyer Excel file (.xlsx, .xls, .xlsm, .xlsb, .ods, CSV, Excel XML/HTML export) or a JSON file." (runtime, line 57). No permission: "Your role can view orders but not upload. Ask an administrator." (runtime as viewer, line 58). Several files dropped: "Drop one order file at a time." (`app.js` line 112). Server 415 message (`main.py` line 356). |
| Empty | "Your uploads will appear here." |
| Loading | Busy pill "Reading the file and finding the order…". |
| Responsive | One column at ≤920 px; no sideways scroll on the phone. |
| Technical information | The format name under the file (for example "Merch BOM", or "Needs column mapping" for an unknown layout, seen on the phone). The recent-files badge always says "XLS", even for CSV or JSON (line 47) — Low. |
| UX problems | (a) A viewer sees the drop zone and only learns afterwards that uploading is not allowed (Medium; see §3). (b) The 12 MB limit is not checked in the browser, so an oversized file goes to the server before it is refused (Low). |
| Status | IMPLEMENTED (runtime). |

### 4.2 Reading stage ("Here is what we found")

| Item | Finding |
|---|---|
| Purpose | Summarise what was read before the user opens the entry form. |
| Components | `showReading()` (`workspace.js` lines 78-102); `#stage-read` (`index.html` lines 92-99). |
| Controls | `Open the order entry →` (gets focus, line 101). |
| Rows (runtime, merch BOM) | "Opened: Excel workbook — 1 sheet · 238 filled cells read…"; "Layout recognised: Merch BOM — 1 order found"; "Order details found (7 of 7)"; "Size breakdown: 2 colour lines, 4 sizes — Buyer order 150 pcs · sample section 0 pcs · 100 packs × 2 = 200 pcs"; "Fabric & yarn: 3 fabric rows"; "Trims & accessories: 7 rows"; warning "Pieces or packs? The file does not say"; warning "2 fields not in the file: OC No / Order reference, Ord Confm Dt — They are outlined in red on the next screen — type them there." |
| Unknown layout | Title "We need one quick hint"; "This layout is new to POPPYS AI — A layout was proposed from the labels and values in the file. Check it once…" (runtime with `unknown_matrix` and `zen_kids_po`). |
| Refused template | "Saved layout “X” (version N) was not used — … so nothing from the saved layout was carried over." (line 81). |
| Accessibility | The icons ✓, ! and – carry the meaning alongside colour; each row has bold text. Good. |
| UX problems | The summary says "Buyer order 150 pcs", but the next screen opens on **"Sample section · 0 pcs"** with "0 pcs" in the side bar and two "All quantities are zero…" errors (runtime). The default quantity source is not stated here (Medium; see §4.3). |
| Status | IMPLEMENTED (runtime). |

### 4.3 Review stage (Axpert-style order entry)

| Item | Finding |
|---|---|
| Purpose | Correct and complete the order before checking it. |
| Components | `renderReview()` (`workspace.js` lines 146-166), `orderScreen()` (174-184), `orderTab()` (220-238), `field()` (213-218), `matrix()` (272-300), `fabricTab()` (314-344), `trimsTab()` (346-381), `renderSide()` (383-398), `nextIssue()` (408-425), undo/redo (444-470), `bindWorkspace()` (706-726). |
| Document bar | File name; format or saved-template name; "uploaded by … · version N"; status pill ("Draft", "Changes not checked yet", "Needs attention", "Checked · ready", "Saved to Oracle"); buttons `↶ Undo`, `↷ Redo` (titles "Undo (Ctrl+Z)" and "Redo (Ctrl+Y)"), `Ask about this file`, `Read this file differently`, `New upload` (line 156). |
| Order screen | Header "SAMPLE ORDER ENTRY"; tabs `Order entry`, `Fabric & yarn · N`, `Trims · N`, with red count badges; total pill "N pcs"; `Remove` when there are several orders. |
| Order header | 17 Axpert fields (`HEADER`, lines 9-16). A red `*` marks required fields. Each field has a source chip ("D99001!C2", "Company default (Settings)", "entered", "automatic", "Derived: …"). "Order No" is read-only: "Generated by Oracle on save". |
| Quantities | Segmented radio groups "Quantities to save *" (`Buyer order · N pcs`, `Sample section · N pcs`, `Type them`) and "Quantity unit *" (`Pieces`, `Packs`, plus "Pieces in one pack *"). Below them: the size matrix with editable size headers, a "＋ size" column, Total, "File total" with ✓ or ≠, `＋ Add colour line`, and the tip "Tip: type a new size name in “＋ size” to add a column." A `Notes from reading the file (N)` disclosure follows. |
| Fabric and trims tabs | Fabric: columns Garment line, Portion, Fabric colour, Quality, GSM, Dia, Content, g/pc, Rejection, Pieces, Kg to save, File kg, Yarn; `＋ Add fabric row`; yarn sub-table with `＋ Add yarn`. Trims: grouped rows, "Only rows that need attention" checkbox. |
| Side and action bar | Sticky at the bottom (`styles.css` line 344): "N places need your input / outlined in red" with `Show me the next one ›`, or "All checks passed", or "Changes made / Not checked yet — checking never writes to Oracle". Stats (pcs, colour lines, sizes, kg fabric, trim rows). Primary `Check & continue →` or `Open the approval view →`. It is disabled for roles without `preview` (line 397). |
| Validation messages (exact) | "Not in the file — please enter it."; "<Label> is required for every order — enter it."; "Choose which quantities this sample order uses."; "Are these quantities pieces or packs?"; "A pack holds at least 2 pieces — how many pieces are in one pack?"; "How many pieces are in one pack?"; "Enter the colour / combo."; "All quantities are zero — type the quantities or choose another quantity source." (lines 113-123). After a check, server messages appear on the same fields. Toast "5 things to fix — each one is outlined in red where it belongs." (line 625, runtime). |
| Banners | Error: "Before this can be saved" with issues not tied to a field (line 157). Warning: "Oracle is not connected, so existing masters and order numbers could not be checked. Start the database and press Check & continue again." (line 158). Info on the fabric and trims tabs: "These rows are checked and shown for review. They are not saved to Oracle yet because the ORDFAB / ORDYARN mapping is not verified. An administrator can verify it in Setup → Write mapping." (`refBanner`, lines 306-312; runtime). |
| Confirmations | `New upload` with unchecked edits: dialog "Discard your corrections?" with `Discard`. `Remove` order: "Remove this order?" — "It will not be imported. Undo brings it back." with `Remove`. Closing or reloading the tab with unchecked edits triggers the browser's own unsaved-changes prompt (`app.js` line 129). |
| Runtime | Merch BOM: 5 problems at first, because the sample section (0 pcs) was preselected. `Show me the next one ›` scrolled to and focused `OC No / Order reference`. After choosing `Buyer order · 150 pcs` and `Pieces` and typing the OC No and the date, the side bar showed "Changes made". Undo cleared the date and Redo restored it. ZEN KIDS (operator): after the layout was applied there were 4 problems; after filling fields, the check still asked "Choose which quantities this sample order uses." because no source had been preselected. |
| Responsive | Desktop is fine. **Phone: the page measured 615 px wide in a 390 px viewport.** The document bar's buttons spill outside the card, the tab labels wrap word by word ("Fabric & / yarn · / 0"), and the pcs pill is cut off (runtime `p_unknown_after_apply.png`). `.doc-bar` and `.screen-head` have no wrap or breakpoint rules (`styles.css` lines 253, 259). |
| Technical information | Cell references (`D99001!C2`) are appropriate for spreadsheet users. The ORDFAB, ORDYARN and ORDACC table names and the word "mapping" appear in the info banner shown to operators. Notes are written in reader language. |
| Accessibility | Good: inputs have `<label for>`, `aria-invalid` is set on wrong fields, the segmented controls use `role=radiogroup`/`radio` with `aria-checked`, matrix cells have `aria-label`s ("0-3M quantity line 1"), and errors are shown as text plus an outline, not by colour alone. Gaps: the tabs use `role=tab`/`tablist` without `aria-controls`, tabpanels or arrow-key support (line 180). Trim group rows react to Enter and Space (line 724) but are `<tr>` elements without `tabindex`, so the keyboard cannot reach them; the caret (▸) is the only cue. A wrong yarn percentage total is shown only in red text (line 341), with no message. |
| UX problems | (a) The default quantity source can be a zero-quantity sample section, which creates errors on a file that holds valid buyer-order quantities (Medium). (b) The phone overflow described above (Medium). (c) Unknown layouts open with no quantity source preselected, so one more click is needed after the check (Low). (d) `Ask about this file` is offered when the local AI is off and returns a stale message; see §4.5 (Low). |
| Status | IMPLEMENTED (runtime). |

### 4.4 "Read this file differently" / layout check (`mapping.js`)

| Item | Finding |
|---|---|
| Purpose | Show once where the order details and size table are in an unfamiliar layout; optionally save that as a buyer template. |
| Entry | Opens automatically when no order could be read; otherwise from `Read this file differently` (`workspace.js` lines 149, 156). |
| Controls | Header pill "Proposed by the field matcher"; `Suggest with local AI` (only when the AI is on) or `Find the fields for me`; `Close`. Per field: a cell input (placeholder "cell"), the value preview, a confidence chip `High`/`Medium`/`Low`/`Yours` with a tooltip explaining why, and `Pick`. `More fields (N)`. Size quantities: `Sizes across columns` / `One row per size`, Header row, column inputs plus `Pick`, `More columns (N)`. Quantity unit: `Pieces` / `Packs` / `Ask on review` plus "Pieces in one pack". Save section: "Save as this buyer’s layout template" checkbox, "Template name", version note, and the primary `Read the file with this layout →`. On the right: "Sheet / page" select, a legend (order detail, table header, columns read, not used) and the sheet grid (up to 80 rows × 30 columns, line 443). |
| Messages | "Click the … in the sheet · Esc cancels" (live region `#pick-hint`); "“X” is not a cell address such as B4."; "X is empty."; "X holds the label “…” — pick the value next to it."; "possible: E2 “…” Use it"; "Not used (N) — these proposals failed the checks"; "Choose the header row of the size table." / "Choose the first size column." / "The last size column must be to the right of the first."; toasts "Some choices are missing — they are outlined in red." and "This layout does not fit the file — the wrong choices are outlined in red."; success "Layout saved as “ZEN KIDS layout” (version 1). The next file with this layout is read automatically." or "Layout applied. Check the order details." (lines 260-273, 405-436; `workspace.js` line 608). |
| Version note | "Saved as a new layout template (version 1)…" or "Updates “X”: saved as version N+1 … Version N stays in the history (Setup → Layout templates)." (lines 380-390). |
| Runtime | On the merch BOM the matcher proposed 8 details, including `Ord Confm Dt` = E2 at Medium confidence, even though the recognised layout had left that field empty. On `unknown_matrix.xlsx`, whose buyer is also ZEN KIDS but whose layout differs, the default template name was "ZEN KIDS layout". Applying it **saved version 2 of the ZEN KIDS template**, replacing the different layout saved minutes earlier from `zen_kids_po.xlsx`. The only warning is the small version note. |
| Responsive | **Phone: the page measured 518 px wide in a 390 px viewport.** The title column is squeezed to about 100 px, and the sheet grid sits below the long form, so `Pick` → click a cell means scrolling a long way (runtime `p_unknown_mapping.png`). |
| Technical information | Confidence levels with "score NN%" in the tooltip (line 235); "the field matcher" / "the local AI"; spreadsheet coordinates. These suit the task, but "score" percentages are internal. |
| Accessibility | Good: labels, `aria-pressed` on `Pick`, a live pick hint, Esc cancels a pick, and tooltips reachable by focus (`tabindex=0` plus `aria-describedby`). Gap: sheet-grid cells are `<td>`s with click handlers only (line 478), so **picking by keyboard is impossible**. Typing the address is the workaround. The legend relies on colour and outline style (line 80, `aria-hidden`). |
| UX problems | (a) Template naming is buyer-based by default, so two different layouts from one buyer silently become versions of one template. Only the newest version is active, so the earlier layout stops being used (Medium; whether a later ZEN KIDS PO is then refused or mis-read was not tested). (b) The phone overflow (Medium). (c) A success toast appears just from opening the panel (Low). |
| Status | IMPLEMENTED for the matcher path (runtime). Local AI suggestion: NOT VERIFIED. |

### 4.5 "Ask about this file" drawer

| Item | Finding |
|---|---|
| Controls | Drawer "Ask about this file", textarea (placeholder "Why is the sample quantity zero?"), `Ask` (`workspace.js` lines 400-406). |
| Runtime | With the AI off, the answer was: "The local AI helper is offline. Everything else still works: the questions panel lists each issue with the Excel cell it came from." (`main.py` line 1589). |
| UX problems | The message points to a **"questions panel" that does not exist**. Problems are shown on the fields, as the comment at `workspace.js` lines 2-3 says (Low). The button is offered even when the sidebar already says "Local AI off" (Low). The textarea has no visible label, only a placeholder (Low, a11y). |
| Status | PARTIALLY IMPLEMENTED (offline path seen); AI answers NOT VERIFIED. |

### 4.6 Check & approve (approval view)

| Item | Finding |
|---|---|
| Purpose | Show exactly what will be written, then accept. |
| Components | `prepare()` (`workspace.js` lines 614-629), `showApprove()` (639-657), `flowCard`/`mountFlow`/`renderTable` (`flowview.js` lines 30-44, 71-106). |
| Controls | `← Back to the order entry`; KPI tiles "New records", "Oracle tables", "Existing masters reused", "Pieces", "Fabric (kg)"; flowchart "Where this order will be saved" (clicking a table shows its rows below); for reference-only tables, "See these rows in the review screen"; primary `Accept and save to Oracle →`. |
| Messages | Warning banner "Please read before approving" plus a list. At runtime: "Source order quantities will be imported as a DEMO SAMPLE order."; "Order size breakdown totals 150 pieces but the header says 200 pieces (100 packs × 2)."; "Accessory sizes matched to the order sizes: …". Offline banner: "Oracle is not connected. This view used the saved database structure…". Action text: "Accepting writes these 18 records in one Oracle transaction, reads every row back to confirm it, and records who approved it. If anything fails, nothing is saved." Disabled reasons: "Saving is disabled until Oracle is connected." / "Fix the outlined fields first." / "Your role cannot approve. An approver or administrator must accept this entry." (lines 646-654). |
| Runtime | 18 new records, 8 tables, 4 masters reused. The flowchart drew ORDFAB, ORDYARN and ORDACC as dashed nodes ("checked, mapping not verified · 3 rows · not saved yet"). The operator run did not reach this view because the quantity source was still unchosen (§4.3). |
| Responsive | KPIs reflow; the flowchart lives in a zoomable viewport. Phone not captured for this stage (NOT VERIFIED at 390 px). |
| Technical information | Node titles pair business names with raw table names (`ORDMAS`, `BUYMAS`, `SIZE_MAS`). Edge labels `ORDMASID`, `SUBNO`, `YDET`. Lane titles "ORDER RECORDS · linked by ORDMASID and line number (SUBNO)" (`flowview.js` line 41). Every field in the record view shows `ORDMAS.DOCID`-style codes. Chips "new ID on save", "new master", "ID 51". The remark value written to Oracle is "POPPYS demo sample import; quantity basis: order_demo", which puts an internal key into an ERP field. |
| UX problems | (a) The **excluded fabric, yarn and trims are not mentioned in the warnings or the action text**. They appear only as dashed diagram nodes (High, together with §4.7). (b) The warning "imported as a DEMO SAMPLE order" uses an internal basis name without explaining what the user should do (Low). |
| Status | IMPLEMENTED (runtime, stand-in). Real Oracle write and approval: NOT VERIFIED. |

### 4.7 Save confirmation dialog

- **Content (runtime).** Title "Save this order to Oracle?". Body: "PK/26-27/S/7777 · T1001 · TESTBUYER", then **"BUYMAS: 1 new record, STYLENUM: 1 new record, STYLEMAS: 1 new record, MERCHMAS: 1 new record, ORDMAS: 1 new record, ORDSTYLE: 2 new records, SIZE_MAS: 3 new records, ORDSIZE: 8 new records"**, then "This cannot be undone from POPPYS AI. Existing orders are never overwritten." Buttons `Go back` and `Yes, save to Oracle` (`workspace.js` lines 668-670).
- **Problems.**
  - The irreversible decision is described only in raw Oracle table names (Medium).
  - Rows that will **not** be saved (fabric, yarn, trims) are not listed (High; see §4.8).
- **Status.** IMPLEMENTED (runtime).

### 4.8 Saved stage

| Item | Finding |
|---|---|
| Components | `showSaved()` (`workspace.js` lines 685-704); read-back graph from `/api/history/{id}/graph`. |
| Content (runtime) | Eyebrow "SAVED AND VERIFIED"; title "The order is saved in Oracle."; "Approved by admin · date/time · 18 new records, 4 existing masters linked"; "PK/26-27/S/7777 · ORDMASID 1000000000005"; buttons `Open in Database` and `Start another order`; card "Where your data was saved — 10 tables · the values below come from a fresh SELECT, not from the save receipt" with the pill "✓ Verified in Oracle"; checkbox "Show every column Oracle returned (not only the ones POPPYS AI wrote)". **At the very bottom**, in blue: "Not saved (mapping not verified yet): Fabric Planning — fabric per garment / portion (3 rows), Fabric Planning — yarn split (3 rows), Accessories (trims) per order (7 rows)." |
| Other states | "Already saved" / "This order is in Oracle." (repeat); pill "⚠ Differences found"; "Could not read back from Oracle: …" with the pill "not verified" (lines 698-703). |
| Problems | **The success heading and the green tick claim the order is saved, while 13 of the rows the user reviewed were dropped.** The notice sits below a tall diagram and a record table, about 1,200 px down on the desktop (runtime `d_saved.png`) — High. `ORDMASID` and long record IDs appear in the main heading (Low). |
| Status | IMPLEMENTED (runtime, stand-in read-back). Real Oracle read-back: NOT VERIFIED. |

---

## 5. Saved orders (`app.js` `history()` and `receipt()`)

| Item | Finding |
|---|---|
| Purpose | List approved imports and show where each one was written. |
| Components | `#view-history` (`index.html` lines 104-109); `history()` (`app.js` lines 65-76); `receipt()` (lines 78-85), which reuses `showSaved(..., standalone)`. |
| Controls | Table columns File, Order, Approved by, Saved; per row `Show where it was saved` (the row is clickable too). The detail opens below the list and scrolls into view. |
| States | Loading "Reading saved imports from Oracle…"; empty "No orders have been saved yet."; error message in place of the table; detail loading "Reading the saved records…". |
| Runtime | One row (bom.xlsx, PK/26-27/S/7777, admin). The detail shows "ALREADY SAVED — This order is in Oracle." plus the diagram, and again the "Not saved (mapping not verified yet)…" note at the bottom. |
| Responsive | Phone: no page overflow, but the table scrolls inside its card and the only action button sits off-screen to the right (runtime `p_history.png`). Tapping the row works, but nothing signals that. |
| Technical information | Same as §4.8 (`ORDMAS.*` codes, ORDMASID, IDs). |
| UX problems | (a) There is no search, filter or paging; the list shows whatever `/api/history` returns (Low). (b) The detail appears below the list on the same page instead of on its own page, and there is no way to close it (Low). (c) Clicking `Show where it was saved` on the Overview switches screens and opens the detail, but because of §1.1 the browser Back button cannot return to the Overview. |
| Status | IMPLEMENTED (runtime, stand-in). |

---

## 6. Ask AI (`chat.js`, `chart.js`, `graph.js`)

| Item | Finding |
|---|---|
| Purpose | Ask plain-language questions about orders and see the answer, its source tables and its query. |
| Components | `#view-chat` (`index.html` lines 111-130); `loadChat`, `welcome`, `askQuestion`, `draw`, `preview`, `rerun`/`addColumn`/`removeColumn`, `bindChat` (`chat.js`); `answerChart` (`chart.js` lines 8-19). |
| Controls | Composer textarea (visually hidden label "Question", max 500 characters; Enter sends, Shift+Enter adds a new line) plus `Ask`. Starter buttons in the welcome card. Side cards "Try asking" (★ marks learned examples), "Your recent questions", "How answers stay accurate". Per answer: diagram (click a table to preview it, ＋ for linked tables), `Did you mean …` buttons, `Download CSV`, sortable headers, × to remove a column, the disclosure "How this was answered and checked", feedback `✓ Correct — remember it` / `✗ Not right`. Preview modal: tabs `Data preview` / `Columns (N)`, "Show empty columns too (N)", `＋ Add` per column, "Linked tables:" buttons. |
| States | Pending: three-dot typing indicator. Non-answer: pill "Cannot answer safely" or "Please rephrase", then the message and "For example:" suggestions (lines 85-89). At runtime, "show me something nice" → "Please rephrase — I could not find order data words in the question." Empty: "No matching rows.". Error: the API message inside an answer card (line 47). Feedback toasts: "Thanks — saved as a confirmed example. Similar questions will be answered the same way." (runtime), "Thanks — marked as not right. Try rephrasing, or add/remove columns.". Source pill "Live Oracle · OWNER" / "Oracle offline — only uploaded-file questions work". |
| Runtime answers | "how many orders" → "4 orders.", High confidence, "✓ 4 of 4 checks passed" (the ORDMAS node shows "5 rows"; the reason, "Cancelled orders are excluded", is visible only inside the collapsed details). "buyer wise order quantity" → bar chart plus table NEXT 950 / TESTBUYER 150 / ZARA 100, "6 of 6 checks passed". **"how many table present in database" → "Understood as: Number of orders", "Low confidence — check", "! 1 check needs attention", and the bold answer "4 orders."** |
| Responsive | One column at ≤1200 px; side cards move below the thread. Phone: no page overflow, but the SVG chart is drawn on a 640-unit viewBox and scaled to about 300 px, so its labels are about 5 px high and unreadable (runtime `p_chat.png`; `chart.js` line 24). |
| Technical information | Heavy. Column headers show `BUYMAS.BUYERNAME` and `ORDMAS.*` under every label (line 108). The answer pill says "matched by rules" / "saved example". The checks name roles such as "SQL engineer", "Oracle security reviewer", "Data quality reviewer" and "Oracle database expert". The details show "Tables:", "Links used:" with join conditions and status pills (verified/declared/proposed), "Values matched in the database: phrase → column", and the full SQL with bind variables as JSON (lines 112-120). The legend mentions "Unconfirmed link". The preview modal shows the table kind ("header"), the table code and column codes. Most of this is collapsed or secondary, but column codes are always visible in the result table. |
| Accessibility | The composer has a label; the chart has `role="img"` with an `aria-label` and per-mark `<title>`. Gaps: sorting is a click handler on `<th>` with no button or `aria-sort` (line 248), so keyboard users cannot sort; confidence is shown by pill colour **and** text (OK); the "check" list uses ✓, ! and ✗ symbols plus colour (OK). |
| UX problems | (a) **A misunderstood question is answered with a figure** instead of "Please rephrase", which goes against the page's own promise: "nothing is guessed silently" (High). (b) The starters and placeholder are tied to demo data ("How many orders for NEXT?", "Size wise quantity for order PK/26-27/S/9001", `chat.js` lines 10-11; `index.html` line 119; side text "Names like “NEXT”…"). On a real database these may return nothing (Low). (c) "Your recent questions" lists the same question twice (runtime) (Low). (d) Phone chart is unreadable (Low). (e) Answer count vs node row count ("4 orders" vs "5 rows") without a visible explanation (Low). |
| Status | IMPLEMENTED for rules-based answers (runtime on SQLite; `tests/test_insight.py`, `tests/test_engine.py` exist). Real Oracle and read-only account answers: NOT VERIFIED. Local-AI understanding: NOT VERIFIED. |

---

## 7. Database explorer (`explorer.js`, `graph.js`)

| Item | Finding |
|---|---|
| Purpose | Browse the ERP schema by business name, open a record and follow linked records; read-only. |
| Components | `#view-explorer` (`index.html` lines 132-140); `loadExplorer`, `drawTables`, `openTable`, `load`, `draw`, `relationshipMap`, `openRecord`, `recordHtml`, `childHtml`, `exploreOrder`, `bindExplorer` (`explorer.js`). |
| Controls | "Find an order: reference or record ID" plus `Find`. "Search tables (e.g. buyer, size, ORDSIZE)". Table list grouped "Order tables / Masters / Local / Other tables", each with a row count. Tabs `Records`, `Columns (N)`, `Relationships`. Filter bar: Column select, Match (`contains` / `is exactly`), Value, `Filter`, `Clear`, "Show empty columns (N)", "Show Axpert system columns (N)". `Load more`. Record drawer: `All fields (N more)`, "Belongs to" master buttons, jump buttons per child table, child tables, `Show all N in the table`, `← Back to …`. |
| States | Loading: busy "Reading the table list from Oracle…", "Reading ORDMAS…", "Finding the order…". Empty: "Choose a table on the left."; "No tables match."; "No records. This table is empty."; "No records match this filter. <Column> contains “…”." with `Clear the filter`. Errors (`role="alert"`): "This filter could not be run." / "The records could not be read." with `Try again` or `Look for exactly “…”`; "More rows could not be read: …"; "Connect Oracle to browse tables."; Find: "That is too long for an order reference (at most 100 characters)…", "The order table (ORDMAS) is not available on this database.", "The order could not be looked up. …", "No order reference is or contains “…”. Check it, or search for a part of it such as 9001." Approximate counts: "≈" plus explanation (`APPROX`, line 13). |
| Runtime | Opens ORDMAS by default: "Order ORDMAS 5 rows 16 links", "Columns (197)", "Show empty columns (166)", "Show Axpert system columns (18)". Clicking a row opened a drawer titled "Order PK/26-27/S/7777" with decoded masters and child tables. "Find" with `PK/26-27/S/9001` opened that order directly. Relationships shows a zoomable graph with unconfirmed links in amber. |
| Responsive | ≤920 px: one column, table list capped at 300 px; no page overflow on the phone (runtime). The grid scrolls inside `.grid-scroll` (`max-height: 62vh`). |
| Technical information | This screen is for power users and exposes a lot: raw table codes beside every business name, column codes under every header, record IDs as chips next to decoded names (for example "TESTBUYER 1000000000001"), "Show Axpert system columns", join conditions such as "ORDSTYLE.ORDMASID = ORDMAS.ORDMASID", "confirmed link / unconfirmed link", the schema owner "GARMENT_DEMO" in the lead text. The eyebrow is hard-coded to **"Read-only · live Oracle"** (`index.html` line 133) whatever the connection state. |
| Accessibility | Gaps: records are clickable `<tr>`s without `tabindex` or a button (line 176), so **keyboard users cannot open a record**. The tabs are plain buttons with no tab roles (line 153). The drawer does not trap focus or return it (§1.4). Good: filter fields are labelled, errors use `role="alert"`, and graph nodes are focusable. |
| UX problems | (a) The keyboard cannot open records (Medium, a11y). (b) The fixed "live Oracle" eyebrow can contradict the status light (Low). (c) "Columns (197)" with 166 empty columns: the defaults hide them, which is good, but the counts are intimidating (Low). |
| Status | IMPLEMENTED (runtime on SQLite stand-in). Approximate counts and timeouts on a large real Oracle: NOT VERIFIED. |

---

## 8. Setup (`admin.js`)

Common shell: heading "Mapping, data dictionary & people" and a subnav with `Write mapping`, `Data dictionary`, `Order fields`, `Layout templates`, `Answer accuracy`, `Users & roles`, `Audit history`, `Settings` (`index.html` lines 144-153). The last three need the `users`, `audit` and `settings` permissions (`data-permission`). Each section loads under busy "Loading…" (`admin.js` line 28). Any load error appears only as a toast and leaves the section body empty (`util.js` line 30).

**Viewer runtime.** `viewer1` saw `Write mapping`, `Data dictionary`, `Order fields`, `Layout templates` and `Answer accuracy`, with every toggle disabled and no `Run the check` button. A viewer can therefore read the whole write mapping and dictionary. That is acceptable for transparency, but it is admin material shown to the lowest role.

**Phone runtime.** The subnav wraps onto three lines (`p_setup_mapping.png`).

### 8.1 Write mapping
| Item | Finding |
|---|---|
| Purpose | Decide which Oracle tables and columns POPPYS AI may write (verified vs proposed). |
| Controls | Per table: pills "visible / not visible", "insert granted / no insert grant", and a `Verified` / `Proposed` toggle. Per column: the same toggle, or a "fixed" pill. Verifying opens the evidence dialog "Verify ORDFAB?" — "From now on POPPYS AI may write this table on approved imports." with "What shows this is correct? *", a textarea, "Saved permanently with your name. Similar column names are not proof.", and `Go back` / `Verify` (runtime; lines 8-19, 72-82). |
| Messages | Banner "Column types below are read live from Oracle." / "Oracle is not connected — showing the saved dictionary snapshot." plus "No discovery evidence yet: run `python scripts/discover_schema.py` on this computer." (runtime) plus "POPPYS AI writes only verified columns of verified tables. Keys and parent links are fixed." Warning toast for short evidence: "Write what shows this is correct (at least 15 characters). Nothing was changed." Success: "TABLE.COL is now verified. Prepare previews again to use it." |
| Technical information | Entirely technical by design: column names, types such as `VARCHAR2(30)`, "Filled from `order.order_ref`", "master BUYMAS.BUYMASID", fill percentages, "FK →". |
| UX problems | (a) The UI tells the admin to run a command-line script (Low). (b) The short-evidence rule is checked only after the dialog closes. The typed text is then lost, and only a toast explains why (Low). (c) The evidence textarea is placed inside `#confirm-body` with no `id`/`for` pairing beyond the wrapping label (OK). |
| Status | IMPLEMENTED (runtime display and dialog; `tests/test_evidence.py`). Real grants: NOT VERIFIED. |

### 8.2 Data dictionary
| Item | Finding |
|---|---|
| Controls | `Refresh from Oracle`; stat tiles (Tables, Links confirmed, Links to review, Values indexed, Confirmed answers); Relationships with the filter `Needs review` / `All links` and per-link `Confirm` / `Unsure` / `Wrong` (Confirm and Wrong ask for evidence); "Tables & business names" list with Search; editor (Business name, Synonyms, Description, `Save table`; per column Business name, Synonyms, Role select, `Save`); "Confirmed answers" with `Remove`. |
| Messages | "Structure read from Oracle on <date>…"; "Every link is confirmed."; "Choose a table."; "None yet."; "Saved. Ask AI uses the new names right away."; "Link updated."; "Read N tables · indexed N values in N columns." |
| Responsive | **Phone: the page measured 398 px wide in a 390 px viewport.** The inline `grid-template-columns: 300px minmax(0,1fr)` (line 296) leaves the editor about 50 px wide ("Choose a / table." stacked) (runtime `p_setup_dictionary.png`). |
| Technical information | Join conditions (`ACCCOLOR = COLORMASID`), sources ("registry", "curated", "naming"), roles (`label`, `measure`, `key`, `system`), masked patterns. |
| UX problems | (a) The phone layout described above (Low). (b) Removing a confirmed answer happens immediately, with no confirmation (Low). (c) Column `Save` buttons save one row at a time with no unsaved-change indicator (Low). |
| Status | IMPLEMENTED (runtime display). |

### 8.3 Order fields
| Item | Finding |
|---|---|
| Controls | Per field: kind pill and location; "Always · needed by Axpert" (lock icon) or a `Required` checkbox; alias chips with × ("Remove “x” from <Field>"); "Add a word, press Enter" input; `Restore the default words`; `Save` (shown only when something changed). |
| Messages | "“x” needs letters or digits."; "“x” is already an alias of <Field>. An alias can belong to one field only."; "<Field> saved. New uploads and the review screen use it now."; for non-admins: "Only an administrator can change order fields." |
| Accessibility | Good: `aria-invalid` and `aria-describedby` on the error, `aria-label` on the alias input and the remove buttons. |
| UX problems | Changes are saved per field, and leaving the section discards unsaved edits without a warning (Low). |
| Status | IMPLEMENTED (runtime display). |

### 8.4 Layout templates
| Item | Finding |
|---|---|
| Controls | Table columns Layout, Buyer, Version (`History (N earlier)` / `Hide`), Files read, Quantity unit, Saved by, In use (a switch, `role="switch"`, "In use" / "Off"). |
| Messages | Empty: "No templates yet. Built-in layouts (merch BOM, buyer contract, simple tables) are recognised automatically." (runtime). "This version now reads new files with this layout; the other versions are off."; "Template turned off. Files with this layout will ask for a layout check."; pill "older format". |
| UX problems | There is no rename, delete or split. Two different layouts merged under one buyer name (§4.4) cannot be separated here (Medium, together with §4.4). |
| Status | IMPLEMENTED (code; empty state seen at runtime; populated table not captured). |

### 8.5 Answer accuracy
| Item | Finding |
|---|---|
| Controls | `Run the check` (admin only). |
| Messages | "Not run yet."; score banner "12 of 12 correct · <date> · admin · 0.2 s" (runtime, stand-in); per question "Understood as: …", a result pill (`correct`, `wrong`, `refused`, `not understood`, `error`, `skipped`) and "Why" ("expected 4, got 4"); "Earlier runs". |
| Technical information | File-system paths: "Shipped with the app: config/golden_questions.json (12 questions) · This company's own: /tmp/poppys-demo-…/golden_questions.json (not created yet)". The UI asks the user to edit a JSON file (`admin.js` lines 244-245). |
| UX problems | A file path and a JSON-editing instruction are the only way to add company questions (Low). |
| Status | IMPLEMENTED (runtime on the stand-in; `tests/test_benchmark.py`). Real Oracle: NOT VERIFIED. |

### 8.6 Users & roles
| Item | Finding |
|---|---|
| Controls | Table User, Role (select), Status, `Disable`/`Enable`, `Reset password`. "Add a person" form: Username, Temporary password (min 12), Role, `Add`. |
| Messages | "Person added." (runtime); "Role updated."; "Password reset. Their sessions were signed out."; server: "That username already exists.", "At least one active administrator is required." |
| Problems | (a) **Reset password uses `prompt()`** (line 223): the new password is typed in plain text, cannot be confirmed, and is not checked before sending (Medium). (b) **Changing the role select saves immediately** (line 219), and **Disable takes effect at once** (line 222). Neither asks for confirmation, so one slip can demote or lock out a colleague (Medium). (c) The role select in each row has no accessible label (line 210) (Low). (d) The username `pattern` is built inside a JS template literal, so `\-` loses its backslash. At runtime Chromium logged "Pattern attribute value [A-Za-z0-9._@-]+ is not a valid regular expression … /v" and ignored it, so client-side username validation does not run here; the server still validates (Low). (e) There is no explanation of what each role can do beyond the subtitle "Operators prepare entries · Approvers save to Oracle · Admins manage setup", and viewers are not mentioned (Low). |
| Status | IMPLEMENTED (runtime add-user; `tests/test_auth.py`). |

### 8.7 Audit history
| Item | Finding |
|---|---|
| Content | "Latest 300 events · stored locally; Oracle saves are also logged in PP_IMPORT_LOG"; columns When, Who, What (friendly action names, `ACTIONS` map, lines 227-231), Details. |
| Runtime | The details show raw values: `plan_hash: d3a751dd…` (64 hex characters), `sha256: …`, `orders: [{"order_ref":…,"ordmasid":…}]`, and for "Corrected values" JSON dumps with sheet, cell and UUID ids (`orders[0].lines[0].sizes: — → [{"size":"0-3M",…}]`). |
| UX problems | Hard to read for an approver, who also has `audit` permission. There is no filter, search, export or paging beyond 300 (Medium for approvers, Low for admins). |
| Status | IMPLEMENTED (runtime). |

### 8.8 Settings
| Item | Finding |
|---|---|
| Controls | "Default exporter (pre-filled on every order)", "Default sample type", the checkbox "Allow the person who uploaded a file to approve it (turn off for a four-eyes rule)", `Save settings`. |
| Messages | "Settings saved." |
| UX problems | Inputs have no inline validation (only `maxlength`), and the screen says nothing about which drafts the new defaults affect (Low). |
| Status | IMPLEMENTED (code; screen captured at runtime; save not exercised). |

---

## 9. Cross-cutting accessibility summary

| Check | Result | Evidence |
|---|---|---|
| Visible focus | `:focus-visible` outline globally; inputs get a ring. The graph overrides `.gnode:focus { outline: none }` but adds a stroke on `:focus-visible`. | `styles.css` lines 30, 151, 359-360, 596-600 |
| Labels | Most inputs are labelled. Missing: the user-row role select, the "Ask about this file" textarea, the Data dictionary search (placeholder only, `admin.js` line 295), the relationship filter select (line 293). | as cited |
| Live regions | Toasts `role=status` (errors are polite, not assertive); busy `role=status`; pick hint and zoom announcements live. | `index.html` lines 160, 194 |
| Keyboard-only gaps | Database record rows; Ask AI sort headers; layout-check sheet cells; trim group rows; Saved/Overview rows (buttons exist there, so OK). | `explorer.js` line 176; `chat.js` line 248; `mapping.js` line 478; `workspace.js` lines 362, 724 |
| Colour-only signals | Mostly paired with text or symbols. Exceptions: yarn % total in red text only; the layout-check legend (colour plus outline style); "unconfirmed link" amber edges in diagrams (legend explains). | `workspace.js` line 341; `mapping.js` line 80 |
| Dialog focus | Native `<dialog>` is fine. The drawer neither traps nor returns focus. | `util.js` lines 49-54 |
| Motion | `prefers-reduced-motion` honoured. | `styles.css` line 547 |
| Language | `lang="en"`; dates follow the browser locale (`toLocaleString`). Date inputs showed `mm/dd/yyyy` in an en-US browser while other screens show ISO `2026-09-01`. | runtime |

---

## 10. Technical information exposed to ordinary users (index)

| Where | What | Who sees it |
|---|---|---|
| Sidebar, Overview | "start Docker + oracle26ai", "GARMENT_DEMO · N tables", read-only DB user | everyone |
| Review | sheet!cell chips, "mapping is not verified" with ORDFAB/ORDYARN/ORDACC | operator |
| Approval diagram | `ORDMAS`, `BUYMAS`, `SIZE_MAS`, edge labels `ORDMASID`/`SUBNO`/`YDET`, `TABLE.COLUMN` codes | operator/approver |
| Save confirm dialog | raw table names with record counts | approver |
| Saved / receipt | `ORDMASID 1000000000005`, master IDs as chips, `TABLE.COLUMN` codes, "fresh SELECT" | approver/all |
| Ask AI | column codes under every header, check roles, join conditions, SQL with bind JSON, link statuses | everyone |
| Database | table/column codes, record IDs, system columns, join conditions | everyone |
| Setup | types, registry sources, CLI commands, file paths, plan hashes, raw JSON | viewer (partly) / approver / admin |

---

## 11. Feature status table

| Feature | Status | Basis |
|---|---|---|
| First-run admin setup, sign-in, wrong-password and common-password errors | IMPLEMENTED | runtime + `tests/test_auth.py` |
| Overview KPIs, queue, connections | IMPLEMENTED | runtime (stand-in) |
| Upload, type check, permission check | IMPLEMENTED | runtime |
| Reading summary | IMPLEMENTED | runtime |
| Review with on-field errors, next-issue navigation, undo/redo | IMPLEMENTED | runtime |
| Layout check (matcher), save as template, versioning | IMPLEMENTED | runtime |
| Layout check with local AI | NOT VERIFIED | AI off |
| Ask about this file | PARTIALLY IMPLEMENTED | offline path only; stale wording |
| Approval view, confirm, single-transaction save, read-back | IMPLEMENTED on stand-in; NOT VERIFIED on real Oracle | runtime with fake Oracle |
| Saved orders list and receipt | IMPLEMENTED (stand-in) | runtime |
| Ask AI rules-based answers, charts, CSV, feedback, preview | IMPLEMENTED (SQLite stand-in) | runtime |
| Ask AI on real Oracle with the read-only account | NOT VERIFIED | — |
| Database explorer, Find order, record drawer, relationship map | IMPLEMENTED (stand-in) | runtime |
| Setup sections (8) | IMPLEMENTED (display); Settings save NOT exercised | runtime |
| Browser Back/Forward between screens | NOT IMPLEMENTED | `app.js` line 63 |
| Phone layout of Review, Layout check, Data dictionary | PARTIALLY IMPLEMENTED | runtime overflow |
| `flow.js` table flowchart | dead code (not imported, broken import) | `flow.js` line 4 |

---

## 12. Prioritized UX problem list

| # | Severity | Problem | Evidence | Suggested direction |
|---|---|---|---|---|
| 1 | High | A partial save is presented as a full success. Fabric, yarn and trims rows that were reviewed are silently not written. The notice is a small blue note at the bottom of the Saved page, and it is missing from the warnings, the action text and the confirm dialog. | Runtime Saved and Approval screens; `workspace.js` lines 650-654, 668-670, 692 | State the exclusion in the approval warnings and the confirm dialog, and use a "Saved, except …" heading with the excluded sections near the top of the Saved page. |
| 2 | High | Ask AI answers a question it did not understand with a number ("how many table present in database" → "4 orders."). | Runtime Q2; `chat.js` lines 85-100 | Treat low-confidence intent matches as "Please rephrase" with suggestions, or require the user to confirm the reading before showing a figure. |
| 3 | Medium | The phone layout of the Review screen (615 px) and the layout check (518 px) scrolls the whole page sideways; buttons spill outside cards and tabs wrap word by word. | Runtime 390 px; `styles.css` lines 253, 259, 327-330, 616, 729-730 | Let the document bar and tab bar wrap or collapse into a menu below ~600 px; put the sheet grid next to the field being picked. |
| 4 | Medium | The save confirmation and the saved record views describe the decision in raw Oracle table and column names and IDs. | Runtime confirm dialog; `workspace.js` line 669; `flowview.js` lines 41, 91 | Lead with business names ("Buyer (new)", "8 size quantities"), and move codes behind a "technical details" toggle. |
| 5 | Medium | Viewers are offered upload actions and refused only after choosing a file. | Runtime `viewer1`; `index.html` line 42; `workspace.js` line 58 | Hide or disable upload calls to action according to permissions, with a one-line reason. |
| 6 | Medium | The default quantity source can be a zero-quantity sample section, so a valid file opens with "All quantities are zero" errors and "0 pcs", contradicting the reading summary ("Buyer order 150 pcs"). Unknown layouts open with no source chosen. | Runtime merch BOM and ZEN KIDS | Preselect the non-zero source when only one has quantities, and say in the reading summary which source will be used. |
| 7 | Medium | The browser Back button and URL hash do not move between screens; Back leaves the app. | `app.js` line 63; runtime | Push a history entry per screen and listen for `hashchange`/`popstate`; keep the draft-discard guard. |
| 8 | Medium | Password reset uses a plain `prompt()`; role changes and Disable take effect without confirmation. | `admin.js` lines 219-223 | Use the app's dialog with a masked, repeated password and a strength hint; confirm role and status changes. |
| 9 | Medium | Template name defaults to the buyer name, so a different layout from the same buyer becomes a new version and turns the earlier layout off; Layout templates cannot rename or split. | Runtime (`unknown.xlsx` → "ZEN KIDS layout" v2); `mapping.js` lines 363-390; `admin.js` lines 105-139 | Warn clearly when an apply will replace a different layout, suggest a distinct name, and allow renaming or separating families. |
| 10 | Medium | Keyboard users cannot open Database records, sort Ask AI columns, pick sheet cells or expand trim groups. | `explorer.js` line 176; `chat.js` line 248; `mapping.js` line 478; `workspace.js` lines 362, 724 | Make these real buttons or give them `tabindex` plus key handlers; add `aria-sort` to sortable headers. |
| 11 | Medium | Audit history shows plan hashes, sha256 values and raw JSON to approvers, with no filter or search. | Runtime Audit; `admin.js` lines 259-267 | Summarise changes in words (field: old → new), put hashes in an expandable row, and add filters. |
| 12 | Medium | The phone navigation takes about 370 px before any content on every screen. | Runtime phone screenshots; `styles.css` lines 527-532 | Collapse the nav into a compact bar or menu below 600 px. |
| 13 | Low | The offline "Ask about this file" answer points to a non-existent "questions panel"; the button is shown while the local AI is off. | Runtime; `main.py` line 1589 | Update the wording to "outlined on the fields"; hide or disable the button when the AI is off. |
| 14 | Low | The busy state drops clicks silently; errors raised inside busy actions appear only as a 12 s toast and can leave an empty section. | `util.js` lines 26-32; `admin.js` line 28 | Disable the triggering control while busy; show load errors inside the section with a retry. |
| 15 | Low | Toasts cover content and the sticky action bar (bottom right) and persist across stages; opening the layout check fires a success toast. | Runtime review and phone; `styles.css` line 110; `mapping.js` line 162 | Clear stage-specific toasts on stage change; reserve success toasts for user actions; move toasts away from the action bar on small screens. |
| 16 | Low | The Database eyebrow always says "Read-only · live Oracle". | `index.html` line 133 | Reflect the actual connection state, as the Ask AI pill does. |
| 17 | Low | The Ask AI chart is unreadable on a phone (fixed 640-unit SVG). | Runtime; `chart.js` lines 21-35 | Use a narrower layout or HTML bars under ~500 px. |
| 18 | Low | The Data dictionary editor grid does not collapse on a phone (page 398 px wide). | Runtime; `admin.js` line 296 | Move the inline grid into CSS with a one-column breakpoint. |
| 19 | Low | The username `pattern` in Add a person is invalid in current Chromium (escaped hyphen lost in a template literal), so client validation is skipped. | Runtime console; `admin.js` line 215 | Fix the escaping so the browser accepts the pattern. |
| 20 | Low | Starter questions and placeholder text are tied to demo data (NEXT, PK/26-27/S/9001). | `chat.js` lines 10-11; `index.html` line 119 | Generate starters from indexed values or confirmed examples. |
| 21 | Low | An internal key is written into the Oracle REMARKS text ("quantity basis: order_demo"). | Runtime approval/saved; Write mapping row REMARKS | Use a business phrase ("buyer order quantities"). |
| 22 | Low | The drawer does not trap or return focus; tabs lack tabpanel or arrow-key semantics; some inputs lack labels (role select, dictionary search, relationship filter, file-question textarea). | `util.js` lines 49-54; `workspace.js` line 180; `admin.js` lines 210, 293, 295 | Add focus management and proper tab semantics; add visually hidden labels. |
| 23 | Low | Saved orders on a phone: the only action button is off-screen inside the scrolling table; there is no search or filter. | Runtime `p_history.png`; `app.js` lines 70-74 | Stack rows as cards on small screens; add a search box. |
| 24 | Low | Admin screens ask users to run CLI scripts or edit JSON files (discovery, golden questions) and show file-system paths. | `admin.js` lines 61, 244-245 | Keep, but move into an "administrator help" note and avoid absolute paths. |
| 25 | Low | `flow.js` is dead code with a broken import (`record` is not exported by `util.js`). | `flow.js` line 4 | Remove it, or fix the import if it is meant to be used. |

## 13. Not verified

- Behaviour with a real Oracle database (connection lights, live masters, approval enablement, real read-back, approximate counts, query timeouts) and with the read-only account. The stand-in reported "Oracle offline" while saving, so the offline banners on the approval and review screens were not seen in a realistic combination.
- Local AI (Ollama): "Suggest with local AI", "Ask about this file" answers, AI-assisted Ask AI understanding.
- Windows-specific behaviour (start.ps1, fonts "Segoe UI Variable", file-dialog behaviour, Edge/Chrome versions used on site).
- The approval view at 390 px, and the Settings save, were not exercised at runtime.
