# Changelog

## [0.4.4] - 2026-08-19

### Fixed

- an administrator or assigned master can complete an active visit from the scheduled start time
  when the procedure finishes earlier than the availability window; completion before the start
  remains blocked and no-show remains available only after the scheduled end;
- staff appointment reminders now contain a tenant-authorized «Перейти к записи» callback instead
  of the client-facing master contact button.

### Changed

- CRM client history is paginated and shows business-local date/time, master, localized appointment
  status, recorded service price, prepayment status, confirmed amount and refunds;
- operator documentation now describes the updated completion boundary, reminder action and
  financial-history semantics. No database migration is required for this release.

## [0.4.3] - 2026-08-16

### Fixed

- maximum-length service and master cards are split into valid balanced Telegram HTML messages;
- service-card navigation handles identical pages, media/text transitions and an empty catalog
  without stale active controls;
- YooKassa file secrets are mounted into both `bot` and `api`, while unconfirmed production
  fiscalization remains fail-closed and does not disable manual prepayments;
- production backup jobs fail when disabled, reject an unexpected/empty database and use explicit
  Compose project/environment bindings;
- irreversible actions use atomic actor/business/object-bound confirmations with a short TTL;
- retries of composite broadcasts do not resend a media phase already persisted in PostgreSQL;
  Telegram does not support idempotency keys, so a crash between the API response and that
  checkpoint still has a narrow duplicate-delivery window rather than an exactly-once guarantee;
- backup/restore Compose uses a dedicated `restore_postgres_password` and a separate restricted
  restore database account instead of reusing the production application password.

### Operations

- CI now runs automatically for `fix/**` and `release/**` and validates the YooKassa override;
- neutral `telegram-crm-backup*` systemd units replace legacy branded names, including freshness,
  monthly restore-test and an `OnFailure` alert hook.
- the legacy Python distribution/package identity remains internal for compatibility; a complete
  rename is deferred to a separate tested release and is not shown in user-facing bot copy.
- the manual `Release images` workflow publishes `bot` and `backup` images from an existing
  annotated SemVer tag to GHCR, refuses to overwrite an existing version tag and records both
  immutable digests; client deployments must never use floating `main` or `latest` tags.

## [0.4.2] - 2026-08-15

### Changed

- service photographs are shown immediately in the paginated client catalog while booking and
  navigation actions remain on the active card;
- PostgreSQL pools and server-side statement, lock and idle-transaction timeouts are bounded and
  configurable per process; dependency health checks use non-persistent connections;
- YooKassa webhook verification and refund submission now use short prepare/apply transactions and
  never hold row locks during provider HTTP calls;
- long Telegram media cards and broadcasts keep their full text by sending media separately when
  the 1024-character caption limit is exceeded;
- production assertions in repeat booking were replaced with explicit user-safe invariant handling;
- backup operations include a 26-hour freshness check and persistent systemd timer examples.

### Security

- dynamic HTML values in waitlist, reminder, repeat-booking and CRM flows are escaped, while
  arbitrary staff and broadcast messages are sent without Telegram HTML parsing;
- permanent review, service and availability deletion is restricted to a verified owner in the
  same business and remains separately confirmed and audited;
- documentation now consistently describes `ADMIN_TELEGRAM_IDS` as bootstrap-only.

## [0.4.1] - 2026-08-15

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
- negotiated-price services via an explicit zero price, with automatic fixed-prepayment reset;
- bounded pagination for growing administrative and client lists.
- a complete Russian operator guide covering client, master and administrative workflows.
- separate quick-start, daily master, client and VPS deployment guides.

### Changed

- upcoming appointment cards no longer fail when a client has no public Telegram username;
- prepayments are split into active operations and history, with appointment/client context and
  contextual approval, rejection, contact and refund actions;
- appointment reminder settings use documented presets and bounded custom schedules;
- review and broadcast switches are managed only through the central bot-features screen;
- masters now open service-neutral availability; a compatible workstation is selected atomically
  only when a client books or reschedules, with an appointment-level PostgreSQL overlap guard and
  one business/date allocation lock shared by different services;
- cancellation and reschedule deadlines are independently configurable, and reschedule choices
  include only open windows with an eligible master, sufficient duration and current resources;
- administrator appointment cards expose complete booking, contact, resource and payment context;
- appointment and availability cards now expose stable IDs and master/resource context, while CRM
  manual booking asks for a service and matching window in two validated steps;
- a new window must fit at least one configured master service; exact service duration and
  workstation capacity are revalidated under locks when a client confirms a booking;
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
- the client service catalog and master directory use one navigable card instead of sending every
  item as a separate chat message;
- appointments, windows, services, add-ons, broadcasts, reviews, waitlists, privacy requests and
  active/payment-history screens retain their actions while navigating between bounded pages.
- master workspace appointments and pending prepayments are also bounded and paginated.
- reservation expiry is a permanent base service instead of an optional profile;
- API, reservation and backup containers now consume PostgreSQL/Redis file secrets consistently;
- backup resolves source and restore database passwords from bounded secret files, and CI runs a
  real encrypted backup-to-separate-database restore smoke test.

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
