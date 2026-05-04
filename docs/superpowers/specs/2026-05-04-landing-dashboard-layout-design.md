# Landing page dashboard layout

**Status:** Approved (brainstorming complete, awaiting spec review)
**Date:** 2026-05-04
**Scope:** Frontend only (`frontend/src/pages/Landing.tsx` + landing components). No backend changes.

## Problem

Adding `JobsSection` to the landing page tipped the balance: the page is now a working dashboard, not a marketing landing. The current top-down stack (hero header → robot tile → jobs → action buttons) makes the action buttons drift below the fold once a few jobs exist, and the hero header + auth banner consume vertical space that should belong to the work area.

The user wants:

- Robot selection: always visible. Can shrink.
- Action buttons (Record / Replay / Training / Inference): always visible, very clear. Can shrink, but should still feel like big buttons.
- Jobs: scrollable area.
- HF auth ("Logged in as…" / not-logged-in warning): refactored into something less intrusive.

## Decisions

Made during brainstorming. Each decision had alternatives considered and discarded.

| # | Decision | Alternatives rejected |
|---|---|---|
| D1 | **Layout: header dock**. Sticky top bar + sticky dock (robot + actions). Jobs grid scrolls below. | Left sidebar dock; split top + wide jobs. |
| D2 | **Robot tile compaction: V1 (mild)**. Selector + icon buttons in row 1, status line, full-width Teleop button. ~30% shorter than today. | Single-row inline (Teleop demoted to a pill); single-line with status chip. Both downgrade Teleop's visual weight. |
| D3 | **HF auth: chip + modal**. Top-right chip in the sticky bar, always present. Click opens a modal with login command + recheck button when not authenticated. The current `HfAuthBanner` warning panel is removed entirely. | Keep banner for not-logged-in state; thin amber strip. |
| D4 | **Header treatment: brand-mark in top bar**. Small logo + "LeLab" wordmark in the top-left of the sticky bar. Hero `LandingHeader` component (big logo + tagline) is removed from this page. | Keep hero header above the dock. |
| D5 | **Page width: `max-w-7xl` (1280px)**, centered. Today is `max-w-4xl` (896px) which wastes horizontal space at the assumed desktop viewport (≥1280px). |
| D6 | **Stickiness**: top bar is sticky to viewport top; dock is sticky directly below the top bar. Both stay pinned while jobs scroll. |

## Target composition

```
┌────────────────────────────────────────────────────────────────┐
│ [▣ LeLab]                                       [● nicolas]   │  ← sticky top bar
├────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────┐ ┌─────────────────────────────────┐   │
│  │ Robot tile          │ │  [Record  →]  [Replay   →]      │   │  ← sticky dock
│  │  selector ⚙ 🗑      │ │  [Training →] [Inference →]     │   │
│  │  ● Ready            │ │                                 │   │
│  │  [ Teleoperation ]  │ │                                 │   │
│  └─────────────────────┘ └─────────────────────────────────┘   │
├────────────────────────────────────────────────────────────────┤
│  Jobs · 12                                              [↻]    │  ← scrolls
│  ┌─────────┐ ┌─────────┐ ┌─────────┐                           │
│  │ JobCard │ │ JobCard │ │ JobCard │   …                       │
│  └─────────┘ └─────────┘ └─────────┘                           │
└────────────────────────────────────────────────────────────────┘
```

## Component changes

### Landing.tsx

Replace the current single-column stack with three vertical zones:

1. `LandingTopBar` (new) — sticky `top-0`, full width, dark background.
2. **Dock** — inline in `Landing.tsx`, no new component. Sticky directly below the top bar, `max-w-7xl` centered. Contains a 2-column grid (`grid-cols-[1.2fr_2fr]` at `lg:`, single column below): `RobotConfigManager` left, `ActionList` right.
3. `JobsSection` — placed below the dock, `max-w-7xl` centered.

Remove from this page: `<LandingHeader />`, `<HfAuthBanner />`. Keep `<UsageInstructionsModal>` and `<RecordingModal>`.

### LandingTopBar (new)

Compact bar with:

- Left: small logo (the existing `lovable-uploads/...png` at h-7 w-7) + "LeLab" wordmark.
- Right: `HfAuthChip`.

### HfAuthChip (new) + HfAuthDialog (new)

Replaces `HfAuthBanner`.

Driven by `useHfAuth()`. The status discriminator is `auth.status`, with values `"loading" | "authenticated" | "unauthenticated"` (see `frontend/src/contexts/HfAuthContext.tsx`).

- **`auth.status === "loading"`**: render a muted neutral chip with a small spinner placeholder; not interactive.
- **`auth.status === "authenticated"`**: chip shows a green dot + `auth.username`. Click is a no-op for this spec.
- **`auth.status === "unauthenticated"`**: chip shows an amber dot + "HF not configured" label. Click opens `HfAuthDialog`.
- **`HfAuthDialog`** contains exactly the content the current banner shows: explanation text, `auth.loginCommand` in a copyable code block, and the "I've logged in — recheck" button. Reuse the existing copy-to-clipboard and `refetch()` handlers from the current `HfAuthBanner.tsx`.

### RobotTile (modified, V1 compaction)

Existing `RobotTile.tsx` already has the right structure but with extra padding. Concrete changes:

- Outer container: reduce `gap-3` → `gap-2`, `p-4` → `p-3`.
- Status line: tighten margin (already `text-xs text-center`; keep but ensure it's a single line, never wraps).
- Teleop button: keep full-width and full-height (this is the big-button anchor).
- The placeholder/empty state when no robot is selected stays unchanged in behaviour but should not exceed the height of the populated tile (avoid layout jumps).

### ActionList (modified, fits dock)

- Today: `grid-cols-1 md:grid-cols-2` with `pt-6`. The `pt-6` is no longer needed when there is no preceding section header.
- Each action item: shrink padding from `p-4` → `p-3`, keep title at `text-lg`, keep description (it's part of the "big button" feel).
- The right-arrow icon button stays as the affordance.

### JobsSection (modified)

- Today: `grid-cols-1 md:grid-cols-2 lg:grid-cols-3` with `gap-4`.
- New: keep the same responsive grid. The container width is now `max-w-7xl` so 3 columns at desktop have more breathing room.
- Empty state copy unchanged ("No training jobs yet. Start one from the Training page.").
- Header: keep "Jobs" label + refresh icon. No changes.

### Files to modify / add

```
frontend/src/pages/Landing.tsx                          (modified)
frontend/src/components/landing/LandingTopBar.tsx       (new)
frontend/src/components/landing/HfAuthChip.tsx          (new)
frontend/src/components/landing/HfAuthDialog.tsx        (new)
frontend/src/components/landing/RobotTile.tsx           (modified — tighter spacing)
frontend/src/components/landing/ActionList.tsx          (modified — tighter spacing, drop pt-6)
frontend/src/components/landing/LandingHeader.tsx       (deleted — no longer used)
frontend/src/components/landing/HfAuthBanner.tsx        (deleted — replaced by chip + dialog)
```

Confirmed via grep: `LandingHeader` and `HfAuthBanner` are only imported by `Landing.tsx`. Both files can be deleted outright.

## Sticky behaviour details

- Top bar: `sticky top-0 z-30 bg-black/95 backdrop-blur border-b border-gray-800`. Height is set by content padding (logo `h-7` + `py-2` ≈ 44px); use a `--lelab-topbar-h` CSS variable on the page root so the dock's `top` offset stays in sync.
- Dock: `sticky top-[var(--lelab-topbar-h)] z-20`. The dock has its own opaque background so jobs scrolling underneath stay hidden. `z-20` keeps it below the top bar but above the jobs grid.
- The page itself (the outermost `<div>`) keeps `min-h-screen` and natural document scroll. Do not introduce an inner overflow container — it breaks browser back-scroll-position restore and adds focus-trap complexity.

## Out of scope

- Any change to `RecordingModal` content or behaviour.
- Any change to per-job interaction (`JobCard` is unchanged).
- Mobile/tablet layout work. Below `lg:` we accept stacked layout; we do not optimise for it. (If the user moves the goal post to mobile later, that's a separate spec.)
- Sign-out flow.
- Anything about training or other pages.

## Acceptance criteria

1. Page renders at 1280px wide with the top bar, dock (robot + 2×2 actions), and jobs grid all visible without horizontal scroll.
2. Scrolling the page keeps the top bar and dock pinned to the viewport top.
3. The four action buttons (Record, Replay, Training, Inference) are visible without scrolling, regardless of how many jobs exist.
4. With 12 jobs, at least one full row of 3 cards is visible above the fold; the remaining rows reveal on scroll.
5. When `auth.status === "authenticated"`, the chip shows the username; no warning UI exists on the page.
6. When `auth.status === "unauthenticated"`, the chip shows an amber state and clicking it opens a modal containing the login command (copyable) and "I've logged in — recheck" button. The page itself contains no banner.
7. The `LandingHeader` hero (big logo + "LeLab" + tagline) is removed from the landing page.
8. No regressions: Recording, Replay, Training, Inference, Teleoperation, Configure (calibration), and robot delete all still work from this page.

## Verification

There is no automated test suite in this repo (per CLAUDE.md). Verification is manual via `lelab --dev`:

- Visit `http://localhost:8080`.
- Confirm criteria 1–8 above.
- Run with `auth.status === "authenticated"` and with HF unset to exercise both chip states. (Unsetting `HF_TOKEN` env or signing out via the CLI exercises the second.)
- Resize browser to ≥1280px to confirm the layout assumes desktop.
