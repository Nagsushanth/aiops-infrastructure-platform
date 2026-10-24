# AIOps Platform for Infrastructure Support — Progress Tracker

**Project start:** October 22, 2026  
**Target completion:** November 6, 2026  
**Last updated:** October 25, 2026  
**Cloud provider:** AWS  
**GenAI provider:** OpenAI GPT  
**Status:** Core foundation implemented; integration and governance work remains.

---

## 1. Project Objective

Build an AIOps platform that allows users to request infrastructure changes using natural-language conversations. The system will collect and validate requirements, generate Terraform configuration, present infrastructure changes for review and approval, estimate costs, and manage infrastructure lifecycle operations with security and audit controls.

## 2. Technology Stack — Required

- **Frontend:** React.js, TypeScript, Material UI
- **Backend:** FastAPI with WebSocket support
- **GenAI:** OpenAI GPT
- **Infrastructure as Code:** Terraform
- **Database:** PostgreSQL
- **Cache and message broker:** Redis
- **Background processing:** Celery
- **Local services:** Docker Compose
- **Authentication:** JWT and role-based access control
- **Cloud:** AWS
- **Testing:** Pytest and frontend build verification

The prescribed technology stack must not be replaced.

---

## 3. Phase 1 — Setup and Architecture

**Planned dates:** October 22–23, 2026  
**Status:** Partially complete

### Completed

- [x] Installed and verified Python, Node.js, npm, Docker, Docker Compose, Terraform and Git.
- [x] Created the project directory and frontend/backend skeletons.
- [x] Set up the React, TypeScript and Material UI frontend.
- [x] Set up the FastAPI backend.
- [x] Configured PostgreSQL and Redis in Docker Compose.
- [x] Created SQLAlchemy models for conversations and messages.
- [x] Configured Alembic database migrations.
- [x] Implemented conversation and message persistence.
- [x] Configured Celery with Redis and created a health task.

### Pending

- [ ] Verify all services start cleanly together from the documented commands.
- [ ] Configure company-approved AWS access and least-privilege IAM.
- [ ] Configure AWS budget safeguards and the dedicated test environment.
- [ ] Configure the company-provided OpenAI API key.
- [ ] Verify FastAPI can reach PostgreSQL, Redis and OpenAI.

**Exit criterion:** The local application services communicate successfully, and external access requirements are verified.

---

## 4. Phase 2 — Conversational AI

**Planned dates:** October 24–26, 2026  
**Status:** Partially complete

### Completed

- [x] Built the chat interface and conversation history.
- [x] Persisted chat messages in PostgreSQL.
- [x] Implemented WebSocket communication.
- [x] Added JWT registration, login and current-user functionality.
- [x] Added authenticated WebSocket connections.
- [x] Added conversation ownership checks and user-specific conversation isolation.
- [x] Implemented deterministic requirement extraction and clarifying questions.
- [x] Added infrastructure requirement validation with Pydantic.
- [x] Implemented multi-turn requirement collection.
- [x] Added a test for collecting requirements across multiple WebSocket messages.
- [x] User reported that the full backend test suite passed: 40 tests.

### Pending

- [ ] Integrate OpenAI GPT for requirement analysis and clarifying questions.
- [ ] Configure structured GPT responses and validate them against Pydantic schemas.
- [ ] Handle GPT API errors, timeouts and invalid responses.
- [ ] Verify typing indicators, connection errors and reconnect behaviour.
- [ ] Complete role-based access control verification.

**Important:** Requirement gathering currently uses deterministic logic. Real GPT-powered requirement extraction is not yet confirmed as implemented.

**Exit criterion:** A user can submit an incomplete request, answer follow-up questions and receive a validated infrastructure specification.

---

## 5. Phase 3 — Terraform Generation and Plan Preview

**Planned dates:** October 27–28, 2026  
**Status:** Partially complete

### Completed

- [x] Implemented an initial Terraform generator for the supported EC2 resource configuration.
- [x] Restricted generation to the supported resource catalog.
- [x] Ran Terraform formatting, initialization and validation checks successfully for the previously tested generator.
- [x] Implemented a local Terraform preview API.
- [x] Created the React Terraform preview component.
- [x] Added proposed resource and configuration details to the preview response.
- [x] Kept real deployment disabled.

### Pending

- [ ] Connect validated requirements to the full Terraform generation workflow.
- [ ] Generate and display a real Terraform plan.
- [ ] Display resource changes, plan errors and execution details.
- [ ] Verify Terraform modules and templates against the required project scope.
- [ ] Test invalid specifications and unsupported resources.

**Important:** The existing preview is a local proposed-resource preview, not a real Terraform plan. No AWS infrastructure has been confirmed as provisioned.

**Exit criterion:** Validated requirements produce Terraform configuration and a reviewable plan.

---

## 6. Phase 4 — Approval Workflow and Cost Estimation

**Planned dates:** October 29–30, 2026  
**Status:** Pending implementation

### Existing foundation

- [x] Terraform preview response includes preliminary approval status fields.
- [x] Authentication foundation is available for protecting approval endpoints.

### Pending

- [ ] Design and implement persistent approval records.
- [ ] Implement approve and reject API endpoints.
- [ ] Add approval and rejection actions to the frontend.
- [ ] Enforce approval permissions using role-based access control.
- [ ] Store reviewer identity, decision, timestamp and comments.
- [ ] Prevent execution when mandatory approvals are missing.
- [ ] Implement infrastructure cost estimation.
- [ ] Display cost assumptions, estimated monthly costs and warnings.
- [ ] Add budget-related governance checks.

**Exit criterion:** Approval decisions are persisted and enforced, and reviewers can evaluate estimated costs before execution.

---

## 7. Phase 5 — Secure Deployment and Lifecycle Management

**Planned dates:** October 31–November 2, 2026  
**Status:** Pending implementation

### Pending

- [ ] Obtain and verify company-approved AWS access.
- [ ] Configure least-privilege IAM permissions.
- [ ] Verify the dedicated AWS test environment and budget controls.
- [ ] Implement controlled Terraform plan and apply execution.
- [ ] Require the necessary approvals before applying infrastructure changes.
- [ ] Capture execution output, errors and resource identifiers.
- [ ] Implement provisioning status and failure handling.
- [ ] Implement controlled infrastructure deprovisioning.
- [ ] Verify cleanup of test resources to avoid unnecessary charges.

**Safety condition:** Do not enable real deployment until authentication, permissions, plan review and required approvals have been verified.

**Exit criterion:** Approved infrastructure changes can be provisioned and deprovisioned in the authorized test environment.

---

## 8. Phase 6 — Audit Trail, Status Tracking and Integration

**Planned dates:** November 3–4, 2026  
**Status:** Pending implementation

### Pending

- [ ] Persist audit records for requests, reviews, approvals and execution.
- [ ] Record the actor, action, timestamp and outcome for each auditable event.
- [ ] Display provisioning status and execution failures in the UI.
- [ ] Enforce appropriate access restrictions for audit records.
- [ ] Integrate the complete request-to-provisioning workflow.
- [ ] Test authentication and authorization across all relevant endpoints.
- [ ] Verify the database migrations and Docker Compose services work together.

**Exit criterion:** The main workflow is integrated, and important decisions and execution outcomes are traceable.

---

## 9. Phase 7 — Testing and Final Demonstration

**Planned dates:** November 5–6, 2026  
**Status:** Pending

### Pending

- [ ] Run the full backend test suite.
- [ ] Verify frontend production build.
- [ ] Test chat, requirement validation and GPT integration.
- [ ] Test Terraform generation and real plan review.
- [ ] Test approval enforcement and audit record persistence.
- [ ] Test secure AWS provisioning and deprovisioning if access is available.
- [ ] Fix critical issues and document known limitations.
- [ ] Prepare architecture, workflow and cost-estimation explanations.
- [ ] Prepare the final demonstration and handover documentation.

**Exit criterion:** The implemented workflow is tested, documented and ready for demonstration, with any external-access limitations clearly disclosed.

---

## 10. Current Technical State

| Component | Current state |
|---|---|
| React frontend | Implemented; chat and preview components exist |
| FastAPI backend | Implemented foundation |
| PostgreSQL | Conversation, message and related application models implemented |
| Redis | Configured for local services and Celery |
| Celery | Initial task and health task implemented |
| JWT authentication | Registration, login and current-user functionality implemented |
| Conversation ownership | Implemented and tested |
| WebSocket | Authenticated communication and multi-turn requirement collection implemented |
| Requirement validation | Deterministic extraction and Pydantic validation implemented |
| OpenAI GPT | Integration pending |
| Terraform generator | Initial EC2-focused generator implemented |
| Terraform preview | Local proposed-resource preview implemented |
| Real Terraform plan | Pending |
| Cost estimation | Pending |
| Persistent approval workflow | Pending |
| Audit trail | Pending |
| AWS provisioning | Pending; no real deployment confirmed |
| Lifecycle management | Pending |
| Final end-to-end demo | Pending |

---

## 11. Immediate Next Steps

1. Verify Alembic configuration and migration history.
2. Review the existing Terraform preview component and FastAPI application routes.
3. Design the database model for approval records and audit events.
4. Add Alembic migration(s) for the new records.
5. Implement authenticated, authorized approval and rejection endpoints.
6. Add the approval UI and persist decisions.
7. Add tests for permissions, approval state transitions and audit persistence.
8. Update this progress tracker after the implementation and tests are verified.

---

## 12. Project Constraints and Notes

- Follow the exact technology stack specified in the project requirements.
- Use AWS and OpenAI GPT as selected.
- Prefer eligible free-tier resources where appropriate, but verify current AWS pricing and eligibility before provisioning.
- Do not expose API keys, passwords, JWT secrets or AWS credentials in source code or logs.
- Do not treat a local Terraform preview as a real plan.
- Do not report approval, audit, cost estimation or provisioning functionality as complete until implemented and tested.
- Git setup and Git operations are intentionally deferred.
- Preserve the November 6, 2026 project deadline and prioritize a safe, demonstrable end-to-end workflow.
