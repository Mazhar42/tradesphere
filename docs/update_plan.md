I recommend using a **two-agent, architecture-first development process**:

- **Claude Opus 5.5:** Acts as principal solution architect, audits the repository, challenges existing decisions, researches deployment options, and produces a comprehensive implementation blueprint. It must not start implementing features.
- **Claude Sonnet 5.5:** Acts as senior implementation engineer, follows the approved blueprint phase by phase, writes code, migrations, tests, and documentation, and reports evidence of completion.

There is one important adjustment I want to make to your target.

Don't instruct Opus to simply make every score 10/10. That encourages unnecessary complexity and inflated assessments. Instead, instruct it to establish measurable production acceptance criteria for every category and achieve the highest defensible engineering standard.

For example, a modular monolith can be a 10/10 architectural choice for a dealership SaaS. Microservices are not automatically better.

## **What the plan must accomplish**

I would expand your scope beyond the previous audit in five important areas:

1. **True multi-tenant SaaS:** Multiple independent dealerships, strict isolation, subscriptions, billing, entitlements and platform administration.
2. **Existing dealership onboarding:** Import data from Excel, CSV, other software and APIs without forcing dealerships to abandon their existing processes.
3. **Product and information discovery:** Search existing catalogs, supplier information, products, barcodes and other records when information is missing.
4. **Two deployment profiles:** Render free-tier demonstration environment and an actual production VPS deployment.
5. **Operational excellence:** Performance, financial integrity, security, disaster recovery, automated testing, monitoring and maintainability.

The import and coexistence requirements are particularly important. You should be able to onboard a dealership without asking it to stop its existing manual registers, Excel sheets, accounting software or ERP.

# **Master planning and implementation instructions**

The following is designed to be pasted into Claude Opus 5.5 with the repository attached or opened in its working environment.

# TradeSphere — SaaS Production Readiness & Architecture Transformation

## Role

Act as a Principal Solution Architect, SaaS Platform Architect, Database Architect, Security Architect, Performance Engineer, DevOps Architect, and Senior Enterprise Software Engineering Reviewer.

You are responsible for transforming the existing TradeSphere dealership management application into a commercially viable, secure, scalable, maintainable, multi-tenant SaaS platform.

Your objective is to establish an evidence-based architecture and implementation roadmap that can be executed by Claude Sonnet 5.5 in small, controlled, verifiable development phases.

This is an architecture and planning assignment, NOT an implementation assignment.

Do not start modifying application code. First understand the existing application, inspect its implementation, challenge its design, identify gaps, and produce an actionable transformation plan.

---

# 1. Product vision

TradeSphere is a dealership management SaaS platform initially targeting FMCG distributors and dealership businesses in Bangladesh, with the possibility of expanding into other markets.

The platform currently supports substantial dealership operations, including:

- Product and catalog management.
- Suppliers and customers.
- Inventory and stock management.
- Batch and expiry tracking.
- Purchasing and purchase orders.
- Goods receipts.
- Sales and sales returns.
- Customer receipts.
- Supplier payments.
- Inventory adjustments and transfers.
- Company claims and settlements.
- Commission management.
- Expenses and fund transfers.
- Financial reports.
- Accounting and journal entries.
- User roles and permissions.
- Audit logging.

The current technology stack includes:

Backend:

- Python
- FastAPI
- SQLAlchemy 2
- PostgreSQL 17
- Alembic
- Pydantic

Frontend:

- React 19
- TypeScript
- Vite
- Tailwind CSS
- TanStack Router
- TanStack Query
- TanStack Table
- React Hook Form
- Zod
- i18next with English and Bangla support

Infrastructure:

- Docker
- Docker Compose
- Render deployment configuration
- Intended Ubuntu VPS production deployment

Do not assume these technologies must be replaced. Retain good existing architectural decisions unless there is measurable evidence that a change is necessary.

The preferred architectural direction is a modular monolith unless the repository audit identifies a compelling reason otherwise.

Do not introduce microservices, Kubernetes, Kafka, or distributed infrastructure simply for architectural fashion.

---

# 2. First assignment: Complete repository audit

Before designing anything, inspect the entire existing repository.

Inspect:

- Project structure.
- Backend modules.
- Frontend features.
- Database models.
- Database constraints.
- Alembic migrations.
- API endpoints.
- Business services.
- Authentication and authorization.
- Financial calculations.
- Inventory transactions.
- Reporting queries.
- Frontend routing and state management.
- API integration.
- Existing tests.
- Dockerfiles.
- Docker Compose.
- Render configuration.
- VPS deployment configuration.
- Environment configuration.
- Documentation.
- CI/CD configuration.
- Security and observability implementation.

Do not rely solely on README files or architectural documentation.

Verify every important architectural claim against actual implementation.

In particular, identify discrepancies between documentation and the repository.

For every significant finding, provide:

1. Severity: Critical, High, Medium, Low.
2. Affected files and modules.
3. Current implementation.
4. Architectural or business problem.
5. Potential consequences.
6. Recommended solution.
7. Dependencies.
8. Testing requirements.
9. Acceptance criteria.

Distinguish clearly between:

- Implemented and verified.
- Implemented but insufficiently tested.
- Partially implemented.
- Documented but not implemented.
- Completely missing.

Do not assume a feature works merely because its endpoint or database model exists.

Run available checks where possible. Do not claim that tests, benchmarks, or security checks passed unless you actually executed them.

---

# 3. Target architecture: Multi-tenant SaaS

This is the highest-priority architectural transformation.

TradeSphere must support independent dealership businesses subscribing to the same SaaS platform.

Example:

- ABC Motors
- XYZ Distributors
- Rahman Trading
- Future dealerships

Every dealership must have completely isolated business data.

Evaluate and recommend the appropriate multi-tenancy strategy.

My initial preference is:

Shared application + shared PostgreSQL database + shared schema + tenant_id isolation.

However, evaluate the trade-offs against schema-per-tenant and database-per-tenant approaches.

The proposed architecture must address:

## 3.1 Tenant management

Design:

- Tenants.
- Tenant registration.
- Tenant provisioning.
- Tenant status.
- Tenant settings.
- Tenant memberships.
- Tenant-specific roles.
- Tenant-specific permissions.
- Tenant-specific business profiles.
- Tenant-specific fiscal years.
- Tenant-specific document sequences.
- Tenant-specific accounting configuration.
- Tenant-specific subscription information.

A user may potentially belong to multiple dealerships.

A dealership owner must be able to invite employees and assign roles.

Platform administrators and dealership administrators must have separate authority.

## 3.2 Tenant isolation

Design defense-in-depth isolation.

Consider:

- Tenant context resolution.
- Authenticated tenant membership.
- Tenant-scoped repository/query patterns.
- Tenant-aware foreign keys.
- Tenant-aware unique constraints.
- Tenant-aware document numbering.
- PostgreSQL Row-Level Security.
- Tenant-scoped background jobs.
- Tenant-aware audit logs.
- Tenant-specific file and export access.
- Tenant isolation in reporting.
- Tenant isolation in caches.
- Tenant isolation in search.
- Tenant isolation in import processes.

Investigate every existing table and determine whether it is:

1. Platform-owned.
2. Tenant-owned.
3. Globally shared reference data.

Create a complete table ownership matrix.

Do not merely add tenant_id to some tables and declare multi-tenancy complete.

A transaction belonging to one dealership must never reference another dealership's product, supplier, customer, account, or inventory record.

Define how PostgreSQL connection pooling and transaction-scoped RLS context will work safely with multiple application workers.

Include negative security tests proving that cross-tenant access is impossible through APIs, IDs, reports, exports, and background tasks.

## 3.3 Tenant onboarding

Design the complete journey:

1. Visitor registers.
2. Creates dealership profile.
3. Selects subscription or starts trial.
4. Becomes dealership owner.
5. Configures business settings.
6. Imports initial data.
7. Invites employees.
8. Starts using individual business modules.

Consider whether registration and subscription activation should be separate operations.

Define account verification, owner recovery, tenant suspension, tenant deletion, and reactivation.

---

# 4. Subscription, billing and SaaS administration

Design a complete subscription management system.

Required capabilities:

- Subscription plans.
- Monthly and yearly billing.
- Free trials.
- Subscription upgrades.
- Downgrades.
- Cancellation.
- Renewal.
- Failed payment handling.
- Grace periods.
- Suspension.
- Reactivation.
- Billing history.
- Invoices and receipts.
- Payment records.
- Payment webhook processing.
- Idempotent payment events.
- Subscription audit history.

Design a feature entitlement engine.

Examples of configurable entitlements:

- Maximum users.
- Maximum branches or warehouses.
- Maximum products.
- Maximum transactions.
- Reporting capabilities.
- Import limits.
- Export capabilities.
- API access.
- Advanced inventory features.

Do not hardcode plan-specific conditions throughout business services.

Recommend a centralized entitlement architecture.

Evaluate payment gateway integration appropriate for Bangladesh and possible international expansion.

Consider local payment providers and international payment providers separately.

Do not assume provider availability, pricing, or API capabilities. Verify current documentation when producing the final plan.

Separate payment provider integration from subscription domain logic.

Also design a platform administration interface where TradeSphere operators can:

- View dealerships.
- Create or deactivate tenants.
- View subscriptions.
- Manage plans.
- Inspect billing events.
- Monitor system health.
- Review support requests.
- Manage platform-level configuration.
- Access tenant information only through explicitly authorized, audited support procedures.

---

# 5. Critical new requirement: Existing dealership data import and migration

This is a major product requirement.

Many dealerships already maintain business information through:

- Excel workbooks.
- CSV files.
- Manual registers.
- Paper invoices.
- Accounting software.
- Existing dealership management software.
- ERP systems.
- Supplier-provided product lists.
- Existing inventory systems.

TradeSphere must make onboarding possible without forcing a dealership to abandon its existing manual or digital processes immediately.

Design a complete data import and coexistence architecture.

## 5.1 Supported import sources

Evaluate support for:

1. CSV.
2. Excel XLSX.
3. Standardized import templates.
4. Existing TradeSphere exports.
5. External software exports.
6. REST API integrations.
7. Scheduled file imports.
8. Supplier-provided catalogs.
9. Future ERP integrations.

Do not assume all external systems provide APIs.

CSV and Excel import must be first-class capabilities.

## 5.2 Importable entities

Design imports for:

- Products.
- Categories.
- Brands.
- Product barcodes.
- Units of measurement.
- Product unit conversions.
- Suppliers.
- Customers.
- Warehouses and stock locations.
- Opening stock.
- Stock batches.
- Expiry dates.
- Purchase orders.
- Outstanding purchase orders.
- Goods receipts, where historical detail is available.
- Customer outstanding balances.
- Supplier outstanding balances.
- Payment accounts.
- Chart of accounts.
- Opening account balances.
- Historical sales and purchases where appropriate.

Identify dependencies and import order.

For example:

Products must exist before stock opening balances can be imported.

Customers and suppliers must exist before their corresponding transactions can be imported.

## 5.3 Import workflow

Design a guided import experience:

1. Select entity type.
2. Upload file or connect source.
3. Detect file structure.
4. Preview sample records.
5. Map source columns to TradeSphere fields.
6. Validate required fields.
7. Identify duplicates.
8. Identify missing references.
9. Resolve data conflicts.
10. Preview the final import.
11. Execute import.
12. Generate an import report.
13. Allow authorized corrections and controlled retry.

Support field mapping that can be saved as a reusable template for future imports.

## 5.4 Data validation

Handle:

- Missing values.
- Invalid numeric values.
- Invalid dates.
- Duplicate SKUs.
- Duplicate barcodes.
- Duplicate supplier codes.
- Invalid unit conversions.
- Missing product references.
- Invalid opening quantities.
- Inconsistent financial balances.
- Unknown currencies.
- Invalid transaction statuses.

Provide row-level validation results.

The user must be able to download an error report and correct their original spreadsheet.

## 5.5 Safe import and rollback

This is mandatory.

Never directly insert uploaded records into production tables without validation.

Design:

- Staging tables.
- Import job records.
- Import batches.
- Row-level results.
- Idempotency.
- Transactional commit.
- Duplicate detection.
- Safe retry.
- Partial failure handling.
- Import audit history.
- Rollback or compensating correction strategy.

Do not promise rollback of already-posted financial events unless the accounting model supports a safe reversal.

An import must never silently overwrite existing operational data.

Use explicit conflict policies:

- Skip existing record.
- Update permitted fields.
- Create a new record.
- Merge after user confirmation.
- Reject the conflicting row.

Require a dry-run preview before committing significant imports.

## 5.6 Opening balances and migration cutover

Design a safe transition strategy for existing dealerships.

A dealership may already have:

- Existing customer dues.
- Existing supplier dues.
- Existing stock.
- Outstanding purchase orders.
- Existing bank balances.
- Cash balances.
- Historical transactions.

Provide a way to establish a verified opening position.

Define how to prevent double-counting between imported opening balances and subsequently imported historical transactions.

Consider a cutover date and an opening-balance reconciliation report.

Historical data should be imported only to the level of detail that can be validated.

The system must not invent missing historical inventory movements or accounting entries.

## 5.7 Coexistence with existing processes

This is a product-level requirement, not merely an import feature.

A dealership should be able to use TradeSphere gradually.

Examples:

- Initially import its product catalog only.
- Continue recording purchases in its existing Excel system.
- Use TradeSphere for inventory visibility.
- Later start recording purchases in TradeSphere.
- Continue using existing accounting software while testing TradeSphere accounting.
- Eventually transition selected workflows after reconciliation.

Design a controlled coexistence approach.

Consider:

- Manual entry.
- CSV exports and imports.
- API-based integrations.
- Scheduled synchronization.
- External reference IDs.
- Source-system identifiers.
- Last synchronization timestamps.
- Conflict detection.
- Synchronization audit logs.
- Reconciliation reports.
- Integration health monitoring.

Clearly distinguish read-only synchronization from bidirectional synchronization.

Do not introduce automatic bidirectional synchronization without defining conflict ownership and resolution.

Never allow an integration to post financial transactions or change stock without explicit authorization and idempotency protection.

The system must not force the dealership to change its existing process on day one.

---

# 6. Product, supplier and business information discovery

A major usability requirement is that users should not always need to create everything manually.

Design an extensible search and information-discovery system.

## 6.1 Internal search

Users should be able to search existing records across:

- Products.
- SKUs.
- Barcodes.
- Categories.
- Brands.
- Suppliers.
- Customers.
- Stock.
- Batches.
- Purchase orders.
- Goods receipts.
- Sales invoices.
- Returns.
- Payments.
- Accounts.

Evaluate a unified search experience and dedicated entity-specific search.

Support:

- Exact matching.
- Partial matching.
- Barcode matching.
- SKU matching.
- Fuzzy matching where appropriate.
- Bangla and English search.
- Search filters.
- Sorting.
- Pagination.
- Search relevance.
- Permission-aware results.
- Tenant-aware results.

Do not create an expensive global search engine before PostgreSQL capabilities have been evaluated.

Investigate PostgreSQL full-text search and trigram indexes first.

## 6.2 Missing product discovery

Consider this scenario:

A dealership receives a product from a supplier that is not yet present in its catalog.

The user enters a product name, SKU, barcode, or manufacturer code.

TradeSphere should be able to:

1. Search the tenant's existing product catalog.
2. Search previously imported product records.
3. Search permitted supplier catalogs.
4. Search approved external product information providers where available.
5. Present possible matches.
6. Show matching attributes and source information.
7. Allow the user to select an existing product.
8. Allow authorized creation of a new tenant-owned product.

Never silently create products based on an uncertain external match.

## 6.3 Product information enrichment

Investigate possible support for:

- Barcode lookup.
- GTIN/EAN/UPC.
- Manufacturer product codes.
- Brand information.
- Product descriptions.
- Product images.
- Units and packaging.
- Supplier-specific SKU mapping.

Evaluate data licensing, source reliability, privacy, and API costs.

External catalog information must not override dealership-specific:

- Dealer price.
- Trade price.
- MRP.
- Purchase cost.
- Supplier relationship.
- Stock quantities.

## 6.4 Supplier and procurement discovery

Consider supplier catalog imports and supplier-specific product identifiers.

A product can have different supplier codes and prices.

Design a mapping between:

- TradeSphere product.
- Supplier.
- Supplier SKU.
- Supplier description.
- Supplier purchase price.
- Minimum order quantity.
- Packaging.
- Historical price.

Investigate supplier search and matching when a new supplier is encountered.

Do not assume a supplier is legitimate simply because its information appears in an external source.

## 6.5 Stock and order search

Provide fast access to:

- Current available stock.
- Stock by location.
- Stock by batch.
- Expiring stock.
- Low stock.
- Stock movement history.
- Outstanding purchase orders.
- Partially received orders.
- Supplier delivery history.
- Customer order history.

Define the underlying search indexes and reporting query strategies.

---

# 7. Business logic and financial integrity

Review the entire financial and inventory domain.

Preserve the existing design principle:

- Business events are independent documents.
- Posted documents are immutable.
- Corrections use reversals.
- Inventory movements are authoritative.
- Journal entries are authoritative.
- Projections are rebuildable.
- Posting is atomic.

Audit:

- Purchase order posting.
- Goods receipt posting.
- Supplier advances.
- Supplier payment allocation.
- Sales invoice posting.
- Customer receipts.
- Sales returns.
- Purchase returns.
- Claims.
- Claim settlements.
- Expenses.
- Fund transfers.
- Opening balances.
- Inventory costing.
- Moving weighted average cost.
- FEFO allocation.
- Stock transfers.
- Inventory adjustments.
- Financial reports.

Establish explicit accounting invariants.

Examples:

- Total debit equals total credit.
- Posted inventory movements reconcile with stock balances.
- Posted sales reconcile with receivables and revenue.
- COGS reconciles with inventory valuation.
- Customer balances reconcile with receivable ledgers.
- Supplier balances reconcile with payable ledgers.
- Reversals correctly restore the original economic effect.
- Duplicate requests cannot produce duplicate financial effects.

Review rounding, decimal precision, date handling, fiscal year boundaries, and timezone handling.

Introduce reconciliation commands and automated tests.

Do not change existing financial semantics without documenting the migration and business impact.

---

# 8. Authentication, authorization and security

Design a production-grade security architecture.

Review:

- Password hashing.
- Access token lifecycle.
- Refresh token rotation.
- Token revocation.
- Session management.
- Role-based access control.
- Tenant memberships.
- Platform administration.
- Permission checks.
- Login rate limiting.
- Account recovery.
- Email or phone verification.
- MFA.
- CSRF protection.
- CORS.
- Trusted proxy configuration.
- Security headers.
- Input validation.
- SQL injection protection.
- File upload security.
- API abuse protection.
- Secret management.
- Audit logging.

Evaluate whether RBAC alone is sufficient or whether selected operations require attribute-based authorization.

Design MFA for dealership owners and platform administrators.

Ensure support personnel cannot casually access tenant financial information.

All exceptional support access must be explicitly authorized and audited.

Include security testing against OWASP ASVS and relevant OWASP API Security Top 10 risks.

Do not claim compliance certification without an actual audit.

---

# 9. Performance and scalability

Establish a measurable performance strategy.

Do not make unsupported scalability claims.

Review:

- SQL query efficiency.
- Index design.
- N+1 queries.
- Connection pooling.
- Database transactions.
- Row locks.
- Pagination.
- Search.
- Reporting.
- Large imports.
- Exports.
- Background jobs.
- Caching.
- API response sizes.
- Frontend bundle size.
- Frontend rendering.
- Memory usage.
- Database growth.

Evaluate PostgreSQL query plans for important workflows.

Consider keyset pagination for large transaction tables.

Consider Redis only when actual caching, rate limiting, or queue requirements justify it.

Design background job execution for:

- Large imports.
- Large exports.
- Scheduled synchronization.
- Report generation.
- Reconciliation.
- Subscription events.
- Notifications.

Evaluate an outbox pattern for reliable event publication.

Define performance targets for normal API requests, financial posting, imports, exports, and reporting.

Propose realistic load scenarios:

Small dealership:

- 5 concurrent users.
- 5,000 products.
- 20,000 invoices.

Medium dealership:

- 20 concurrent users.
- 25,000 products.
- 200,000 invoices.

SaaS workload:

- 50 dealerships.
- 5 concurrent users per dealership.
- 250 concurrent users overall.

These are proposed benchmark scenarios, not assumed system capabilities.

Determine the infrastructure needed to test them.

---

# 10. Frontend architecture and user experience

Retain the current frontend stack unless there is a compelling reason to change it.

Review:

- Feature boundaries.
- Reusable components.
- API client.
- Authentication state.
- Tenant context.
- Error handling.
- Loading states.
- Form validation.
- Table performance.
- Accessibility.
- Internationalization.
- Responsive behavior.
- State synchronization.
- Route protection.

Design the missing SaaS experiences:

- Public landing page.
- Registration.
- Dealership onboarding.
- Subscription selection.
- Tenant switcher.
- User invitations.
- Billing dashboard.
- Import wizard.
- Integration management.
- Import history.
- Data reconciliation.
- Global search.
- Product discovery.
- Platform administration.

Maintain the existing English and Bangla capabilities.

Do not introduce unnecessary frontend complexity.

---

# 11. Automated testing and quality assurance

Establish a complete testing strategy.

Backend:

- Unit tests.
- Integration tests.
- Database tests.
- Financial invariant tests.
- Concurrency tests.
- Idempotency tests.
- Tenant isolation tests.
- Subscription lifecycle tests.
- Import validation tests.
- API security tests.

Frontend:

- Component tests.
- Form tests.
- State management tests.
- API integration tests.
- Accessibility tests.

End-to-end:

- Playwright.
- Dealership registration.
- Owner onboarding.
- Employee invitation.
- Product import.
- Supplier import.
- Opening stock.
- Purchase order.
- Goods receipt.
- Sales invoice.
- Customer receipt.
- Sales return.
- Financial reconciliation.
- Subscription expiry.
- Tenant isolation.

Infrastructure:

- Docker build tests.
- Migration tests.
- Backup verification.
- Restore tests.
- Deployment smoke tests.
- Rollback tests.

Define required checks for every pull request.

Use actual coverage and test evidence. Do not use coverage percentage as a substitute for meaningful assertions.

---

# 12. Observability and production operations

Design:

- Structured JSON logs.
- Request IDs.
- Tenant-aware log context without exposing sensitive data.
- Error tracking.
- Metrics.
- Health checks.
- Readiness checks.
- Database monitoring.
- Slow query monitoring.
- Alerting.
- Audit log retention.
- Operational dashboards.

Define backup and disaster recovery requirements.

Include:

- Automated PostgreSQL backups.
- Point-in-time recovery where appropriate.
- Off-site storage.
- Encryption.
- Retention policy.
- Backup integrity verification.
- Restoration drills.
- Recovery Point Objective.
- Recovery Time Objective.

Provide documented deployment, rollback, restore, and incident response procedures.

---

# 13. Two separate deployment options

The architecture plan MUST provide two independent deployment profiles.

They are not interchangeable.

## Option A: Render free-tier demonstration deployment

Purpose:

- Development demonstrations.
- Stakeholder reviews.
- UI validation.
- Feature demonstrations.
- Disposable test data.
- Early product feedback.

Use:

- Render static site for React.
- Render web service for FastAPI.
- Render PostgreSQL where currently available under the selected free offering.
- Render Blueprint configuration.
- Environment-based secrets.
- Automated build and deployment from Git.

Review the existing render.yaml and deployment documentation.

Identify every limitation of the current free-tier offering.

In the supplied repository, the documentation describes a free database with a 30-day expiry and no backups, sleeping web services, and non-persistent free service disks. Verify current provider documentation before finalizing the plan because pricing and resource limits can change.

The Render free profile must:

- Be clearly marked NON-PRODUCTION.
- Use test data only.
- Never be represented as suitable for real financial or dealership data.
- Never be the only copy of business data.
- Avoid relying on persistent local files.
- Have separate test credentials and secrets.
- Provide a clean deployment and reset process.

Include:

- Required environment variables.
- Blueprint configuration.
- Build commands.
- Migration process.
- Owner bootstrap.
- Health checks.
- Troubleshooting.
- Cost and resource limitations.
- Teardown procedure.

Do not weaken production security simply to make the free deployment work.

## Option B: Actual production VPS deployment

Purpose:

A real commercial SaaS environment holding actual dealership data.

Design an Ubuntu LTS VPS deployment using Docker Compose.

The initial architecture should include:

- Cloudflare DNS and appropriate protection.
- Caddy reverse proxy.
- React frontend.
- FastAPI API.
- PostgreSQL 17 or a verified supported version.
- Dedicated migration job.
- Background worker where required.
- Backup service.
- Off-site backup storage.
- Monitoring.
- Centralized logs.
- Secret management.

Evaluate whether a single VPS is appropriate initially and when a separate database server or managed database becomes necessary.

Provide recommended infrastructure tiers:

1. Pilot.
2. Initial commercial SaaS.
3. Growing SaaS.

For each tier, estimate resource requirements and explain assumptions.

Include:

- Dockerfiles.
- Docker Compose production configuration.
- Network isolation.
- Least-privilege database roles.
- Firewall.
- TLS.
- Environment files.
- Deployment automation.
- Database migration strategy.
- Backup automation.
- Restore procedures.
- Monitoring.
- Log rotation.
- Upgrade strategy.
- Rollback.
- Disaster recovery.

Do not expose PostgreSQL or internal application ports publicly.

Do not run migrations independently from every API worker.

Define how database schema migrations are applied exactly once before application rollout.

Explain the implications of running multiple API instances.

Provide a practical deployment runbook with executable commands.

The production deployment must be independent of the Render free deployment.

---

# 14. Data migration and existing customer onboarding strategy

Create a separate migration architecture document.

This should explain how an existing dealership moves from its old system to TradeSphere without disrupting daily operations.

Define:

- Data discovery.
- Source-system inventory.
- Data ownership.
- Field mapping.
- Data cleansing.
- Import templates.
- Staging.
- Validation.
- Dry runs.
- Cutover dates.
- Opening balances.
- Reconciliation.
- Parallel operation.
- Incremental synchronization.
- Final cutover.
- Rollback or recovery.

Include at least three realistic onboarding scenarios:

Scenario A:\
A dealership uses Excel for products, customers, suppliers and stock.

Scenario B:\
A dealership uses an existing ERP or dealership management system and can export CSV/XLSX.

Scenario C:\
A dealership uses an existing system with API access and wants gradual integration.

Explain what TradeSphere can import automatically, what requires user confirmation, and what requires manual accounting validation.

The dealership must be able to continue its existing processes while gradually adopting TradeSphere.

---

# 15. Database and architecture migration strategy

Multi-tenancy and subscription architecture will affect existing data models.

Create a safe migration strategy for the existing single-dealership installation.

Include:

- Tenant creation.
- Initial tenant assignment.
- Existing owner migration.
- Existing user migration.
- Tenant-aware constraints.
- Tenant-aware foreign keys.
- Existing data backfill.
- Document sequence migration.
- Audit log migration.
- Index creation.
- RLS introduction.
- Verification.
- Rollback.

Do not assume that adding a non-null tenant_id to existing tables can be performed safely in one migration.

Consider staged expand-and-contract migrations.

Do not destroy existing data or rewrite historical financial records.

Provide explicit migration verification queries and data integrity checks.

---

# 16. Target engineering standards and assessment

Reassess these categories:

1. Backend architecture.
2. Database architecture.
3. Business logic and financial integrity.
4. Authentication and authorization.
5. Frontend architecture.
6. Performance engineering.
7. Automated testing.
8. Multi-tenancy.
9. Subscription and billing.
10. Production operations.
11. Data migration and integration.
12. Search and discovery.
13. Security.
14. Maintainability.
15. Observability and disaster recovery.

My objective is a defensible 10/10 engineering standard for each category.

Do not award 10/10 merely because a feature exists.

For each category define:

- Current score.
- Target score.
- Remaining gap.
- Measurable acceptance criteria.
- Evidence required.
- Blocking issues.

A 10/10 target means the architecture is appropriate for its business scale, secure, tested, observable, documented, and operationally proven.

It does not mean unlimited scalability or zero risk.

If a category cannot reasonably receive 10/10, explain why and define the remaining risk.

---

# 17. Required planning deliverables

Create the following documents in a dedicated planning directory, preferably docs/architecture/saas-readiness/.

## 1. executive-assessment.md

Executive summary, current architecture assessment, critical risks, recommendations, and launch decision.

## 2. current-state-audit.md

File-by-file audit of the existing implementation.

Include verified implementation status and severity-ranked gaps.

## 3. target-architecture.md

Complete target architecture, component boundaries, responsibilities, and architectural decisions.

Include Mermaid diagrams for:

- SaaS platform architecture.
- Tenant isolation.
- Authentication and authorization.
- Subscription lifecycle.
- Import pipeline.
- External integration.
- Production infrastructure.

## 4. database-transformation.md

Complete tenant-aware data model.

Include:

- New entities.
- Existing tables requiring tenant ownership.
- Foreign key changes.
- Unique constraint changes.
- Indexes.
- RLS strategy.
- Migration order.
- Backfill strategy.

## 5. data-import-and-coexistence.md

Complete import architecture, templates, validation, staging, opening balances, reconciliation, and parallel-operation strategy.

## 6. search-and-discovery.md

Internal search, product discovery, supplier catalogs, barcode lookup, external data providers, and search performance.

## 7. subscription-and-billing.md

Subscription lifecycle, plans, entitlements, payment integration, webhooks, billing records, and platform administration.

## 8. security-and-compliance.md

Threat model, tenant isolation, authentication, authorization, OWASP risks, data protection, and security testing.

## 9. performance-and-scalability.md

Performance budgets, query optimization, indexing, load testing, connection pooling, background jobs, and capacity planning.

## 10. deployment-render-free.md

Complete demonstration deployment guide.

## 11. deployment-vps-production.md

Complete production deployment and operations guide.

## 12. testing-and-acceptance.md

Test strategy, quality gates, acceptance criteria, and release requirements.

## 13. implementation-roadmap.md

The authoritative, ordered implementation roadmap for Claude Sonnet 5.5.

## 14. architecture-decision-records.md

Document significant decisions, alternatives, rationale, and consequences.

---

# 18. Implementation roadmap requirements

The roadmap must divide the work into independent, verifiable phases.

At minimum consider:

Phase 0: Repository audit and baseline stabilization.

Phase 1: Architecture foundations and migration preparation.

Phase 2: Tenant model and tenant isolation.

Phase 3: Tenant-aware authentication, membership, RBAC and platform administration.

Phase 4: Subscription plans, entitlements and billing.

Phase 5: Data import framework and dealership onboarding.

Phase 6: Product, supplier and information discovery.

Phase 7: Existing-system integration and coexistence.

Phase 8: Financial and inventory integrity hardening.

Phase 9: Performance optimization.

Phase 10: Frontend SaaS experiences.

Phase 11: Security hardening.

Phase 12: Automated testing and load testing.

Phase 13: Render demonstration deployment.

Phase 14: Production VPS infrastructure and operations.

Phase 15: Pilot dealership validation and commercial launch approval.

You may reorganize these phases when dependencies justify it.

For every phase provide:

- Objective.
- Prerequisites.
- Affected files.
- New files.
- Database migrations.
- Backend tasks.
- Frontend tasks.
- Security considerations.
- Testing requirements.
- Acceptance criteria.
- Deployment considerations.
- Rollback strategy.
- Dependencies.
- Definition of Done.

Break implementation into small, reviewable tasks suitable for Sonnet 5.5.

Avoid massive phases that require rewriting many unrelated modules simultaneously.

Preserve working features throughout the transformation.

---

# 19. Strict rules for your architecture recommendations

1. Do not implement code during this assignment.
2. Do not rewrite the application without justification.
3. Do not assume existing documentation is accurate.
4. Do not invent existing functionality.
5. Do not silently change financial semantics.
6. Do not sacrifice tenant isolation for convenience.
7. Do not recommend infrastructure without cost and operational justification.
8. Do not introduce unnecessary distributed architecture.
9. Do not claim production readiness without evidence.
10. Do not use generic recommendations without identifying affected repository components.
11. Do not design data import as unrestricted bulk database insertion.
12. Do not force dealerships to abandon existing systems during onboarding.
13. Do not silently overwrite existing customer, supplier, product, inventory or accounting information.
14. Do not use external product information without identifying its source and confidence.
15. Do not assume external APIs are available or free.
16. Do not make Render free-tier hosting a production recommendation.
17. Do not create an unrealistic implementation plan that attempts to accomplish everything in one commit.
18. Preserve existing data and maintain backward compatibility where feasible.
19. Clearly distinguish mandatory work from optional future enhancements.
20. Use evidence-based acceptance criteria.

When external provider information, payment gateways, Render limitations, or VPS infrastructure requirements are relevant, verify current official documentation and include source references.

---

# 20. Final assignment

Your final objective is to produce an architecture and implementation blueprint that Claude Sonnet 5.5 can execute without repeatedly making fundamental architectural decisions.

The documents must be sufficiently detailed that another senior engineer could understand the intended architecture, implementation order, data migration requirements, and acceptance criteria.

Before finalizing:

1. Recheck the repository against your proposed target architecture.
2. Identify unresolved architectural decisions.
3. Identify dependencies that could invalidate the roadmap.
4. Check that migration order is safe.
5. Check that tenant isolation is enforceable.
6. Check that import and coexistence requirements are fully covered.
7. Check that both deployment options are independently documented.
8. Check that financial and inventory integrity are preserved.
9. Check that each implementation phase has testable completion criteria.

Produce a concise executive recommendation at the beginning and a detailed implementation roadmap at the end.

The output must be an engineering blueprint, not a generic SaaS checklist.

The objective is to transform TradeSphere into a trustworthy commercial SaaS product that independent dealerships can subscribe to, migrate into gradually, and use for real business operations with confidence.

# **Second prompt: Sonnet implementation instructions**

Once Opus has completed the blueprint, do not give Sonnet the instruction to implement the entire plan in one go.

Give it the following prompt, along with the approved architecture documents.

# TradeSphere — Controlled SaaS Implementation

## Role

Act as a Senior Software Engineer and Implementation Architect working under the approved TradeSphere SaaS architecture.

Claude Opus 5.5 has produced the architecture and implementation blueprint.

Your responsibility is to implement that blueprint faithfully, one phase and one verifiable milestone at a time.

You are not authorized to independently redesign major architectural decisions.

If you identify a flaw, contradiction, missing dependency, or unsafe migration in the approved plan, stop and report it before implementing the affected work.

## Mandatory preparation

Before writing code:

1. Read all approved architecture documents.
2. Read the current repository documentation.
3. Inspect the affected source files.
4. Understand existing business logic.
5. Identify dependencies.
6. Review the previous phase's completion report.
7. Check Git status and existing uncommitted changes.
8. Confirm the database migration state.
9. Identify regression risks.

Do not overwrite unrelated user work.

## Implementation rules

1. Implement only the assigned phase or milestone.
2. Break large tasks into small logical changes.
3. Follow existing project conventions unless the approved plan requires a change.
4. Maintain the modular monolith.
5. Use explicit SQLAlchemy models and typed Pydantic schemas.
6. Use Alembic migrations for database changes.
7. Keep financial calculations server-authoritative.
8. Preserve Decimal/NUMERIC handling.
9. Maintain transactional business operations.
10. Preserve document immutability and reversal semantics.
11. Maintain idempotency.
12. Apply tenant isolation consistently.
13. Never trust tenant IDs supplied directly by clients.
14. Maintain API and frontend type consistency.
15. Update generated API types when required.
16. Write tests alongside implementation.
17. Do not remove existing tests merely to make a build pass.
18. Do not weaken security or validation to simplify implementation.
19. Do not introduce dependencies without justification.
20. Do not claim work is complete without execution evidence.

## Database migration rules

Database migrations must be safe and reversible where possible.

For tenant migration:

- Use staged migrations.
- Backfill existing records.
- Validate relationships.
- Introduce constraints only after data is valid.
- Preserve historical financial records.
- Validate tenant ownership.
- Test migration from a copy of the pre-migration database.
- Provide rollback or recovery instructions.

Do not execute destructive production migrations without explicit authorization.

## Import implementation rules

For all data imports:

- Use staging.
- Validate before committing.
- Provide dry-run.
- Detect duplicates.
- Record source-system identifiers.
- Make imports idempotent.
- Provide row-level errors.
- Prevent unauthorized overwrites.
- Preserve audit history.
- Reconcile opening balances.
- Do not silently post historical financial events.

## Testing rules

After each meaningful change:

Run relevant:

- Backend tests.
- Frontend tests.
- Type checking.
- Linting.
- Migration tests.
- Integration tests.
- Tenant isolation tests.
- Financial invariant tests.

Run full regression suites at appropriate phase boundaries.

If a test fails, investigate the underlying cause rather than bypassing it.

## Performance and security rules

Avoid N+1 queries.

Use appropriate indexes.

Avoid loading unbounded datasets into memory.

Use background jobs for long-running operations.

Protect every tenant-owned query.

Protect file imports and exports.

Never expose secrets or sensitive financial information in logs.

## Deployment rules

Maintain two independent deployment profiles:

1. Render free-tier demonstration deployment.
2. Production VPS deployment.

Never treat the free-tier deployment as a substitute for production infrastructure.

Keep production secrets out of source control.

Migrations must run through a controlled deployment step.

## Required completion report

At the end of every milestone, provide:

### 1. Completed work

What was implemented.

### 2. Files changed

List every important modified or added file.

### 3. Database changes

List migrations and their effects.

### 4. Tests executed

List commands, results, and failures.

### 5. Security review

Explain how the change affects authorization and tenant isolation.

### 6. Performance impact

Identify potential regressions and optimization considerations.

### 7. Known limitations

Identify incomplete work and unresolved risks.

### 8. Acceptance criteria

Mark each criterion as passed, failed, or not verified.

### 9. Git checkpoint

Recommend a commit boundary and descriptive commit message.

### 10. Next milestone

Identify the next task and its dependencies.

## Critical instruction

Do not attempt to implement multiple major phases simply because you have sufficient context or token budget.

Work incrementally.

Preserve application stability.

If a task is too large, divide it into smaller milestones and obtain approval before crossing architectural boundaries.

The objective is not to produce the largest amount of code.

The objective is to produce correct, secure, maintainable, tested, production-quality software.

# **3. How I recommend you execute this**

I would use the following workflow.

## **Opus → Architecture approval → Sonnet**

1. **Prepare repository**

   Create a separate Git branch and ensure the current application builds and tests pass. Preserve a database backup.
2. **Opus architecture review**

   Provide the ZIP or repository access, master prompt, and existing documentation. Ask it to create the 14 planning documents.
3. **Challenge the architecture**

   Ask Opus to perform a second review specifically looking for missing tenant isolation, data migration risks, accounting inconsistencies, and overengineering.
4. **Approve the blueprint**

   Freeze the architecture decisions and establish the implementation roadmap. Do not let implementation drift from it silently.
5. **Sonnet implementation**

   Assign one milestone at a time. Review its changes, execute tests, and commit before proceeding.
6. **Independent validation**

   Have Opus review completed milestones against the architecture and acceptance criteria. Sonnet fixes identified issues.
7. **Pilot deployment**

   Validate using Render with disposable data, then deploy to a production VPS and onboard a controlled pilot dealership.

## **4. My suggested Git strategy**

Keep your existing application stable.

```
main
  |
  └── saas-transformation
        |
        ├── phase-01-foundation
        ├── phase-02-multitenancy
        ├── phase-03-subscriptions
        ├── phase-04-data-import
        ├── phase-05-discovery
        ├── phase-06-hardening
        └── phase-07-production
```

Each phase should have small feature branches and reviewed pull requests.

Never let an AI coding agent perform a large database migration directly against your production database.

# **5. Additional requirement I strongly recommend**

There is one feature I would explicitly prioritize during onboarding: **a dealership migration assistant**.

[Column Mapping & Data Cleanup Panel | Osmos](https://images.openai.com/static-rsc-4/eAYqho57sd76IQ3bTB61-bQ04LVlutY3aGBKGru1LM_rZqICbu6D4wOZKKNjbJ2coTYDqNs3sXniVZ30nok91U_XFXKdwo8GzdY5FfUCw6cMVr7wzW22ny8DvT1XKN9vJeLyPfiYYmPFRoh69I7v4XOPFL0SymvElWHh7nhJtGc?purpose=inline)

[nuvo Software Reviews, Demo & Pricing - 2026](https://images.openai.com/static-rsc-4/eG1bwZ6rLA3ruuiBDx9SDoA0cbNmQvaju0NJDF1ee0liTBxikADM56ehtwz53uMOhC103h-fEssMyM5X7NtUvfTJQ-WKhU65TBJFldncfrNE6DdDRfDciEVg_KGGsCn88Chbydmj91qMjBkpDMOAHpKCuiYma3g8nrgo5vmF_W4?purpose=inline)

[Barcode Scanning  – Service Fusion](https://images.openai.com/static-rsc-4/YrdPk8yUAiXkAWr-ZDk2i5Dv7syGkmlsgq4G6ezhIrUCbvRKuaaYWg-oUOfPdv-4t9l5fm1eYO5eivjGDR08cQJA4b5p32_RSB7YMr79HRrq0jumnkqUrshFH-CWAQsyr7rBW3j9XT_f1aTDmL0ehtWbIlG51ifnXMTEq2x4sSY?purpose=inline)

14

Imagine a dealership owner registering for TradeSphere.

Instead of seeing an empty application, the owner sees:

- Import your existing products.
- Import your suppliers.
- Import your customers.
- Import your stock.
- Import outstanding purchase orders.
- Import opening balances.

They upload their Excel files, map columns, preview errors and approve the import.

TradeSphere then creates a migration report showing what was imported, what needs correction, and what remains outstanding.

I would also provide an option to continue using the old system while gradually enabling TradeSphere workflows.

This is more than a technical convenience. It can become a genuine competitive advantage when acquiring dealership customers.

## **6. The acceptance standard I would insist on**

Before calling TradeSphere commercially ready, I would require these conditions:

| Assessment category   | Launch requirement                                  |
| --------------------- | --------------------------------------------------- |
| Backend architecture  | Modular boundaries and reliable business operations |
| Database design       | Tenant-aware integrity and safe migrations          |
| Business logic        | Verified financial and inventory reconciliation     |
| Authentication        | Secure identity, sessions and permissions           |
| Frontend              | Complete, tested SaaS user journeys                 |
| Performance           | Demonstrated load-test results                      |
| Testing               | Automated critical workflow and isolation coverage  |
| Multi-tenancy         | No cross-tenant data access                         |
| Subscription          | Complete lifecycle and entitlement enforcement      |
| Production operations | Verified backup, restore and rollback               |
| Data migration        | Repeatable, auditable and reconciled imports        |
| Search and discovery  | Fast, tenant-aware and permission-aware             |
| Security              | Threat-model review and security testing            |
| Maintainability       | Documented architecture and controlled changes      |
| Observability         | Actionable monitoring and operational alerts        |

**One final piece of advice:** Don't onboard paying dealerships simply because every feature has been implemented. Onboard them after the system has passed its migration, reconciliation, security, backup-restoration and operational acceptance tests.

The ultimate goal is not a 10/10-looking architecture document. It is a dealership owner trusting TradeSphere with their actual inventory, money, customers and daily business operations.