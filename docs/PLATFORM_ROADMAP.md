# FAM Market Manager — Platform Evolution Roadmap

> **Document Version:** 1.0
> **Date:** March 2, 2026
> **Based on:** FAM Market Manager v1.5.1 codebase analysis
> **Purpose:** Strategic roadmap for evolving from a single-workstation offline desktop app into a full multi-market, multi-organization platform comparable to Healthy Ways and Double Up Food Bucks.

---

## Table of Contents

1. [Current State Assessment](#a-current-state-assessment)
2. [Phased Roadmap](#b-recommended-roadmap-by-phases)
3. [Feature Scoring Table](#c-feature-scoring-table)
4. [Specific Area Evaluations](#d-specific-area-evaluations)
5. [Realism Check](#e-realism-check)
6. [Top 10 Recommendations](#f-top-10-recommendations)

---

## A) Current State Assessment

### Architecture Summary

FAM Market Manager is a **monolithic desktop application** built with Python 3.12 + PySide6 (Qt6), packaged as a standalone Windows `.exe` via PyInstaller. It uses a local SQLite database with WAL mode and thread-local connections. The architecture is a clean 3-layer stack:

```
┌─────────────────────────────────────┐
│  UI Layer (PySide6 / Qt6)           │  7 screens, sidebar nav, tutorial
│  ~300K lines QSS-styled widgets     │
├─────────────────────────────────────┤
│  Models Layer (CRUD + queries)      │  7 model modules, audit log
│  Pure Python, no ORM               │
├─────────────────────────────────────┤
│  Database Layer (SQLite)            │  13 tables, 10 schema versions
│  Thread-local connections, WAL      │  Triggers, indexes, migrations
└─────────────────────────────────────┘
```

### Existing Capabilities

| Capability | Status | Notes |
|---|---|---|
| Market day lifecycle | Complete | Open/close/reopen with audit |
| Multi-receipt customer orders | Complete | Sequential labels, returning customer detection |
| Configurable payment methods | Complete | 0-999% match, per-market assignment |
| Daily match cap per customer | Complete | Proportional reduction, penny-accurate |
| FMNP external program tracking | Complete | Separate from receipt flow |
| Transaction adjustments | Complete | Receipt total, vendor, payment methods, void |
| Append-only audit log | Complete | All actions logged with reason codes |
| CSV export (6 report types) | Complete | Vendor reimbursement, FAM match, ledger, audit, geolocation, FMNP |
| Charts and visualization | Complete | 5 chart types via Matplotlib |
| Geolocation (zip code) | Complete | pgeocode lat/lon, folium heat maps |
| Automatic ledger backup | Complete | Human-readable text fallback |
| Interactive tutorial | Complete | 11 steps, 20 detail hints, auto-launch on first run |
| Single-instance prevention | Complete | Windows named mutex |

### Capability Gaps for a Full Platform

| Capability | Gap | Severity |
|---|---|---|
| User authentication and login | None exists | **Critical** |
| Role-based access control | None exists | **Critical** |
| Multi-workstation sync | No network capability | **Critical** |
| Cloud/server database | SQLite only | **Critical** |
| Multi-organization tenancy | Single-org implicit | High |
| Persistent customer accounts | Day-scoped labels only | High |
| API layer (REST/GraphQL) | No backend server | High |
| Mobile apps | Desktop-only | High |
| Payment terminal integration | Manual entry only | High |
| SNAP/EBT processing | Reference code field only | High |
| Data encryption | None | Medium |
| Automated backups to cloud | Local file only | Medium |
| Cross-platform support | Windows-only mutex | Medium |
| Vendor self-service portal | Admin-only config | Medium |
| Real-time dashboards | Static charts | Low |

### Technical Constraints Affecting the Roadmap

1. **SQLite** — Single-writer, file-based. Cannot support concurrent multi-machine writes. Must migrate to client-server DB (PostgreSQL) or use a sync protocol.
2. **PySide6 (Qt6)** — Excellent for desktop, but not portable to web or mobile. A web/mobile strategy requires a separate frontend or rewrite.
3. **No API layer** — Models call SQLite directly. An API server must be introduced between clients and database.
4. **Windows-only code** — `ctypes.windll` mutex, PyInstaller Windows spec. Cross-platform requires abstracting platform-specific code.
5. **No ORM** — Raw SQL in models. Migrating to PostgreSQL requires rewriting queries or adopting SQLAlchemy.
6. **Tightly coupled UI-to-Data** — UI screens call model functions directly. Decoupling into API calls is the prerequisite for multi-workstation.
7. **~300K of Qt widget code** — Significant investment. Rewriting for web is a major effort; maintaining dual desktop+web adds complexity.

### Current Technology Stack

| Component | Technology | Version |
|---|---|---|
| Language | Python | 3.12 |
| UI Framework | PySide6 (Qt6) | 6.5+ |
| Database | SQLite | 3.41+ (bundled with Python) |
| Charting | Matplotlib | 3.7+ |
| Data Processing | pandas | 2.0+ |
| Mapping | folium + pgeocode | 0.14+ / 0.4+ |
| Packaging | PyInstaller | 6.19.0 |
| Testing | pytest | 9.0+ |
| Tests | 229 passing | 5 test files |

---

## B) Recommended Roadmap by Phases

### Phase 0: Harden and Prepare (Desktop Foundation)

**Objective:** Solidify the offline desktop app, add authentication groundwork, and prepare the codebase for future network capability.

**Features:**
- Local user accounts with PIN or password (stored hashed in new `users` table)
- Role definitions: Admin, Manager, Volunteer (permission flags per role)
- Lock sensitive operations behind roles (void, adjust, settings changes, close market day)
- Persistent customer database (cross-day customer lookup by name/ID)
- Database encryption at rest (SQLCipher or application-level encryption)
- Automated local backup rotation (scheduled DB copy, not just ledger text)
- Cross-platform single-instance check (replace Windows mutex with file lock or Qt shared memory)
- Abstract all raw SQL behind a data-access interface (prepare for future ORM/API swap)

**Key Dependencies:** None external. All local code changes.

**Key Risks:**
- Scope creep — must resist adding network features here
- SQLCipher adds a native dependency that complicates PyInstaller builds

**Definition of Done:**
- Login screen gates app access; roles restrict UI elements
- Existing 229 tests pass + new auth/role tests added
- Database encrypted at rest; backup rotation working
- App builds and runs on Windows 10/11 with no regressions

**Product Direction Change:** FAM Manager becomes a credible single-site tool suitable for real nonprofit deployment, not just a prototype.

---

### Phase 1: API Server and Multi-Workstation Sync

**Objective:** Introduce a backend server so multiple workstations at the same market can share data in real time, with offline fallback.

**Features:**
- REST API server (FastAPI or Django REST Framework) wrapping existing models
- PostgreSQL database (replaces SQLite for server; SQLite retained for offline cache)
- Client-server architecture: desktop app becomes an API client
- Offline-first sync: local SQLite queue pushes to server when online
- Conflict resolution strategy (last-write-wins with server timestamp, or operational transform for concurrent edits)
- Source device identifiers on all records (device UUID stamped on transactions)
- Real-time WebSocket notifications (new transaction, market day status changes)
- Centralized audit log (server-authoritative, append-only)
- JWT-based authentication (tokens issued by server, validated on each request)

**Key Dependencies:**
- Server hosting (cloud VM, managed DB, or PaaS like Railway/Render)
- Domain + TLS certificate
- Network connectivity at market sites (or cellular hotspot)

**Key Risks:**
- Offline/online sync is architecturally complex — conflict resolution is the hardest problem
- Network reliability at outdoor farmers markets is unpredictable
- Two codebases to maintain (server + desktop client)
- Migration of existing local databases to server

**Definition of Done:**
- 2+ workstations at the same market see each other's transactions within 5 seconds
- App works fully offline; syncs automatically when connectivity restored
- No data loss on network interruption mid-transaction
- Server deployed and accessible from market WiFi/cellular

**Product Direction Change:** FAM Manager becomes a connected system. Requires ongoing server operations (hosting, monitoring, backups).

---

### Phase 2: Multi-Market and Multi-Organization Platform

**Objective:** Support multiple markets under one organization, and multiple organizations on the same platform (multi-tenant).

**Features:**
- Organization entity (tenant): each org has its own markets, vendors, payment methods, users
- Tenant isolation: data fully separated per organization (schema-level or row-level)
- Organization admin dashboard (web-based): manage markets, users, payment configs
- Cross-market reporting: aggregate vendor reimbursement, FAM match totals across all markets in an org
- Hierarchical roles: Org Admin, Market Manager, Volunteer
- Vendor sharing across markets within an org (already partially supported via junction tables)
- Configurable match programs per organization (different match rules per org)
- Data export at org level (combined CSV, PDF reports)
- Organization onboarding workflow (self-service or admin-provisioned)

**Key Dependencies:**
- Phase 1 API server operational
- Web admin dashboard (new frontend — React, Vue, or similar)
- Email service for invitations and notifications

**Key Risks:**
- Multi-tenant data isolation is security-critical — a bug leaks org A's data to org B
- Schema complexity increases significantly
- Web dashboard is a new frontend to build and maintain
- Org-level reporting queries become expensive at scale

**Definition of Done:**
- Two separate organizations can operate on the same server with zero data leakage
- Org admin can create markets, invite users, configure payment methods via web dashboard
- Cross-market reports aggregate correctly
- Penetration test confirms tenant isolation

**Product Direction Change:** FAM Manager becomes a platform. Requires product management, SLA commitments, and support processes.

---

### Phase 3: Vendor Portal and Mobile Apps

**Objective:** Give vendors self-service capability and extend the platform to mobile devices for field use.

**Vendor Portal (Web):**
- Vendor self-registration (submit name, contact, markets they attend)
- View reimbursement status and history
- Download payment summaries
- Update contact information and market attendance
- Admin approval workflow for new vendor registrations

**Mobile App (React Native or Flutter):**
- Volunteer mode: receipt intake, payment processing, customer order management
- Offline-capable with local storage + sync (same pattern as Phase 1)
- Camera integration for receipt photo capture (attach to transaction)
- Barcode/QR scanning for customer ID (future card system)
- Push notifications (market day opened, approaching match limit)
- GPS-based market check-in (auto-detect which market the device is at)

**Shared:**
- Unified API serves desktop, web portal, and mobile clients
- Responsive web dashboard works on tablets

**Key Dependencies:**
- Phase 2 multi-tenant API
- App store accounts (Apple Developer $99/yr, Google Play $25 one-time)
- Mobile device provisioning for market volunteers
- Receipt photo storage (S3 or equivalent)

**Key Risks:**
- Mobile app doubles the client surface area (iOS + Android + Desktop + Web)
- Offline sync on mobile is harder (intermittent connectivity, battery constraints)
- App store review processes add release latency
- Volunteer training burden increases with new form factors

**Definition of Done:**
- Vendors can register, view reimbursements, and download reports via web portal
- Mobile app completes full receipt-to-payment-to-confirmation flow offline
- Syncs to server within 30 seconds of connectivity
- Available on both iOS and Android app stores

**Product Direction Change:** FAM Manager becomes a multi-client ecosystem. Requires mobile DevOps, app store management, and device support.

---

### Phase 4: Payments, Card Loading, and Terminal Integration

**Objective:** Add real payment processing capabilities — SNAP/EBT integration, customer wallet/card system, and PAX terminal compatibility.

**Customer Accounts and Wallet:**
- Persistent customer profiles (name, contact, unique ID/card number)
- Balance tracking (loaded funds, match balance, spending history)
- Card-based identification (QR code, NFC, or magnetic stripe)
- Card loading workflow (admin loads funds, balance available at point of sale)

**SNAP/EBT Integration Options:**

| Option | Path | Timeline | Cost | Feasibility |
|---|---|---|---|---|
| A: Gateway Partner | Partner with Novo Dia Group or similar FNS-authorized gateway | 3-6 months | $5K-20K setup + per-txn fees | **Most realistic** |
| B: Processor SDK | Use Worldpay/FIS SDK with existing FNS authorization | 6-12 months | $10K-50K + ongoing | Moderate |
| C: Direct FNS TPP | Apply to USDA FNS as Third Party Processor | 12-24 months | $100K+ | Least realistic for small team |

**PAX Terminal Compatibility:**
- Android-based PAX devices (A920, A80) run Android — can install a custom APK
- PAX BroadPOS SDK for payment processing on-device
- Device enrollment and management (MDM or manual provisioning)
- Offline transaction queuing on terminal with batch upload
- Receipt printing via PAX built-in printer

**Advanced Compliance:**
- PCI DSS compliance for any card data handling
- FNS regulations for SNAP benefit redemption
- State-level program rules (vary by state)
- SOC 2 Type II for cloud platform (if handling payment data)

**Key Dependencies:**
- FNS authorization or partnership with FNS-authorized entity (critical path)
- PCI DSS assessment and compliance certification
- PAX developer program membership + test hardware
- Payment processor contract and merchant account
- Legal review for financial compliance

**Key Risks:**
- FNS certification is a 12-24 month process with significant regulatory requirements
- PCI compliance is expensive and ongoing ($20K-100K+ annually for assessments)
- PAX SDK is proprietary — requires NDA and developer agreement
- Financial liability for payment processing errors
- State-by-state regulatory variation adds complexity
- This phase cannot be done by code alone — requires legal, compliance, and business partnerships

**Definition of Done:**
- Customer can present card/QR at market, balance deducted automatically
- SNAP benefits can be redeemed at market via authorized gateway
- PAX terminal processes transactions and syncs to server
- PCI DSS compliance verified by QSA (Qualified Security Assessor)
- FNS authorization obtained (directly or via partner)

**Product Direction Change:** FAM Manager becomes a regulated financial platform. Requires compliance team, legal counsel, and payment processor partnerships. This is no longer a software-only endeavor.

---

### Phase 5: Scale, Analytics, and Ecosystem

**Objective:** Production-grade reliability, advanced analytics, and ecosystem integrations for large-scale multi-state operation.

**Features:**
- High-availability server deployment (load balancer, database replication, auto-scaling)
- Real-time dashboards with drill-down analytics (market performance, vendor trends, customer demographics)
- Automated reimbursement workflows (generate payment files, integrate with accounting systems)
- Scheduled report generation and email delivery
- Integration with external systems: WIC MIS, state SNAP databases, grant reporting tools
- Public API for third-party integrations
- White-label capability (rebrand per organization)
- Accessibility compliance (WCAG 2.1 AA for web, mobile accessibility)
- Internationalization (Spanish language support at minimum)

**Key Dependencies:**
- Phases 0-4 complete
- DevOps team or managed infrastructure
- Analytics tooling (Metabase, Looker, or custom)
- State/federal data sharing agreements

**Key Risks:**
- Operational complexity at scale
- Data privacy regulations (state-level consumer protection laws)
- Support burden across multiple organizations
- Competitive landscape (Healthy Ways, DFBP already operational)

**Definition of Done:**
- Platform supports 50+ markets across 5+ organizations with <1s response times
- Automated reimbursement reduces admin time by 80%
- 99.9% uptime SLA achieved over 3-month period
- At least one state-level program integration operational

---

## C) Feature Scoring Table

| Feature | Phase | User Value | Difficulty (1-5) | Risk | Ext. Deps | Feasibility (0-100) | Claude Code Only? | Notes |
|---|---|---|---|---|---|---|---|---|
| Local user accounts + PIN auth | 0 | High | 2 | Low | None | 95 | Yes | Hash PINs with bcrypt, gate UI behind roles |
| Role-based access (Admin/Manager/Volunteer) | 0 | High | 2 | Low | None | 90 | Yes | Permission flags per role, UI element hiding |
| Persistent customer database | 0 | High | 3 | Low | None | 85 | Yes | New customers table, cross-day lookup, merge UI |
| Database encryption at rest | 0 | Med | 3 | Med | None | 75 | Yes | SQLCipher or app-level AES; complicates PyInstaller |
| Automated local backup rotation | 0 | Med | 1 | Low | None | 98 | Yes | Scheduled DB file copy, retain N backups |
| Cross-platform single-instance | 0 | Low | 2 | Low | None | 90 | Yes | Replace Windows mutex with file lock |
| REST API server (FastAPI) | 1 | High | 4 | Med | Some | 70 | Partially | Can build API; needs hosting, domain, TLS |
| PostgreSQL migration | 1 | High | 3 | Med | Some | 75 | Partially | Query rewrite needed; hosting required |
| Offline-first sync with conflict resolution | 1 | High | 5 | High | None | 50 | Yes | Architecturally hard; needs extensive testing |
| Source device identifiers | 1 | Med | 1 | Low | None | 98 | Yes | UUID column on transactions, trivial |
| JWT authentication | 1 | High | 3 | Med | None | 80 | Yes | Standard library implementation |
| WebSocket real-time updates | 1 | Med | 3 | Med | None | 75 | Yes | FastAPI WebSocket support built-in |
| Multi-tenant organization model | 2 | High | 4 | High | None | 60 | Yes | Schema redesign, isolation testing critical |
| Organization admin web dashboard | 2 | High | 4 | Med | Some | 55 | Partially | New React/Vue frontend; needs hosting |
| Cross-market aggregated reporting | 2 | High | 3 | Low | None | 80 | Yes | SQL aggregation across markets |
| Hierarchical roles (Org/Market/Volunteer) | 2 | Med | 3 | Med | None | 70 | Yes | Extends Phase 0 role system |
| Vendor self-registration portal | 3 | Med | 3 | Low | Some | 65 | Partially | Web form + approval workflow; needs hosting |
| Mobile app (React Native/Flutter) | 3 | High | 5 | High | Some | 40 | Partially | Can build app; needs devices, store accounts |
| Receipt photo capture | 3 | Med | 2 | Low | None | 80 | Yes | Camera API + S3 upload |
| Push notifications | 3 | Low | 3 | Med | Some | 60 | Partially | Requires FCM/APNs setup |
| Customer accounts and wallet/balance | 4 | High | 4 | High | Some | 45 | Partially | Can build ledger; financial compliance needed |
| Card loading workflows | 4 | High | 4 | High | Heavy | 35 | Partially | Software buildable; card printing/NFC hardware |
| SNAP/EBT integration (gateway partner) | 4 | High | 5 | High | Heavy | 20 | No | Requires FNS-authorized partner, legal agreements |
| SNAP/EBT integration (direct FNS) | 4 | High | 5 | High | Heavy | 5 | No | 12-24 month FNS certification process |
| PAX terminal app (Android APK) | 4 | High | 5 | High | Heavy | 25 | No | PAX SDK proprietary, NDA, hardware testing |
| PAX device enrollment and MDM | 4 | Med | 4 | High | Heavy | 20 | No | Proprietary tooling, fleet management |
| PCI DSS compliance | 4 | High | 5 | High | Heavy | 15 | No | Requires QSA assessment, organizational controls |
| High-availability deployment | 5 | Med | 4 | Med | Some | 50 | Partially | Can architect; needs cloud infra budget |
| Real-time analytics dashboards | 5 | Med | 3 | Low | None | 70 | Yes | Metabase or custom; data already structured |
| Automated reimbursement workflows | 5 | High | 3 | Med | Some | 60 | Partially | Payment file generation; bank integration |
| State/federal system integration | 5 | Med | 5 | High | Heavy | 15 | No | Data sharing agreements, proprietary APIs |
| White-label / rebrand capability | 5 | Low | 2 | Low | None | 85 | Yes | Theme system, configurable branding |
| Spanish language support (i18n) | 5 | Med | 3 | Low | None | 80 | Yes | Qt i18n system, translation files |
| Offline fallback in online platform | 1-5 | High | 5 | High | None | 45 | Yes | Must be designed from Phase 1 onward |

---

## D) Specific Area Evaluations

### Online Mode with Cloud Database + Multi-Workstation Sync

**Current state:** SQLite file-based, single machine. No network code.

**Recommended approach:** Introduce a FastAPI REST server backed by PostgreSQL. The desktop app becomes an API client. For offline fallback, maintain a local SQLite cache that queues writes and syncs when online.

**Architecture:**

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│Desktop #1│────▸│          │     │          │
│(SQLite   │     │ FastAPI  │────▸│PostgreSQL│
│ cache)   │◂────│ Server   │◂────│  (cloud) │
└──────────┘     │          │     │          │
┌──────────┐     │          │     └──────────┘
│Desktop #2│────▸│          │
│(SQLite   │◂────│          │
│ cache)   │     └──────────┘
└──────────┘
```

**Effort estimate:** 3-4 months for a single developer with Claude Code assistance. The sync layer is the hardest part.

---

### Conflict Resolution and Source Device Identifiers

**Current state:** No device identifiers. No conflict scenarios (single machine).

**Recommended approach:**
- Stamp every record with `source_device_uuid` (generated on first launch, stored in `app_settings`)
- Use **server-authoritative timestamps** — server assigns `synced_at` on receipt
- **Conflict policy:** Last-write-wins for most fields. For financial records (transactions, payment lines), conflicts are flagged for manual review rather than auto-resolved
- **FAM Transaction ID collisions:** If two offline devices generate the same sequence number, server detects and reassigns

**Feasibility:** Source device IDs are trivial (Phase 1, day 1). Conflict resolution design is the hardest architectural decision in the entire roadmap.

---

### Multi-Tenant (Multiple Organizations) and Data Separation

**Current state:** Single implicit organization. Markets, vendors, payment methods are global.

**Recommended approach:** Add `organization_id` FK to markets, vendors, payment_methods, users. All queries filter by org. Option for schema-per-tenant (separate PostgreSQL schemas) for stronger isolation — adds operational complexity but guarantees no data leakage.

**Risk:** Row-level tenancy is simpler to build but a single missing WHERE clause leaks data. Schema-per-tenant is safer but harder to manage at scale. Recommend starting with row-level + aggressive testing, moving to schema-per-tenant if the platform exceeds 10 organizations.

---

### Authentication, Roles, Permissions, Audit Logs

**Current state:** No auth. Audit log exists but uses free-text volunteer names.

**Phase 0 approach (local):** Users table with hashed PIN, role enum (Admin/Manager/Volunteer). UI elements hidden per role. Audit log references user_id instead of free text.

**Phase 1+ approach (server):** JWT tokens, OAuth2 flows, session management. Hierarchical roles (Org Admin, Market Manager, Volunteer). Fine-grained permissions (can_void, can_adjust, can_export, can_manage_settings).

**Current audit log is a strong foundation** — the schema already captures table, record_id, action, old/new values, changed_by, and timestamp. Adding user_id FK and making it server-authoritative makes it production-grade.

---

### Vendor Self-Registration

**Current state:** Vendors created manually in Settings by whoever has the app open.

**Approach:** Web form (Phase 3) where vendors submit name, contact info, markets they attend. Submissions go into a pending queue. Market Manager or Org Admin reviews and approves. On approval, vendor is created and assigned to requested markets.

**Feasibility:** Straightforward web CRUD. The approval workflow is the only non-trivial piece. Estimated 2-3 weeks with Claude Code.

---

### User Accounts and Customer Wallet Concepts

**Current state:** Customers are day-scoped labels (C-001). No persistent identity. No balance tracking.

**Phased approach:**
1. **Phase 0:** Persistent customer table (name, phone/email, unique ID). Cross-day lookup. Prior match history visible.
2. **Phase 4:** Wallet/balance model — customer has a balance loaded by admin. Transactions deduct from balance. Match is calculated and added to balance or applied at point of sale.

**Key design decision:** Is the wallet a prepaid debit model (customer loads cash, gets match credit) or a post-pay reimbursement model (customer pays full price, gets match refunded)? Current FAM model is neither — it's a real-time split at the register. The wallet model would be a significant product change.

---

### Card Loading Workflows and Balance Tracking

**Current state:** Nothing exists.

**Approach:** New `customer_wallets` table (customer_id, balance, last_loaded_at). New `wallet_transactions` table (load, debit, match_credit, adjustment). Admin UI for loading funds. Transaction flow checks wallet balance before confirming. Card/QR maps to customer_id.

**External dependencies:** Physical cards (print QR codes, or NFC cards + reader hardware). Card printing is ~$0.50-2/card for QR, $3-5/card for NFC.

**Feasibility:** The software ledger is buildable (65/100). Physical card logistics are an operational challenge, not a code challenge.

---

### SNAP Processing Integration

**Current state:** `snap_reference_code` text field on transactions. No actual processing.

**Three options evaluated:**

| Option | Path | Timeline | Cost | Feasibility |
|---|---|---|---|---|
| A: Gateway Partner | Partner with Novo Dia Group or similar FNS-authorized gateway | 3-6 months | $5K-20K setup + per-txn fees | **Most realistic** |
| B: Processor SDK | Use Worldpay/FIS SDK with existing FNS authorization | 6-12 months | $10K-50K + ongoing | Moderate |
| C: Direct FNS TPP | Apply to USDA FNS as Third Party Processor | 12-24 months | $100K+ | Least realistic for small team |

**Recommendation:** Option A. Partner with an already-authorized gateway. They handle FNS compliance; you integrate via their API. This is how most small market incentive programs operate.

---

### PAX Terminal Compatibility

**Current state:** No terminal integration.

**Approach:**
- PAX A920/A80 run Android — can sideload or deploy custom APK
- PAX BroadPOS SDK provides payment processing APIs on-device
- Custom FAM app on PAX: receipt entry, payment allocation, SNAP swipe, receipt print
- Offline queue with batch sync (same pattern as desktop offline mode)

**External dependencies:**
- PAX developer account + NDA for SDK access
- Test hardware ($300-800/device)
- Payment processor agreement for terminal merchant ID
- EMV certification for card-present transactions

**Feasibility:** 25/100. The APK is buildable, but EMV certification, PAX SDK access, and payment processor contracts are external gates that cannot be solved with code alone.

---

### Mobile Apps (Android/iOS)

**Realistic phased plan:**

| Sub-phase | Scope | Timeline | Notes |
|---|---|---|---|
| 3a: Web-responsive | Make admin dashboard tablet-friendly | 2-4 weeks | CSS-only, lowest effort |
| 3b: PWA | Progressive Web App for receipt intake on mobile browser | 1-2 months | Works on any device, no app store |
| 3c: React Native MVP | Native app for volunteer receipt intake + payment | 3-6 months | iOS + Android from single codebase |
| 3d: Full mobile | Offline sync, camera, push notifications, GPS | 6-12 months | Feature parity with desktop |

**Recommendation:** Start with 3a+3b (responsive web + PWA). This covers 80% of mobile use cases without app store complexity. Go to 3c only if native device features (NFC, Bluetooth printer) are required.

---

### Reporting at Scale

**Current state:** 6 CSV exports, 5 Matplotlib charts, local data only.

**Phase 2+ approach:**
- Server-side report generation (background jobs)
- Org-level aggregation across all markets
- Scheduled reports (daily summary, weekly reimbursement)
- PDF generation for formal reimbursement packages
- Dashboard with filters, drill-down, date ranges (Metabase or custom)

**Feasibility:** High (70-80/100). The data model already supports the queries needed. The gap is infrastructure (report scheduler, PDF renderer, email delivery).

---

### Security and Compliance

| Requirement | Phase | Effort | Notes |
|---|---|---|---|
| Hashed credentials | 0 | Low | bcrypt, standard practice |
| HTTPS / TLS | 1 | Low | Let's Encrypt, reverse proxy |
| JWT token management | 1 | Medium | Token rotation, refresh tokens |
| Database encryption | 0-1 | Medium | SQLCipher locally, PostgreSQL TDE on server |
| Audit log integrity | 1 | Medium | Hash chain or append-only with checksums |
| PCI DSS | 4 | Very High | Only if handling card data directly |
| SOC 2 Type II | 4-5 | Very High | Organizational, not just technical |
| HIPAA | N/A | N/A | Not handling health data |
| FNS compliance | 4 | Very High | SNAP program rules, annual recertification |

---

### Offline-First Fallback Strategy

**Design principle:** Every client (desktop, mobile, terminal) must function fully without network. Network is an enhancement, not a requirement.

**Pattern:**
1. Local SQLite database on every client device
2. All writes go to local DB first (instant response)
3. Background sync service pushes changes to server when online
4. Server resolves conflicts and pushes authoritative state back
5. Sync status indicator in UI (green = synced, yellow = pending, red = offline)
6. Manual "force sync" button for end-of-day reconciliation

**This must be designed in Phase 1 and maintained through all subsequent phases.** Retrofitting offline capability into an online-only system is much harder than building offline-first from the start.

---

## E) Realism Check

### What One Developer + Claude Code Can Realistically Achieve

| Phase | Solo Dev + Claude Code | Timeline | Confidence |
|---|---|---|---|
| Phase 0 | Fully achievable | 4-8 weeks | High |
| Phase 1 (API + sync) | Achievable with effort | 3-6 months | Medium |
| Phase 1 (offline sync) | Achievable but risky | +2-3 months | Medium-Low |
| Phase 2 (multi-tenant) | Achievable for core | 3-4 months | Medium |
| Phase 2 (web dashboard) | Achievable (basic) | +2-3 months | Medium |
| Phase 3 (vendor portal) | Achievable | 1-2 months | Medium |
| Phase 3 (mobile app) | Achievable (PWA) | 1-2 months | Medium |
| Phase 3 (native mobile) | Stretch | 4-6 months | Low |
| Phase 4 (wallet/cards) | Software portion only | 2-3 months | Medium |
| Phase 4 (SNAP/PAX) | **Cannot do alone** | N/A | N/A |
| Phase 5 (scale) | Partially | Ongoing | Low |

### What Requires External Partnerships

| Need | Partner Type | Why Code Alone Won't Work |
|---|---|---|
| SNAP/EBT processing | FNS-authorized gateway (Novo Dia, etc.) | Federal authorization required; cannot self-certify |
| PAX terminal SDK | PAX Technology | Proprietary SDK under NDA; hardware testing |
| EMV card processing | Payment processor (Worldpay, Stripe, Square) | Merchant account, processor agreement, certification |
| PCI DSS audit | QSA (Qualified Security Assessor) | Third-party assessment required by card brands |
| Physical card production | Card manufacturer | Printing, encoding, shipping logistics |
| App store presence | Apple / Google | Developer accounts, review process compliance |
| FNS compliance review | USDA FNS / legal counsel | Regulatory expertise, not engineering |
| State program rules | State agriculture departments | Vary by state, require relationship management |

### Volunteer Time Constraints

The current app is designed for market-day volunteers who may have 30 minutes of training. This must be preserved:

- **Phase 0-1:** Desktop UX stays the same. Login adds one step but simplifies volunteer names in audit.
- **Phase 2-3:** Web/mobile must be as simple or simpler than desktop. Progressive disclosure — volunteers see receipt intake only; admins see everything.
- **Phase 4:** Terminal integration actually simplifies the volunteer workflow (swipe instead of manual entry).
- **Key metric:** Time from "customer approaches table" to "transaction confirmed" must stay under 60 seconds.

---

## F) Top 10 Recommendations

Prioritized by impact and feasibility:

1. **Add local user accounts and role-based access control (Phase 0)** — Highest impact-to-effort ratio. Makes the app deployable to real nonprofits today. Fully buildable with Claude Code.

2. **Build persistent customer database with cross-day lookup (Phase 0)** — Returning customer tracking is the #1 feature gap for market operators. Extends existing customer_orders model.

3. **Introduce REST API server with FastAPI (Phase 1)** — The single most important architectural change. Unlocks every subsequent phase. Start with read-only endpoints, then add writes.

4. **Implement offline-first sync architecture from day one (Phase 1)** — Design the sync protocol before building the API. Retrofitting offline support later is 5x harder.

5. **Add source device identifiers to all records (Phase 1)** — Trivial to implement, essential for multi-workstation debugging and conflict resolution. Do this immediately.

6. **Build a Progressive Web App for mobile receipt intake (Phase 3a)** — 80% of mobile value at 20% of native app effort. Works on any device with a browser. No app store needed.

7. **Partner with an FNS-authorized gateway for SNAP (Phase 4)** — Do not try to get FNS authorization directly. Partner with Novo Dia Group or equivalent. This is a business decision, not a technical one.

8. **Automate reimbursement report generation (Phase 2-3)** — The biggest admin time sink is generating vendor reimbursement packages. Automating this delivers immediate value to every org using the platform.

9. **Build the web admin dashboard as a separate frontend (Phase 2)** — Do not try to make the desktop app do web things. Build a React/Vue dashboard for org administration, keep the desktop app for market-day operations.

10. **Accept that Phase 4 (payments/terminals) requires a team and funding (Phase 4)** — This is not a solo-developer milestone. Budget $50-150K and 12-18 months for SNAP integration, terminal compatibility, and PCI compliance. Seek grant funding from USDA or foundations supporting food access programs.

---

## Appendix: Visual Phase Timeline

```
            Q1 2026    Q2 2026    Q3 2026    Q4 2026    2027       2028+
            ─────────  ─────────  ─────────  ─────────  ─────────  ─────────
Phase 0     ████████░
Phase 1                ██████████████████░░
Phase 2                           ░░░░░████████████████
Phase 3                                      ░░░░░░████████████░░
Phase 4                                                 ░░░░░░░████████████
Phase 5                                                            ░░░░████

████ = Active development
░░░░ = Planning / dependencies
```

> **Note:** Timelines assume one developer with Claude Code assistance. A 2-3 person team could compress Phases 0-2 into 6-9 months. Phase 4 timelines are driven by external partnerships, not development speed.

---

*This document was generated from a comprehensive analysis of the FAM Market Manager v1.5.1 codebase (13 database tables, 7 UI screens, 229 passing tests, ~300K lines of source code). It reflects the current architecture as of March 2026.*
