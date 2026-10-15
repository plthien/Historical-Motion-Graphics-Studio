# Scene Asset Upload and Inline Beat Approval

Release target: `3.0.0-external-authoring.11`

## Scope

Scene Assets now supports direct user image upload per beat and an inline **Approve Beat Image** action beside **Regenerate Image**.

## Upload semantics

- JPEG, PNG, and WebP only.
- Uploads use the same normalized image-validation policy as Style Bible visual-reference uploads.
- Uploaded files are persisted as immutable Scene Asset image artifacts with `provider_source=user_upload`.
- An upload becomes the beat's Current Draft immediately, but is never auto-approved.
- Existing approved continuity authority remains unchanged until the user explicitly approves another image.
- Uploaded candidates are validated as exact beat-owned artifacts without manufacturing fake MediaTasks.
- Upload publication uses the current Scene Asset Manifest CAS boundary and preserves immutable history.

## Continuity

A continuous beat may upload or approve a candidate only when its immediately previous beat has an approved Scene Asset. This prevents manual upload from bypassing the approved-only continuity contract.

Changing only the Current Draft does not invalidate later beats. Replacing or clearing an approved source continues to invalidate later beats in the same continuity group through the existing approved-source invalidation path.

## UI

Each beat exposes **Upload Image**. The uploaded draft becomes the primary preview and is visibly marked **Uploaded**.

When the displayed draft is not already approved, **Approve Beat Image** appears directly beside **Regenerate Image**. The action approves the displayed Current Draft/candidate and retains the existing warning when approval would replace a continuity source and invalidate later beats.

Candidate-level review remains available in expanded details.

Once a beat image is approved, generation controls for that beat are locked: **Generate**, **Retry**, and **Regenerate Image** are unavailable until the user explicitly chooses **Clear Approval**. The backend enforces the same rule, so stale or direct API requests cannot replace an approved continuity authority. After Clear Approval, Regenerate becomes eligible again when a reviewable draft/candidate remains, and the existing downstream continuity invalidation path applies.

## Validation and regression coverage

Coverage includes:

- upload before first Scene Asset Manifest exists;
- immutable candidate/current-draft publication without auto-approval;
- multipart API upload;
- continuous upload blocked until previous approval;
- continuous uploaded candidate approval;
- exact uploaded-artifact beat ownership;
- generated-candidate MediaTask ownership preserved;
- Style Bible upload validator regression coverage;
- static Scene Assets UI actions and current V4 route usage.
