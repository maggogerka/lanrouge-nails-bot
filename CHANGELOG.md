# Changelog

## [0.3.1] - 2026-07-24

### Added

- status-aware retention timestamps for appointment reference photos;
- six-hour cleanup worker, safe CLI dry-run/execute and structured health alerts;
- confirmed administrator cleanup and unrestricted client privacy deletion;
- Docker log rotation and configurable draft/terminal retention periods.

### Changed

- reference drafts expire from Redis after 24 hours by default;
- rescheduling moves active references and recalculates their expiry;
- stored Telegram identifiers are anonymized after expiry while Appointment history remains.

### Security

- the application continues to store no reference-image binaries or local photo files;
- cleanup logs and reports contain no Telegram file IDs or client PII.
