# Upstream Usage Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a single-file, offline Chinese HTML guide that explains and teaches the current Upstream configuration model.

**Architecture:** One semantic HTML document owns all content, PriceAI-aligned CSS tokens, and small dependency-free JavaScript interactions. The production application, API contract, database, and user data remain untouched.

**Tech Stack:** HTML5, CSS custom properties, vanilla JavaScript, inline SVG icons, Playwright for browser verification.

---

### Task 1: Build the standalone guide

**Files:**
- Create: `docs/upstream-usage-guide.html`
- Reference: `docs/superpowers/specs/2026-07-31-upstream-usage-guide-design.md`

- [x] **Step 1: Create semantic document structure**

Implement `header`, `aside`, `main`, sequential `section` headings, tables, alerts, code blocks, checklists, and three configuration-flow diagrams. Include every section required by the design spec and keep all examples redacted.

- [x] **Step 2: Add the PriceAI visual system**

Define semantic CSS variables for page, panel, border, brand, text, success, warning, danger, info, and code surfaces. Add light/dark mappings, responsive grid constraints, print styles, visible focus, and reduced-motion handling.

- [x] **Step 3: Add dependency-free interactions**

Implement search filtering/highlighting, active-section tracking, mobile navigation, theme persistence, code-copy feedback, status checklist persistence, and a print command with semantic buttons and ARIA state.

### Task 2: Verify content and static integrity

**Files:**
- Test: `docs/upstream-usage-guide.html`

- [x] **Step 1: Run static assertions**

Run:

```bash
node -e "const fs=require('fs');const s=fs.readFileSync('docs/upstream-usage-guide.html','utf8');for(const x of ['sites','admin_sites','/api/user/groups','/api/v1/auth/me','CONSOLE_PASSWORD','--truncate','model_added_to_group'])if(!s.includes(x))throw new Error('missing '+x);console.log('guide static assertions: ok')"
```

Expected: `guide static assertions: ok`.

- [x] **Step 2: Scan for obvious secrets and placeholders**

Run:

```bash
rg -n "DB_PASSWORD=[^<]|sk-[A-Za-z0-9]|webhook/send\?key=[A-Za-z0-9]" docs/upstream-usage-guide.html
```

Expected: no real credential match; documented placeholder examples may be reviewed manually.

### Task 3: Browser and responsive verification

**Files:**
- Test: `docs/upstream-usage-guide.html`

- [ ] **Step 1: Verify desktop rendering and interactions**

Open the absolute `file://` URL in Playwright at 1440×1000. Confirm nonblank content, navigation, search, copy feedback, dark mode, and no page-level horizontal overflow.

- [ ] **Step 2: Verify mobile rendering**

Repeat at 390×844. Confirm the sidebar becomes a usable drawer, buttons remain at least 44px, code/table overflow is contained, and text does not overlap.

- [ ] **Step 3: Review screenshots and finalize**

Inspect desktop and mobile screenshots, correct any layout or contrast defect, rerun checks, then mark every plan item complete.

**Verification note (2026-08-01):** Static content assertions, JavaScript syntax,
tag balance, duplicate-ID, hash-target, ARIA-control, responsive-rule, secret-scan,
and whitespace checks passed. Automated visual verification could not run: browser
security policy blocked the final file URL, and the approval service was unavailable
when requesting a temporary loopback-only HTTP server. No alternate browser workaround
was used.
