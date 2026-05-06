# Upload page "already on Hub" mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a user opens the Upload page for a dataset that exists both locally and on the Hub (`source === "both"`), hide upload-related UI and offer a single "View on Hugging Face Hub" button instead.

**Architecture:** Pass `source` from Landing's dataset picker through `react-router` navigation state into the Upload page. Upload reads it, computes `isAlreadyOnHub`, and gates three render blocks (Upload Configuration card, Upload+Skip button pair, About-Hub info box) on it. Replaces the button pair with a single View button when `isAlreadyOnHub`.

**Tech Stack:** React + Vite + react-router. No backend changes. No new types beyond reusing the existing `DatasetSource` from `@/lib/replayApi`.

**Repo conventions:** No test suite, no linter, no build step (per [CLAUDE.md](../../../CLAUDE.md)). Validation is manual — `lelab --dev` + browser. Each task ends with a type check and a commit.

**Spec:** [docs/superpowers/specs/2026-05-06-upload-page-already-on-hub-design.md](../specs/2026-05-06-upload-page-already-on-hub-design.md)

---

## File Structure

Two files, both pre-existing:

- Modify [`frontend/src/pages/Landing.tsx`](../../../frontend/src/pages/Landing.tsx) — pass `source` in navigation state.
- Modify [`frontend/src/pages/Upload.tsx`](../../../frontend/src/pages/Upload.tsx) — extend `DatasetInfo`, add viewer helper, gate three render blocks, swap button pair.

No new files. No file splits.

---

## Task 1: Landing — pass `source` in navigation state

**Files:**
- Modify: [`frontend/src/pages/Landing.tsx`](../../../frontend/src/pages/Landing.tsx) (around lines 100-108, the `handlePickExisting` function)

- [ ] **Step 1: Update the navigation call**

In [`frontend/src/pages/Landing.tsx`](../../../frontend/src/pages/Landing.tsx), find:

```tsx
  const handlePickExisting = (item: DatasetItem) => {
    if (item.source === "local" || item.source === "both") {
      navigate("/upload", {
        state: { datasetInfo: { dataset_repo_id: item.repo_id } },
      });
      return;
    }
    openHubViewer(item.repo_id, item.private);
  };
```

Replace with:

```tsx
  const handlePickExisting = (item: DatasetItem) => {
    if (item.source === "local" || item.source === "both") {
      navigate("/upload", {
        state: {
          datasetInfo: {
            dataset_repo_id: item.repo_id,
            source: item.source,
          },
        },
      });
      return;
    }
    openHubViewer(item.repo_id, item.private);
  };
```

- [ ] **Step 2: Type check**

```bash
cd frontend && npx tsc --noEmit
```

Expected: exits 0. The added `source` field doesn't constrain `state` (react-router's `state` is typed `unknown` / `any`), so no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Landing.tsx
git commit -m "feat(landing): pass dataset source to upload page navigation"
```

---

## Task 2: Upload — read `source`, gate render blocks, swap button pair

**Files:**
- Modify: [`frontend/src/pages/Upload.tsx`](../../../frontend/src/pages/Upload.tsx)

This is the bulk of the change. We split it into small steps so each is reviewable.

- [ ] **Step 1: Add the `DatasetSource` import and extend `DatasetInfo`**

At the top of [`frontend/src/pages/Upload.tsx`](../../../frontend/src/pages/Upload.tsx), find the existing import for `useApi`:

```tsx
import { useApi } from "@/contexts/ApiContext";
```

Add immediately below it:

```tsx
import { DatasetSource } from "@/lib/replayApi";
```

Then find the `DatasetInfo` interface (around line 33):

```tsx
interface DatasetInfo {
  dataset_repo_id: string;
  single_task: string;
  num_episodes: number;
  saved_episodes?: number;
  session_elapsed_seconds?: number;
  fps?: number;
  total_frames?: number;
  robot_type?: string;
}
```

Replace with:

```tsx
interface DatasetInfo {
  dataset_repo_id: string;
  single_task: string;
  num_episodes: number;
  saved_episodes?: number;
  session_elapsed_seconds?: number;
  fps?: number;
  total_frames?: number;
  robot_type?: string;
  source?: DatasetSource;
}
```

- [ ] **Step 2: Carry `source` through the success-path setState**

In the same file, find the `loadDatasetInfo` effect's success branch (around lines 97-104):

```tsx
        if (response.ok && data.success) {
          // Merge the loaded dataset info with any session info we have
          setDatasetInfo({
            ...data,
            saved_episodes: data.num_episodes, // Use actual episodes from dataset
            session_elapsed_seconds:
              initialDatasetInfo.session_elapsed_seconds || 0,
          });
        } else {
```

Replace with:

```tsx
        if (response.ok && data.success) {
          // Merge the loaded dataset info with any session info we have
          setDatasetInfo({
            ...data,
            saved_episodes: data.num_episodes, // Use actual episodes from dataset
            session_elapsed_seconds:
              initialDatasetInfo.session_elapsed_seconds || 0,
            source: initialDatasetInfo.source,
          });
        } else {
```

(The fallback path `setDatasetInfo(initialDatasetInfo)` already carries `source` because it spreads the whole object.)

- [ ] **Step 3: Add the `openInHubViewer` helper**

In the same file, find the `formatDuration` function (around line 132):

```tsx
  const formatDuration = (seconds: number): string => {
```

Insert immediately ABOVE it:

```tsx
  const openInHubViewer = (repoId: string) => {
    const spacePath = `/spaces/lerobot/visualize_dataset?path=${encodeURIComponent(`/${repoId}`)}`;
    // The user owns/manages the dataset (it appears under their hub
    // listing), so login-redirect always works whether public or
    // private. Avoids passing `private` through navigation state.
    const target = `https://huggingface.co/login?next=${encodeURIComponent(spacePath)}`;
    window.open(target, "_blank", "noopener,noreferrer");
  };

```

(Blank line after the closing brace, before `formatDuration`.)

- [ ] **Step 4: Compute `isAlreadyOnHub` inside the render**

Find the start of the JSX `return` (around line 262):

```tsx
  return (
    <div className="min-h-screen bg-black text-white p-8">
```

Replace with:

```tsx
  const isAlreadyOnHub = datasetInfo.source === "both";

  return (
    <div className="min-h-screen bg-black text-white p-8">
```

- [ ] **Step 5: Gate the Upload Configuration card**

Find the Upload Configuration card (around lines 391-444):

```tsx
            {/* Upload Configuration */}
            <div className="bg-gray-900 rounded-lg p-6 border border-gray-700 mb-8">
              <h2 className="text-xl font-semibold text-white mb-6">
                Upload Configuration
              </h2>
```

…through its closing `</div>` (the one matching the opening `div` on line 392, ending around line 444).

Wrap the entire `{/* Upload Configuration */}` block in a conditional. The full edit: find:

```tsx
            {/* Upload Configuration */}
            <div className="bg-gray-900 rounded-lg p-6 border border-gray-700 mb-8">
              <h2 className="text-xl font-semibold text-white mb-6">
                Upload Configuration
              </h2>

              <div className="space-y-6">
                {/* Tags */}
                <div>
                  <Label htmlFor="tags" className="text-gray-300 mb-2 block">
                    Tags (comma-separated)
                  </Label>
                  <Input
                    id="tags"
                    value={tagsInput}
                    onChange={(e) => setTagsInput(e.target.value)}
                    placeholder="robotics, lerobot, manipulation"
                    className="bg-gray-800 border-gray-600 text-white"
                  />
                  <p className="text-sm text-gray-500 mt-1">
                    Tags help others discover your dataset on HuggingFace Hub
                  </p>
                </div>

                {/* Privacy Setting */}
                <div className="flex items-center space-x-3">
                  <Checkbox
                    id="private"
                    checked={uploadConfig.private}
                    onCheckedChange={(checked) =>
                      setUploadConfig({
                        ...uploadConfig,
                        private: checked as boolean,
                      })
                    }
                  />
                  <div className="flex items-center gap-2">
                    {uploadConfig.private ? (
                      <EyeOff className="w-4 h-4 text-gray-400" />
                    ) : (
                      <Eye className="w-4 h-4 text-gray-400" />
                    )}
                    <Label htmlFor="private" className="text-gray-300">
                      Make dataset private
                    </Label>
                  </div>
                </div>
                <p className="text-sm text-gray-500 ml-6">
                  {uploadConfig.private
                    ? "Only you will be able to access this dataset"
                    : "Dataset will be publicly accessible on HuggingFace Hub"}
                </p>
              </div>
            </div>
```

Replace with the same block wrapped in `{!isAlreadyOnHub && (...)}`:

```tsx
            {/* Upload Configuration */}
            {!isAlreadyOnHub && (
              <div className="bg-gray-900 rounded-lg p-6 border border-gray-700 mb-8">
                <h2 className="text-xl font-semibold text-white mb-6">
                  Upload Configuration
                </h2>

                <div className="space-y-6">
                  {/* Tags */}
                  <div>
                    <Label htmlFor="tags" className="text-gray-300 mb-2 block">
                      Tags (comma-separated)
                    </Label>
                    <Input
                      id="tags"
                      value={tagsInput}
                      onChange={(e) => setTagsInput(e.target.value)}
                      placeholder="robotics, lerobot, manipulation"
                      className="bg-gray-800 border-gray-600 text-white"
                    />
                    <p className="text-sm text-gray-500 mt-1">
                      Tags help others discover your dataset on HuggingFace Hub
                    </p>
                  </div>

                  {/* Privacy Setting */}
                  <div className="flex items-center space-x-3">
                    <Checkbox
                      id="private"
                      checked={uploadConfig.private}
                      onCheckedChange={(checked) =>
                        setUploadConfig({
                          ...uploadConfig,
                          private: checked as boolean,
                        })
                      }
                    />
                    <div className="flex items-center gap-2">
                      {uploadConfig.private ? (
                        <EyeOff className="w-4 h-4 text-gray-400" />
                      ) : (
                        <Eye className="w-4 h-4 text-gray-400" />
                      )}
                      <Label htmlFor="private" className="text-gray-300">
                        Make dataset private
                      </Label>
                    </div>
                  </div>
                  <p className="text-sm text-gray-500 ml-6">
                    {uploadConfig.private
                      ? "Only you will be able to access this dataset"
                      : "Dataset will be publicly accessible on HuggingFace Hub"}
                  </p>
                </div>
              </div>
            )}
```

(All inner content indented one level deeper.)

- [ ] **Step 6: Swap the action button pair for a conditional**

Find the action buttons block (around lines 446-474):

```tsx
            {/* Action Buttons */}
            <div className="flex flex-col sm:flex-row gap-4 justify-center">
              <Button
                onClick={handleUploadToHub}
                disabled={isUploading}
                className="bg-blue-500 hover:bg-blue-600 text-white font-semibold py-4 px-8 text-lg"
              >
                {isUploading ? (
                  <>
                    <Loader2 className="w-5 h-5 mr-2 animate-spin" />
                    Uploading to Hub...
                  </>
                ) : (
                  <>
                    <UploadIcon className="w-5 h-5 mr-2" />
                    Upload to HuggingFace Hub
                  </>
                )}
              </Button>

              <Button
                onClick={handleSkipUpload}
                disabled={isUploading}
                variant="outline"
                className="border-gray-600 text-gray-300 hover:bg-gray-800 hover:text-white py-4 px-8 text-lg"
              >
                Skip Upload
              </Button>
            </div>
```

Replace with:

```tsx
            {/* Action Buttons */}
            <div className="flex flex-col sm:flex-row gap-4 justify-center">
              {isAlreadyOnHub ? (
                <Button
                  onClick={() => openInHubViewer(datasetInfo.dataset_repo_id)}
                  className="bg-blue-500 hover:bg-blue-600 text-white font-semibold py-4 px-8 text-lg"
                >
                  <ExternalLink className="w-5 h-5 mr-2" />
                  View on Hugging Face Hub
                </Button>
              ) : (
                <>
                  <Button
                    onClick={handleUploadToHub}
                    disabled={isUploading}
                    className="bg-blue-500 hover:bg-blue-600 text-white font-semibold py-4 px-8 text-lg"
                  >
                    {isUploading ? (
                      <>
                        <Loader2 className="w-5 h-5 mr-2 animate-spin" />
                        Uploading to Hub...
                      </>
                    ) : (
                      <>
                        <UploadIcon className="w-5 h-5 mr-2" />
                        Upload to HuggingFace Hub
                      </>
                    )}
                  </Button>

                  <Button
                    onClick={handleSkipUpload}
                    disabled={isUploading}
                    variant="outline"
                    className="border-gray-600 text-gray-300 hover:bg-gray-800 hover:text-white py-4 px-8 text-lg"
                  >
                    Skip Upload
                  </Button>
                </>
              )}
            </div>
```

- [ ] **Step 7: Gate the About-Hub info box**

Find the info box (around lines 476-504):

```tsx
            {/* Info Box */}
            <div className="mt-8 p-4 bg-blue-900/20 border border-blue-600 rounded-lg">
              <div className="flex items-start gap-3">
                <AlertCircle className="w-5 h-5 text-blue-400 mt-0.5" />
                <div>
                  <h3 className="font-semibold text-blue-400 mb-2">
                    About HuggingFace Hub Upload
                  </h3>
                  <ul className="text-sm text-gray-300 space-y-1">
                    <li>
                      • Your dataset will be uploaded to HuggingFace Hub for
                      sharing and collaboration
                    </li>
                    <li>
                      • You need to be logged in to HuggingFace CLI on the
                      server
                    </li>
                    <li>
                      • Uploaded datasets can be used for training models and
                      sharing with the community
                    </li>
                    <li>
                      • You can always upload manually later using the
                      HuggingFace CLI
                    </li>
                  </ul>
                </div>
              </div>
            </div>
```

Replace with the same block wrapped in `{!isAlreadyOnHub && (...)}`:

```tsx
            {/* Info Box */}
            {!isAlreadyOnHub && (
              <div className="mt-8 p-4 bg-blue-900/20 border border-blue-600 rounded-lg">
                <div className="flex items-start gap-3">
                  <AlertCircle className="w-5 h-5 text-blue-400 mt-0.5" />
                  <div>
                    <h3 className="font-semibold text-blue-400 mb-2">
                      About HuggingFace Hub Upload
                    </h3>
                    <ul className="text-sm text-gray-300 space-y-1">
                      <li>
                        • Your dataset will be uploaded to HuggingFace Hub for
                        sharing and collaboration
                      </li>
                      <li>
                        • You need to be logged in to HuggingFace CLI on the
                        server
                      </li>
                      <li>
                        • Uploaded datasets can be used for training models and
                        sharing with the community
                      </li>
                      <li>
                        • You can always upload manually later using the
                        HuggingFace CLI
                      </li>
                    </ul>
                  </div>
                </div>
              </div>
            )}
```

(All inner content indented one level deeper.)

- [ ] **Step 8: Type check**

```bash
cd frontend && npx tsc --noEmit
```

Expected: exits 0. The new `isAlreadyOnHub` constant is used correctly; `openInHubViewer` is defined before its call site; `ExternalLink` is already imported (used by the success-state button).

- [ ] **Step 9: Production build sanity**

```bash
cd frontend && npm run build 2>&1 | tail -10
```

Expected: ends with `✓ built in Xs`.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/pages/Upload.tsx
git commit -m "feat(upload): hide upload UI for datasets already on Hub"
```

---

## Task 3: End-to-end manual validation

**Files:** none modified.

- [ ] **Step 1: Launch the dev server**

```bash
lelab --dev
```

Open `http://localhost:8080`.

- [ ] **Step 2: Run the matrix**

| # | Action | Expected |
|---|--------|----------|
| 1 | Landing → click a Hub-only dataset | Opens HF viewer in new tab. Unchanged behavior. |
| 2 | Landing → click a Local-only dataset | /upload shows: Dataset Summary card, Upload Configuration card, Upload + Skip buttons, About-Hub info box, header has Back to Home + Trash. Unchanged from before this PR. |
| 3 | Landing → click a "both" dataset | /upload shows: Dataset Summary card, **single** "View on Hugging Face Hub" button. **No** Upload Configuration card, **no** Skip button, **no** About-Hub info box. Header still has Back to Home + Trash. |
| 4 | "Both" dataset → click "View on Hugging Face Hub" | New tab opens to `huggingface.co/login?next=...` redirecting to the dataset's visualize Space. |
| 5 | "Both" dataset → click Trash → confirm | Local copy removed, navigates home. The dataset stays on the Hub (untouched). |
| 6 | Successful upload of a Local-only dataset | Existing success state still appears with "View on HuggingFace Hub" + "Start Training" buttons. Unchanged. |

- [ ] **Step 3: Stop the dev server (Ctrl-C)**

No commit needed for validation. If anything in the matrix fails, return to Task 2 and fix.

---

## Self-review notes

- **Spec coverage**: §1 Pass source from Landing → Task 1. §2 Track source in Upload → Task 2 Steps 1-2. §3 Helper → Task 2 Step 3. §4 Conditional render (three blocks) → Task 2 Steps 4-7. §5 Edge cases → Task 3 manual matrix.
- **No placeholders**. Every step shows the exact before/after code or exact command.
- **Type consistency**: `isAlreadyOnHub` defined once in Task 2 Step 4, used in Steps 5, 6, 7. `openInHubViewer` defined in Step 3, called in Step 6. `DatasetSource` imported in Step 1, used in the interface in Step 1.
