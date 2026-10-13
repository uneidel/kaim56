#!/bin/sh
# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# The repowise dashboard as kAIm56's "Code index": no repowise logo, name,
# feedback/upgrade/repowise.dev prompts, and no "Ask Repowise" chat (it needs
# an LLM; the indexer runs offline with --no-prose). Run in packages/web of
# the pinned source before `npm run build`. Every edit is checked: a new
# repowise version that moved a string fails the build instead of shipping
# half-branded.
set -eu

stub() {  # stub <file> <body>: the component file becomes exports that render nothing
  printf '%s\n' "$2" > "$1"
}
swap() {  # swap <file> <from> <to>: a literal replacement that must happen
  grep -qF -- "$2" "$1" || { echo "debrand: '$2' not in $1" >&2; exit 1; }
  FROM="$2" TO="$3" perl -0pi -e 's/\Q$ENV{FROM}\E/$ENV{TO}/g' "$1"
}

brand() {  # brand <file>: the bare "repowise" text node next to the logo becomes "Code index"
  grep -qE '^[[:space:]]*repowise[[:space:]]*$' "$1" || { echo "debrand: no brand text in $1" >&2; exit 1; }
  perl -pi -e 's/^(\s*)repowise(\s*)$/$1Code index$2/' "$1"
}

C=src/components
brand $C/layout/sidebar.tsx
brand $C/layout/mobile-nav.tsx
stub $C/layout/brand-logo.tsx 'export function BrandLogo(props: { size?: number; className?: string }) { void props; return null; }'
stub $C/layout/feedback-button.tsx 'export function FeedbackButton() { return null; }'
stub $C/layout/upgrade-banner.tsx 'export function UpgradeBanner() { return null; }'
stub $C/layout/version-footer.tsx 'export function VersionFooter() { return null; }'
stub $C/hosted/publish.tsx 'export function PublishPanel(props: { repoId: string }) { void props; return null; }
export function PublishOverviewSection(props: { repoId: string }) { void props; return null; }
export function PublishItFree(props: { repoId: string }) { void props; return null; }'
stub $C/hosted/hosted-nudge-slot.tsx 'export function HostedNudgeSlot(props: { candidates?: unknown; repoId?: string; className?: string }) { void props; return null; }'
rm -f $C/hosted/publish.test.tsx $C/hosted/hosted-nudge-slot.test.tsx
# the shared UI package (loader, chat avatar) still points at the logo files:
# a transparent 1x1 PNG makes every one of them render nothing, never a broken image
for f in public/repowise-logo.png public/repowise-logo-light.png; do
  [ -f "$f" ] || { echo "debrand: $f missing" >&2; exit 1; }
  printf 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=' | base64 -d > "$f"
done

swap src/app/layout.tsx 'default: "repowise",' 'default: "Code index",'
swap src/app/layout.tsx 'template: "%s — repowise",' 'template: "%s — Code index",'
swap src/app/layout.tsx 'description: "Open-source codebase documentation engine",' 'description: "Code index",'
swap src/app/page.tsx 'Everything repowise has indexed from this machine.' 'Everything indexed for this project.'
swap $C/layout/mobile-nav.tsx '<SheetTitle className="text-base">repowise</SheetTitle>' '<SheetTitle className="text-base">Code index</SheetTitle>'
swap $C/layout/app-shell-skeleton.tsx 'label="Loading Repowise"' 'label="Loading"'
swap $C/layout/whats-new-modal.tsx "What&apos;s new in repowise" "What&apos;s new"
swap $C/repos/empty-repos-state.tsx 'Point repowise at a local git repository' 'Point the index at a local git repository'
swap $C/search/command-palette.tsx 'Ask repowise: ' 'Ask: '
swap $C/search/command-palette.tsx '"Ask repowise…"' '"Ask…"'
swap $C/settings/display-section.tsx 'label: "Ask Repowise",' 'label: "Ask",'
swap $C/settings/display-section.tsx 'Show the Ask Repowise chat pill' 'Show the Ask chat pill'
swap $C/settings/display-section.tsx 'Unavailable while Ask Repowise is hidden.' 'Unavailable while Ask is hidden.'

# the Ask chat and the repowise.dev tips: always hidden
swap src/lib/config.ts 'getChatDockHidden: () => read(KEYS.chatDockHidden) === "1",' 'getChatDockHidden: () => true,'
swap src/lib/config.ts 'getChatAskControlsHidden: () => read(KEYS.chatAskControlsHidden) === "1",' 'getChatAskControlsHidden: () => true,'
swap src/lib/config.ts 'getChatSelectionAskHidden: () => read(KEYS.chatSelectionAskHidden) === "1",' 'getChatSelectionAskHidden: () => true,'
swap src/lib/config.ts 'return read(KEYS.hostedTipsHidden) === "1";' 'return true;'
echo "debrand: ok"
