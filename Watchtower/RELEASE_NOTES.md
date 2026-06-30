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

## 1.1.2 (2026-06-19)

- - Refactored license management to support packaged executables.

## 1.1.3 (2026-06-19)

- - Refactored license management to support packaged executables.

## 1.1.4 (2026-06-19)

- Enhance license path handling and add folder opening option after activation.

## 1.1.5 (2026-06-19)

- Fixed minimap reload logic.

## 1.1.6 (2026-06-30)

- Added a label to display the last trigger time in the Monitor UI.
- Improved layout of UI components for better responsiveness.
- Replaced the text widget with a label for last trigger time updates.
- Updated logging mechanism to print messages instead of using a text widget.
- Enhanced escape order handling in the process tower, including new methods for managing escape order combos.
- Introduced new functions for reading scan address entries and handling pointer chains with offsets.
- Improved map pointer handling with fallback mechanisms and calibration for better accuracy.
- Updated scan addresses for the MapOverlay to reflect changes in the game module and offsets.

## 1.1.7 (2026-06-30)

- Add ghost app functionality and improve map state handling in process tower.

## 1.1.8 (2026-06-30)

- Enhance ghost close functionality with countdown and status updates.

