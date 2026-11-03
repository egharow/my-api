# Finance app: design handover

For: Claude Design. From: Ely (owner and main user). Goal: a complete, coherent visual and interaction design for the whole app, on **desktop** and on **phone**.

## 1. What the app is

A private household finance tracker for two people (Ely and Shir). It runs on Ely's Windows computer and opens in a browser window. Data lives in a local SQLite file and never leaves the computer, except an optional backup to Google Drive and an optional read-only phone page.

It turns bank and credit-card statements (PDF, Excel, screenshots) into a monthly picture: where money came from, where it went, what is left, and how wealth is growing.

Users:
- **Ely**: uses it weekly at a desk. Wants speed and trust. Drops statements in, approves what the app guessed, looks at trends.
- **Shir**: occasional. Mostly looks at the Overview, sometimes from a phone.
- **Possibly other households later.** Onboarding and plain language matter, but this is not a public product yet.

The app is in Hebrew-and-English life: merchant and category names are often **Hebrew (RTL)** inside an English interface. Amounts are in shekels (₪), sometimes dollars or other currencies for assets and trips.

## 2. Principles

1. **Trust first.** Every number must be traceable: tap a figure, see the lines behind it. The design should make "where did this number come from?" one click away, and show when something is a guess versus confirmed.
2. **Calm, not gamified.** No confetti, no streaks, no red-everywhere. It is money; it should feel like a well-kept ledger.
3. **The work is mostly reviewing.** The most common action is "the app guessed a category, confirm or fix it". That loop must be fast, keyboard-friendly on desktop and thumb-friendly on phone.
4. **Honest about gaps.** The app knows when a statement is missing, a total does not match, or a payment is unexplained. Show these clearly but without alarm.
5. **Nothing looks AI-generated.** Ely's words about an earlier design: "It's too AI.." Avoid generic gradient cards, emoji decoration, and rounded-everything dashboards. The current direction (a printed statement) was chosen in response.

## 3. Current look ("Ledger") and what to keep

Current design language, implemented in plain CSS with variables:
- Square corners, thin rules instead of boxes, black primary buttons, one ink-blue accent for links (`#1f3a93`, lighter `#8fa8ff` in dark mode).
- Figures in a **monospaced, tabular** face so columns of numbers align.
- Page `#efefee`, surface `#ffffff`, ink `#111`, muted `#6b6b6e`, good `#1b7a3d`, critical `#b3261e`. Full light and dark themes, following the system setting, with a manual override.
- Body 15px system sans.

Keep: the ledger feeling, tabular numbers, rules over cards, light/dark.
Free to change: everything else, including type choice, spacing, navigation pattern, chart styling, iconography, and the density of tables. If a stronger overall direction exists, propose it.

## 4. Desktop screens (all of them)

Top navigation today: **Overview, Upload, Review, Recurring, Wealth, Trips, Settings.** Several other screens hang off those.

| Screen | Purpose | Key content |
|---|---|---|
| **Overview** | The month at a glance | Month picker and 1/6/12-month range; income, expenses, kept; "where it went" categories with bars and "vs usual"; to-do chips (items to review, missing statements, alerts); day-by-day chart; clickable chart legend that filters the charts |
| **Spending / Trends** | Categories over time | Stacked/line charts, category filter (`cats=`), drill into a category's lines |
| **Year** | Whole-year view | Month grid, totals, best/worst months |
| **Upload** | Get statements in | Drop zone, Drive watch-folder status, "check the folder" button, recent imports, link to Files page |
| **Files ("Where every file went")** | Audit trail per file | Per file: account, billing date and period, line count, total check, matched bank payment, which months it counts in |
| **Review** | Confirm the app's guesses | List of lines with suggested category and confidence; bulk approve; one-line change; leave out a payment; create a rule from a line |
| **Item detail** | One transaction | Merchant, amount, history of the same merchant, category change, leave out, add to a trip |
| **Recurring** | Subscriptions and regular payments | Detected recurring items, monthly/annual cost, changes in price |
| **Wealth / Net worth** | Assets and liabilities | Net worth line over time, accounts grouped by kind, per-account balances, dollar rate, include/exclude toggles |
| **Balances** | Enter or confirm balances | Per-account balance entry, balance screenshots waiting for approval |
| **Screens (balance screenshots)** | Read balances from phone screenshots | Side-by-side: the image on one side, the filled-in boxes on the other; nothing saves until approved |
| **Goals** | Wealth goals | Target, progress, projected date |
| **Simulator** | Future wealth | Two sliders (return % per year, years) that also accept typed values; capital-gains-at-the-end input; chart of outcomes |
| **Budgets** | Per-category budget | Set/fill budgets, progress per category, auto-fill from history |
| **Trips and tags** | Group spending | Trip cards with totals, add cash payments, add lines to a trip |
| **Cash** | Cash payments | Add/delete cash spending; reimbursed-in-cash ("Vituri") toggle |
| **Alerts** | Things that need attention | Unusual spending, missing statements, unmatched transfers; dismiss |
| **Close the month** | Lock a reviewed month | Checklist, what is still open, close button |
| **Left out** | Payments excluded from totals | List with restore |
| **Settings** | Everything configurable | Accounts (rename, owner, close, arrange), categories (rename, merge), rules (user/learned/builtin, toggle, delete), aliases, card-month basis, Drive backup and restore, Google Sheet link, phone access, error log |
| **Onboarding / first run** | New user | Choose folder, add accounts, first upload, starter rules |
| **Login (PIN)** | Remote access | Single PIN field |

## 5. Phone

Two different things exist today:

1. **Phone page (read-only, works when the computer is off).** A single small HTML page served from Ely's own Google Apps Script, protected by a PIN. It has two tabs, **Overview** and **Wealth**; Overview has Month / 6 months / 12 months, a month stepper, three tiles (Income, Expenses, Kept) and a "Where it went" list with bars and "vs usual". It can be added to the home screen. It shows the last numbers the computer sent.
2. **Live app over a private link (Tailscale).** The same desktop pages, read-only for non-local devices, behind a PIN.

What we want designed:
- A proper **mobile layout for every screen that makes sense on a phone**: Overview, Wealth, Trips, Alerts, Review (thumb-friendly confirm/change), and quick add of a cash payment.
- A **bottom tab bar** (or equivalent) rather than the desktop top nav.
- Tap a number or bar to drill down. On the phone page the data is a summary only, so design the drill-down to degrade gracefully (show only what the snapshot contains).
- A **"last updated" indicator** on the phone page, because it can be stale when the computer is off.
- Works as a **home-screen app** (icon, splash, standalone display), light and dark.
- Right-to-left text inside left-to-right layout must not break rows.

Update: the phone should be able to approve, change a category, add a cash payment and request a Drive check (see section 10). Today it changes nothing.

## 6. Key flows to design end to end

1. **Add statements.** Drop files, or let Drive sync; see per-file result (recognised, duplicate, locked, unrecognised, wrong total); land in Review.
2. **Review a month.** Go through guessed categories fast; approve in bulk when confident; create a rule from a correction; see how many remain.
3. **Check completeness.** Is every card and bank statement present for the month? What is missing? Does each card payment on the bank statement match a card statement?
4. **Close the month.** Everything reviewed, totals reconciled, lock it.
5. **See where money went.** Overview, tap a category, tap a line, change or exclude it, see the totals update.
6. **Track wealth.** Update balances (including from a screenshot), see net worth, set a goal, run the simulator.
7. **Trips.** Create a trip, attach card lines and cash payments, see the total and a per-day view.
8. **Phone access setup.** Turn on, see the link and PIN, add to home screen. This was hard for the owner to set up; the design should make each step obvious and show errors in plain words.
9. **First run for a new household.**

## 7. Components and states needed

- Month picker with range selector; stat tiles; category rows with bar and "vs usual" delta; tables with tabular numbers and sticky headers; sortable, filterable line lists; chips and badges (counts, status); toggles; sliders with a numeric input; side-by-side image-versus-form review; drop zone; progress for imports; file status rows; empty states; toasts and inline errors; confirm dialogs for destructive actions (delete, restore, close month).
- Chart types: stacked bars per month, line (net worth, projections), horizontal category bars, day-by-day columns. Charts are **inline SVG**, clickable legends and bars filter other charts. A single categorical palette that works in light and dark and for colour-blind users.
- Status vocabulary to make visually consistent: confirmed, suggested (guess), uncategorised, left out, matched, unmatched, missing, mismatch, closed month.
- Number formatting: ₪ prefix, thousands separators, negative handling, currency variants, monospaced tabular.
- Hebrew/English mixed rows, bidi-safe.

## 8. Technical limits the design must respect

- **Plain server-rendered HTML + CSS + a little inline JS.** No React, no build step, no external CDNs or fonts at runtime. The app runs offline on a Windows PC. Design should be implementable as CSS variables and semantic HTML. System font stack; if a web font is wanted it must be shippable as a local file and the owner should approve.
- Content-Security-Policy allows only same-origin images and `data:` images. No remote assets.
- Phone page is **one self-contained HTML file** (currently about 95 lines) served from Google Apps Script, so it must be small and have no external requests.
- Charts are SVG generated in Python.
- Must work in current Chrome/Edge on Windows and in mobile Chrome/Safari. Keyboard operable. Respect reduced motion and the system dark/light setting.
- Navigation items and route names exist today (see section 4); changing labels is fine, but each screen needs to remain reachable.

## 9. What I want back

1. A short **design direction** (one paragraph plus 2-3 alternatives to choose between) before full work.
2. A **design system**: colour tokens (light and dark), type scale, spacing, components with states.
3. **Desktop** designs for every screen in section 4, with Overview, Review, Upload/Files, Wealth and Settings highest priority.
4. **Phone** designs for the screens in section 5, including the home-screen icon.
5. The **key flows** in section 6 as short sequences.
6. Handoff as **CSS variables and component notes** that can be pasted into the existing stylesheet, not a new framework.

## 10. Decisions from Ely

- **Direction:** open to a different one if it is better. Please propose 2-3 directions, one of which may keep Ledger.
- **Phone can change things.** It can approve or change categories, add a cash payment, and trigger "check the Drive folder" after files are added to Drive. Design these as first-class phone actions, not an afterthought.
- **Users:** only Ely's household for now (Ely and Shir). Keep onboarding simple but do not over-invest in it.
- **Language:** English interface for now. Hebrew still appears inside data (merchants, categories), so bidi must work. Do not design a mirrored RTL interface yet, but do not make it impossible later.
- **Top three screens:** Ely is not sure. Working assumption from how the app is used: **Overview, Review, Upload/Files**. Give these the most care, then Wealth and Trips.

### What phone changes mean for the design (technical note)

Today the phone page is a read-only snapshot hosted in Google Apps Script, and the real app and database run on Ely's computer. So an action taken on the phone cannot change data directly when the computer is off. Design for these states:
- **Computer reachable** (live app over the private link): the action applies immediately.
- **Computer not reachable:** the action is **queued** on the phone page and shown as "waiting for your computer", then applied the next time the app is open. Show queued actions clearly and let them be cancelled.
- "Check Drive for new files" from the phone is the same: it is a request that the computer runs when it is on. Show its result ("3 files added, 12 lines to review") when it comes back.
- Approvals from the phone should be small and reversible (approve a suggestion, change a category, leave out a payment), never destructive (no delete, restore or close-month from the phone).

## Appendix: facts that affect design

- Sensitive data stays local. Mock data in designs should be fictional.
- Two people; accounts have owners (Ely, Shir, joint); the interface sometimes filters by owner.
- Months can be **closed** (locked) and the interface should show that state.
- A card purchase counts in the month it was bought; instalments stay in their billing month. The Files page explains this per statement.
- The app shows its build number at the bottom of every page.
