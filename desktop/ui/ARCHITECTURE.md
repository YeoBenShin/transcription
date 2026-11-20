# desktop/ui/

## How it works

The layout follows OpenSuperWhisper: one narrow window with a search field at the top, a list of cards, a round record button, and a bottom bar with the drop hint and the settings button.

| File | Role |
|---|---|
| `main_window.py` | `MainWindow`: search (200 ms delay), a stack of 3 pages (list, live, transcript), record button, bottom bar (Files…, Folder…, Link…, Merge…, settings), drag and drop, quit rules. |
| `cards.py` | `HistoryCard` (3-line preview, date and time, actions on hover: copy, show in folder, finish, move to trash) and `JobCard` (name, status, busy bar, stop, remove, log). |
| `pages.py` | `LivePage` (state, clock, closed lines, draft line) and `TranscriptPage` (full transcript, search matches marked, actions). |
| `record_button.py` | Painted circle with a glow: idle (white in dark mode, blue in light mode), recording (red with a stop square), busy (spinning arc). |
| `models_view.py` | `DownloadManager` (one thread per download, signals in the GUI thread) and `ModelList` / `ModelRow` (Download, progress, Cancel, Select, Selected, Delete). |
| `dialogs.py` | `OnboardingDialog`, `SettingsDialog` (Model, Recording, Output), `RecordingSettings`, `MergeDialog`, `LinkDialog`. |
| `theme.py` | Light and dark colors (OpenSuperWhisper palette), the style sheet and the Qt palette. Follows the system color scheme and changes with it. |
| `icons.py` | Line icons as inline SVG, drawn in the secondary text color. |

## Decisions

- Fusion style with an own style sheet, so the app looks the same on macOS and Windows and matches the OpenSuperWhisper look.
- Live text in the main window, not in a floating indicator: a meeting transcript is read while it grows. OpenSuperWhisper's mini recorder is for dictation.
- A transcript opens in its own page, not inline in the card: meeting transcripts are long.
- The download manager belongs to the app, not to a dialog: a download goes on when the settings close, and a row shows the progress again when it opens.
- Onboarding Continue is enabled only when the selected model is installed: without a model, the record button and the jobs cannot run.
- The list is built again on each change (the item count is small). The scroll position is kept.
- No global hotkey and no paste into other apps: the app is for meetings and files, not dictation.
