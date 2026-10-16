# P0 — Historical Motion Graphics / Remotion technical proof

> **Technical experiment only.** This folder does not run the Historical Studio product.
> It does not open, migrate or use a YTB Studio database, run an LLM API,
> synthesize narration, align words, clear archive licenses or ship a historical film.
> P1 is a separate, unimplemented clean cut.

## Scope and timeline

The POC uses Remotion 4.0.535, one `DocumentaryComposition` for both
`@remotion/player` and the CLI exporter, and a frozen
`src/plan.mjs` fixture (`history_motion_poc.v1`).
Three consecutive Visual Setups (0–569, 570–1199, 1200–1799, inclusive)
contain independent animated layers, editorial headlines, stamps,
a moving schematic map and illustrative archival-photo placeholder.
The technical demo is **silent** and contains no approved historical footage.

## Prerequisites

- Windows 10/11 (CI Windows Server 2022) or Linux for independent proof.
- **Node.js 22**, npm with access to the registry for the first `npm ci`.
- **FFmpeg and FFprobe on PATH before running the test suite or rendering.**
  Verify in PowerShell: `ffmpeg -version` and `ffprobe -version`.
- Chrome/Chromium: Remotion can provision its Chrome Headless Shell;
  Playwright's Chromium is separate and required for the Player smoke.
- Filesystem permission to install npm packages, browser binaries and write to `out/`.

Remotion's `remotion`, `@remotion/cli` and `@remotion/player` are pinned to
**4.0.535**. Install with `npm ci` (never use an unpinned root installation).

## Install

From the repository root:

```bash
cd experiments/remotion-history-poc
npm ci
npx playwright install chromium
npx remotion browser ensure
```

CI on Linux installs Playwright system dependencies with `npx playwright install --with-deps chromium`.
On Windows, install a trusted FFmpeg package and ensure both `ffmpeg.exe` and `ffprobe.exe` are visible in the active terminal.

## Preview and seek

```bash
npm run dev
```

Open **http://127.0.0.1:5174/**. Three Visual Setup jump controls
seek to global frames 0, 570 and 1200. Playback and pause are real
`@remotion/player` controls; the displayed frames come from the same
React composition used by the export.

Stop the development server first. Then, after Chromium is installed, run:

```bash
npm run test:player
```

This runs real browser play/pause, seeking, setup jumps, map/route geometry
checks and exports native Player screenshots under `out/`.
The test starts its own Vite process; don't run a second server on port 5174.

## Validation and build

```bash
npm test
npm run build
```

Tests cover strict plans (unknown fields, duplicate IDs, setup/event frame
boundaries), safe functional layout zones, filesystem path containment
and symlinks, media signature/codec/decoding, missing/corrupt/oversized
media, render preflight, canonical plan identity and POC regressions.
**FFmpeg + FFprobe must be installed for media-byte tests**, including the
real WAV test; an extension alone does not authorize media.

## Render and independently verify

```bash
npm run render
npm run verify:video
```

`npm run render` invokes `scripts/render.mjs`, NOT unguarded Remotion
directly. It checks the frozen, canonical plan, resolves assets inside
`public/`, checks signature, size, decodeability and dimensions/duration
before launching Chromium. Alternate P0 plans are rejected rather than
silently rendering the built-in fixture. An optional diagnostic,
`node scripts/render.mjs --preflight-only`, runs the same preflight
without starting the renderer.

Expected output: **`out/history-poc.mp4`**, H.264 1920×1080,
**30/1 fps**, 1800 decoded frames, 60.000 seconds, **no audio track**.
`verify:video` independently uses FFprobe and fully decodes with FFmpeg.

No remote media, arbitrary executable JSX, old image-per-beat pipeline
or production export API is supported in this POC.

## CI evidence and artifacts

The **History Remotion P0 proof** workflow at
`../../.github/workflows/history-remotion-poc.yml` runs exact-head
Linux and Windows builds and renders. Its Linux artifact contains the
video, 1920×1080 Player captures, sampled decoded MP4 frames at
00/05/10/19/20/25/35/39/40/45/55/59 seconds, the chronological
contact sheet and `out/parity/report.json` (per-frame Player/MP4 RGB
differences). Windows uploads its MP4. The workflow log records both
FFprobe values and exact tested SHA.

To inspect evidence, visit this repository's **Actions → History Remotion P0 proof →
latest successful run → Artifacts**. The video is not committed to Git.

## Historical/editorial limitations

- The archival photo is an explicit **PLACEHOLDER**, not authentic archive evidence.
- The map is **SCHEMATIC NOT TO SCALE**, not historically verified borders or travel path.
- No real photo license, author, date, source or attribution has been approved.
- The date/place label illustrates the technical presentation and is not a researched documentary script.
- External reference-style parity is **REFERENCE_STYLE_COMPARISON_NOT_VERIFIED**
  unless an actual benchmark video is provided and compared.
- No voice WAV, forced alignment, finished history application or P1 runtime is included.

## Troubleshooting

- **Missing Chromium:** rerun `npx remotion browser ensure`; for Player
  use `npx playwright install chromium`.
- **FFmpeg or FFprobe not found:** install both and reopen PowerShell so
  the updated PATH is applied; `npm test` needs these for genuine media checks.
- **`MOTION_MEDIA_INVALID` or `media_decode`:** confirm the local asset
  is real decodable media, not renamed text; inspect its size/header/codec;
  keep files inside `public/`, never symlink outside it.
- **`P0_PLAN_IDENTITY_MISMATCH`:** the fixed P0 renderer accepts only
  `motionPlan`; do not pass an alternate fixture without a separately
  designed and approved plan-forwarding contract.
- **Windows local paths:** do not give absolute `C:\\...`, `..\`,
  URLs or drive paths to plan assets. Assets use safe relative
  paths under `public/`.
- **Port 5174 already in use:** stop the existing Vite instance.
  `test:player` launches a strict port 5174 server.
- **Damaged npm install:** run `npm ci` again using Node 22.
