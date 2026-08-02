# Modal Explicit-Close Design

## Goal

Prevent every shared application modal from closing when a user drags to select
text from an input into the backdrop. A modal may close only through an explicit
control or an intentional workflow completion.

## Root Cause

The shared `Modal` component attaches `onClick={onClose}` to its full-screen
backdrop. When a primary-button drag begins in an input and ends outside the
dialog, Chromium dispatches the resulting `click` to the backdrop. That click
invokes `onClose`, so every dialog built on `Modal` closes and discards the
user's in-progress interaction.

## Behavior

- Clicking or pressing any mouse button on the backdrop does not close a modal.
- Dragging text selection from a field into the backdrop does not close a modal.
- The header close button and explicit cancel controls continue to close it.
- Successful save/confirm workflows keep their existing intentional close behavior.
- No visual, API, backend, or database behavior changes.

## Implementation

Remove backdrop-driven closing from the shared `Modal` component. Keep the
existing `onClose` calls on explicit controls. Because every current modal uses
this shared component, one scoped change covers form, ratio, priority, channel,
and confirmation dialogs without duplicating event guards.

## Verification

Add a browser regression test that opens the shared site form modal and verifies:

1. Primary-button text selection dragged outside the dialog leaves it open.
2. Backdrop clicks leave it open.
3. Middle-button and right-button backdrop interactions leave it open.
4. The header close button still closes it.

Run the web regression suite, the production frontend build, and a real-browser
interaction pass against the local development server.
