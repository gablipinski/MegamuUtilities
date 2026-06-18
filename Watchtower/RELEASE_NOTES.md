# Watchtower Release Notes

## 1.0.0 (2026-06-04)

- Initial commercial release.
- Centralized version metadata for build, installer, and UI.

## 1.0.1 (2026-06-09)

- feat: Add functionality to view last trigger snapshot and position popups at main window

## 1.0.2 (2026-06-17)

- feat: Enhance action controller and monitor UI with text input support and escape route management
- Added support for typing text in the action controller, allowing for text actions in escape routes.
- Implemented loading and saving of escape routes from a JSON configuration file.
- Introduced a UI for selecting and editing escape routes, including options to create, delete, and modify routes.
- Enhanced the player monitor with adaptive color calibration features for improved detection accuracy.
- Updated the escape route editor to accommodate text actions alongside clicks and key presses.
- Added a new JSON configuration file for predefined escape routes.

## 1.0.3 (2026-06-17)

- Created the new operational mode: PROCESS TOWER
- Add scan_addresses.json for address scanning configuration
- Introduced a new JSON configuration file to define memory addresses for scanning.
- Added entries for SLDetection and MapOverlay with relevant details including offsets and descriptions.

## 1.0.4 (2026-06-18)

- feat: Add config folder content to watchtower build chain

## 1.0.5 (2026-06-18)

- feat: Add config folder content to watchtower build chain

## 1.0.6 (2026-06-18)

- Filter process list to include only 'megamu' related processes in MonitorUI.

## 1.1.0 (2026-06-18)

- Introduced a new configuration file `config.py` to centralize runtime settings including notification options, character detection parameters, and minimap settings.
- Created `scan_addresses.py` to define memory addresses for scanning, including SLDetection and MapOverlay.
- Implemented common components in `common_components.py` for UI button creation and process memory reading functions.
- Developed `process_tower.py` to manage process scanning, including starting and stopping scans, and handling key triggers based on detected characters.
- Added `spot_tower.py` for monitoring player detection with an asynchronous approach, integrating action sequences upon detection.

## 1.1.1 (2026-06-18)

- Add escape order and delay controls to Process Tower functionality.

## 1.1.2 (2026-06-18)

- Add escape order and delay controls to Process Tower functionality.

