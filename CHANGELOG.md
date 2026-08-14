# Changelog

## [0.4.1] - 2026-08-11

### Added

- business workstations with per-service assignment and collision-safe capacity allocation for
  simultaneous multi-master windows;
- a calendar-first client booking flow and master-labelled time slots;
- per-master portfolio management with owner-wide access and master self-service isolation;
- a guarded local database reset script with an automatic PostgreSQL backup by default;
- separate business address/map editing, friendly timezone selection and up to five support links;
- up to five per-master social/contact links with the selected master's direct contact in bookings;
- immutable address, map and master-contact snapshots for new appointments;
- client service and master cards with photos and direct booking actions;
- compact staff profile administration with booking visibility, service assignments and media;
- direct Telegram client-chat links in appointment details;
- recoverable, lease-protected privacy anonymization with bounded retries and worker health;
- draft/preview/publish workflow for a safe formatted welcome text and optional photo;
- service add-ons, duration ranges, media and immutable appointment snapshots;
- rolling future-booking quota with audited permissioned override;
- one-tap skip controls and safe Telegram profile links in CRM cards.

### Changed

- appointment and availability cards now expose stable IDs and master/resource context, while CRM
  manual booking asks for a service and matching window in two validated steps;
- service duration and workstation availability are validated before a window can be opened, and
  booking revalidates the selected service/resource before confirmation;
- portfolio visibility no longer removes its administration entry, so a hidden portfolio can be
  enabled again;
- appointment status rendering handles payment and refund states without a generic callback error;
- solo/salon presentation is derived from active bookable specialists; manual mode and duplicate
  specialist controls were removed from the business settings UI;
- unfinished Loyalty and Mini App switches and the obsolete master-info admin screen are hidden;
- navigation now recovers from active and stale FSM forms;
- Telegram contact sharing is accepted without treating contact-only messages as navigation;
- consent and booking copy is gender-neutral, and the primary booking action has a clear emoji;
- disabled client features refresh stale persistent keyboards instead of silently doing nothing;
- SOLO instances unify the technical master profile with the bootstrap owner while preserving data;
- window creation chooses a master in salon mode and skips the choice for a single master;
- administrative services and availability windows hide archived rows by default and expose an
  explicit archive toggle;
- upcoming appointments are grouped by day as a calendar agenda, and the window date picker
  selects today without attempting an identical Telegram edit;
- verified owners can perform a separately confirmed aggregate hard delete for a service or
  availability window;
- manual prepayment lifecycle is transactional and concurrency safe;
- staff roles, bootstrap ownership and solo/salon transitions are enforced in PostgreSQL;
- runtime copy and test fixtures are vertical-neutral and white-label.

### Security

- active staff and bootstrap identities cannot enter anonymization;
- failed anonymization stores only bounded machine codes and never exception PII;
- CRM phone visibility requires an explicit permission;
- welcome formatting uses a strict Telegram HTML whitelist and HTTPS-only links.

## [0.4.0] - 2026-08-11

### Added

- white-label `Business`, multi-master staff, DB-backed RBAC and one-time invitations;
- per-master schedules, service assignments, effective price/duration/prepayment snapshots;
- centralized feature flags, subscription grace guard and acquisition funnel;
- manual/YooKassa payment abstraction, reservations, refunds and replay-safe webhook inbox;
- privacy deletion workflow, versioned consent and PII anonymization;
- authenticated Mini App `/api/v1`, opaque Redis sessions, rate limits and security headers;
- component heartbeats, optional Sentry, encrypted restic backup/restore tooling and hardened Compose.

### Changed

- `ADMIN_TELEGRAM_IDS` is bootstrap-only; runtime authorization always uses active DB staff;
- pending payment/manual confirmation occupies a slot until confirmation, cancellation or expiry;
- runtime branding, menus and support contacts are business-driven;
- API and manual payments can run when YooKassa is not configured.

### Security

- tenant/master/client scoping is enforced in repositories and application services;
- PostgreSQL prevents overlapping active appointments for one master;
- provider webhook payload is never trusted as payment proof and is not stored or logged raw;
- secrets, contact data and payment-shaped fields are scrubbed from logs and Sentry events.

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
