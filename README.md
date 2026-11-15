**# AIOps Platform for Infrastructure Support**



An AI-assisted infrastructure management platform that turns

natural-language infrastructure requests into validated requirements,

Terraform plans, approval workflows, and governed infrastructure

lifecycle operations.



The project is designed to reduce manual infrastructure provisioning

effort while keeping infrastructure changes reviewable, traceable, and

protected by explicit approval and execution safeguards.



\> **\*\*Lifecycle safety note:\*\*** Terraform plan generation and review are

\> implemented. The provision and deprovision workers include execution

\> paths guarded by feature flags. Keep \`AIOPS_TERRAFORM_APPLY_ENABLED\`

\> and \`AIOPS_TERRAFORM_DESTROY_ENABLED\` disabled unless the deployment

\> environment, AWS permissions, remote state, approvals, and operational

\> safeguards have been reviewed. A successful worker preflight is not

\> the same as a successful live AWS apply or destroy. Live AWS execution

\> should only be described as verified after it has actually been run

\> and its resulting state has been checked.



**------------------------------------------------------------------------**



**## Table of contents**



\-   [Project overview]\(#project-overview)

\-   [Objectives]\(#objectives)

\-   [Core capabilities]\(#core-capabilities)

-   [GenAI orchestration]\(#genai-orchestration)

\-   [Architecture]\(#architecture)

\-   [Technology stack]\(#technology-stack)

\-   [Repository structure]\(#repository-structure)

\-   [How the workflow operates]\(#how-the-workflow-operates)

\-   [Terraform lifecycle and safety

&#x20;   controls]\(#terraform-lifecycle-and-safety-controls)

\-   [Prerequisites]\(#prerequisites)

\-   [Local development setup]\(#local-development-setup)

\-   [Environment configuration]\(#environment-configuration)

\-   [Running the application]\(#running-the-application)

\-   [API documentation and health

&#x20;   checks]\(#api-documentation-and-health-checks)

\-   [Testing]\(#testing)

\-   [AWS and Terraform configuration]\(#aws-and-terraform-configuration)

\-   [Security considerations]\(#security-considerations)

\-   [Troubleshooting]\(#troubleshooting)

\-   [Current implementation status]\(#current-implementation-status)

\-   [Roadmap]\(#roadmap)



**------------------------------------------------------------------------**



**## Project overview**



Infrastructure teams commonly receive requests such as:



\-   "Create a development EC2 instance with 2 vCPUs and 2 GB RAM."

\-   "Prepare a secure AWS development environment."

\-   "Create a Linux virtual machine for testing."



Turning these requests into infrastructure usually requires several

manual steps: clarifying requirements, selecting resource sizes, writing

Terraform, reviewing the plan, obtaining approvals, executing changes,

and tracking the resulting resources.



This platform brings those activities into one application. A user

interacts with an infrastructure assistant, the system gathers and

validates the required specifications, and the Terraform workflow

prepares a plan for review. Approval and lifecycle-operation records

provide governance and an audit trail around infrastructure changes.



The project follows the goals and deliverables in the supplied \*\*AIOps

Platform for Infrastructure Support\*\* project specification.



**## Objectives**



1\.  **\*\*Natural-language infrastructure requests\*\*** --- provide a

&#x20;   conversational interface for infrastructure needs.

2\.  **\*\*Intelligent requirement gathering\*\*** --- ask clarifying questions

&#x20;   and validate required infrastructure attributes.

3\.  **\*\*Infrastructure-as-Code generation\*\*** --- translate validated

&#x20;   requirements into Terraform configuration.

4\.  **\*\*Plan visibility\*\*** --- show resource-level Terraform changes and

&#x20;   estimated cost information before deployment.

5\.  **\*\*Governance\*\*** --- use authenticated actions, staged approvals, and

&#x20;   audit records.

6\.  **\*\*Lifecycle management\*\*** --- model provision and deprovision

&#x20;   operations with confirmation and state-safety checks.

7\.  **\*\*Operational visibility\*\*** --- expose operation status, progress

&#x20;   events, health endpoints, and infrastructure inventory information.



**## Core capabilities**



**### 1. Conversational infrastructure assistant**



\-   React-based chat interface.

\-   WebSocket communication with the FastAPI backend.

\-   Conversation and message persistence.

\-   Session continuity for an active conversation.

\-   Multi-turn clarification of infrastructure requirements.

\-   Rich message rendering for formatted text, code, tables, and

&#x20;   diagrams.



**### 2. Requirement validation**



The request flow collects a structured infrastructure specification. The

current frontend/backend contract includes fields such as:



\-   Cloud provider.

\-   Resource type.

\-   Environment.

\-   Region.

\-   vCPU count.

\-   Memory.

\-   Public-access preference.

\-   Operating system.



The backend validates the structured specification before it is used by

the Terraform workflow.



**### 3. Terraform configuration and plan generation**



\-   Generates Terraform configuration from validated infrastructure

&#x20;   requirements.

\-   Uses Terraform CLI integration to initialize and create a saved

&#x20;   plan.

\-   Parses plan JSON for resource actions and summaries.

\-   Calculates an EC2 monthly cost estimate using the project's pricing

&#x20;   logic.

\-   Evaluates Terraform security policies.

\-   Persists plan metadata and integrity fingerprints.

\-   Stores the generated configuration, provider lock file, and saved

&#x20;   plan as separate artifacts.



**### 4. Terraform plan review**



The UI provides a review surface for supplied Terraform plan JSON. It is

a review/parser feature; it does not itself execute Terraform or deploy

infrastructure.



The trusted plan workflow is separate: the backend generates the plan

from the validated request and records the plan metadata and artifact

references used by the approval and lifecycle workflows.



**### 5. Approval workflow**



\-   Approval requests are associated with an infrastructure request and

&#x20;   a Terraform plan record.

\-   Approval stages support decisions and reviewer comments.

\-   Approval data is checked again by the lifecycle worker before

&#x20;   execution.

\-   Approval and operation actions are represented in persistent

&#x20;   records.



The project specification describes technical, cost, and security review

stages. The exact stage configuration and authorization policy should be

taken from the deployed database/application configuration rather than

assumed to be identical in every environment.



**### 6. Infrastructure operations and audit history**



The infrastructure operations API supports provision/deprovision

operation records, operation listing and retrieval, and operation audit

retrieval. Worker events record status transitions and progress

information.



The worker implementation includes safeguards such as atomic operation

claiming, execution tokens, expiring worker leases, artifact

verification, and state checks.



**### 7. Terraform state and inventory**



\-   Uses an S3 backend configuration for request-specific Terraform

&#x20;   state.

\-   Uses encrypted remote state configuration and Terraform's S3

&#x20;   lockfile mechanism.

\-   Stores state metadata in PostgreSQL.

\-   Reads and parses S3 state to build sanitized infrastructure

&#x20;   inventory data.

\-   Checks the active state snapshot before destructive lifecycle

&#x20;   processing.

\-   Tracks state versions and state fingerprints/checksums where

&#x20;   supported by the implementation.



**------------------------------------------------------------------------**



**## GenAI orchestration**


The GenAI layer assists the infrastructure conversation. It interprets the
user's natural-language request, helps complete missing requirement fields,
and returns a structured specification for the deterministic backend
workflow. The current implementation uses the OpenAI Python SDK.


**### 1. Natural-language requirement extraction**


\-   The user describes the infrastructure they want in ordinary language.

\-   The AI extraction flow converts the request into structured
&#x20;   infrastructure attributes, such as cloud provider, resource type,
&#x20;   region, environment, vCPU, memory, public-access preference, and
&#x20;   operating system.

\-   The result is validated against the backend's infrastructure
&#x20;   requirement model.

\-   Deterministic extraction is also used. The merge logic preserves
&#x20;   values already identified by deterministic parsing and allows AI
&#x20;   extraction to fill fields that are still missing; AI output is not
&#x20;   allowed to overwrite those already-resolved values.


**### 2. Context-aware clarification questions**


When required information is missing, the backend identifies the first
missing field and asks the user for that information. The GenAI
clarification function receives the missing field, the requirements
collected so far, and the latest user message. It is prompted to produce
one concise question about that missing field rather than restarting the
conversation or asking for information already supplied.


This allows the question wording to reflect the current request. For
example, if a user has already selected AWS and specified a region, the
assistant should ask about the next missing requirement rather than ask
for the cloud provider again.


**### 3. Conversation flow control and branching**


Python code, rather than the language model, controls the conversation
state and branch decisions:


1\.  If a required field is missing, identify that field and ask a
&#x20;   clarification question.
2\.  If the AI clarification call succeeds, use its contextual question.
3\.  If the AI call fails or returns an invalid question, use the
&#x20;   predefined question for that field as a fallback.
4\.  If no required field is missing, validate the completed
&#x20;   specification and return the completed requirements instead of
&#x20;   asking another question.


This is deterministic, requirement-driven branching. It is not an
arbitrary or self-learning decision tree. The model helps phrase
clarification questions; the backend decides which field is missing,
when to ask, and when the requirements are complete.


**### 4. Validation and safe handoff**


\-   Pydantic models validate the extracted and merged requirement data.

\-   The completed structured specification is handed to the existing
&#x20;   Terraform workflow.

\-   GenAI does not approve infrastructure changes, execute Terraform,
&#x20;   or bypass the approval and lifecycle safety controls.


**### Current GenAI scope**


The configured implementation currently uses **OpenAI**. The project
specification lists OpenAI, Google Gemini, and Azure OpenAI as possible
providers, but multi-provider integration is not documented as
implemented in this version. The current clarification context consists
of the accumulated structured requirements and the latest user message;
this should not be described as full raw-transcript reasoning.


**### Relevant backend modules**


\-   \`backend/app/services/ai/client.py\` --- OpenAI client calls,
&#x20;   requirement extraction support, and contextual clarification
&#x20;   question generation.

\-   \`backend/app/services/ai/requirements.py\` --- requirement
&#x20;   extraction orchestration, deterministic/AI merge behavior,
&#x20;   missing-field selection, fallback questions, and completion
&#x20;   validation.

\-   \`tests/backend/test_ai_requirements.py\` --- tests for requirement
&#x20;   extraction/clarification behavior, AI question use, fallback
&#x20;   behavior, and completed requirements.


**## Architecture**



\`\`\` text

┌───────────────────────────────────────────────────────────┐

│                    React + TypeScript UI                  │

│                                                           │

│  Login / Register   Infrastructure Assistant              │

│  Terraform Preview  Plan Review  Approvals  Operations    │

└────────────────────────────┬──────────────────────────────┘

&#x20;                            │ HTTP / WebSocket

&#x20;                            ▼

┌───────────────────────────────────────────────────────────┐

│                       FastAPI Backend                     │

│                                                           │

│  Authentication  Conversations  WebSocket  Requirements  │

│  Terraform Plans  Approvals  Infrastructure Operations   │

└───────────────┬─────────────────────────────┬─────────────┘

&#x20;               │                             │

&#x20;               ▼                             ▼

┌──────────────────────────┐      ┌─────────────────────────┐

│       PostgreSQL         │      │      Redis              │

│                          │      │                         │

│ Users / Conversations    │      │ Celery broker/backend   │

│ Requirements / Plans     │      │ Async task coordination │

│ Approvals / Operations   │      │                         │

│ State / Inventory / Audit│      └────────────┬────────────┘

└──────────────────────────┘                   │

&#x20;                                              ▼

&#x20;                                   ┌───────────────────────┐

&#x20;                                   │ Celery Worker         │

&#x20;                                   │                       │

&#x20;                                   │ Provision preflight   │

&#x20;                                   │ Deprovision preflight │

&#x20;                                   │ State reconciliation  │

&#x20;                                   │ Stale-operation logic │

&#x20;                                   └───────────┬───────────┘

&#x20;                                               │

&#x20;                                               ▼

&#x20;                                   ┌───────────────────────┐

&#x20;                                   │ Terraform CLI         │

&#x20;                                   │                       │

&#x20;                                   │ Init / Plan / Saved   │

&#x20;                                   │ plan validation and   │

&#x20;                                   │ gated lifecycle exec  │

&#x20;                                   └───────────┬───────────┘

&#x20;                                               │

&#x20;                                               ▼

&#x20;                                   ┌───────────────────────┐

&#x20;                                   │ AWS                   │

&#x20;                                   │                       │

&#x20;                                   │ S3 remote state       │

&#x20;                                   │ AWS resources         │

&#x20;                                   │ IAM-authorized access │

&#x20;                                   └───────────────────────┘

\`\`\`



**### Component responsibilities**



**&#x20; -----------------------------------------------------------------------**

&#x20; Component                           Responsibility

**&#x20; ----------------------------------- -----------------------------------**

&#x20; React frontend                      User interface, chat, plan review,

&#x20;                                     approval and operation views



&#x20; FastAPI                             Authentication, REST APIs,

&#x20;                                     WebSocket communication, validation

&#x20;                                     and orchestration



&#x20; PostgreSQL                          Persistent application records,

&#x20;                                     approvals, operations, state

&#x20;                                     metadata and inventory



&#x20; Redis                               Celery broker/result backend and

&#x20;                                     asynchronous coordination



&#x20; Celery                              Background infrastructure lifecycle

&#x20;                                     tasks



&#x20; Terraform CLI                       Configuration initialization,

&#x20;                                     planning and gated saved-plan

&#x20;                                     execution



&#x20; AWS S3                              Remote Terraform state storage and

&#x20;                                     state snapshot retrieval



&#x20; OpenAI API                          GenAI-assisted interpretation and

&#x20;                                     clarification in the configured AI

&#x20;                                     flow

&#x20; \-----------------------------------------------------------------------



**------------------------------------------------------------------------**



**## Technology stack**



&#x20; Layer                    Technology

**&#x20; ------------------------ -----------------------------**

&#x20; Frontend                 React, TypeScript, Vite

&#x20; UI components            Material UI (MUI)

&#x20; Backend                  Python, FastAPI

&#x20; API server               Uvicorn

&#x20; Database                 PostgreSQL

&#x20; ORM and migrations       SQLAlchemy, Alembic

&#x20; Async processing         Celery

&#x20; Broker/result backend    Redis

&#x20; AI integration           OpenAI Python SDK

&#x20; Authentication           JWT-based authentication

&#x20; Infrastructure as Code   Terraform CLI

&#x20; Cloud integration        AWS, boto3, S3 remote state

&#x20; Testing                  pytest

&#x20; Configuration            python-dotenv



The backend dependency versions are pinned in \`requirements.txt\`. The

frontend dependencies and scripts are defined in

\`frontend/package.json\`.



**------------------------------------------------------------------------**



**## Repository structure**



The following is a high-level guide to the main application areas. The

exact repository may contain additional tests, migrations, assets, and

supporting modules.



\`\`\` text

aiops-infrastructure-platform/

│

├── backend/

│   ├── app/

│   │   ├── api/

│   │   │   ├── auth.py

│   │   │   ├── conversations.py

│   │   │   ├── websocket.py

│   │   │   ├── requirements.py

│   │   │   ├── terraform_preview\.py

│   │   │   ├── terraform_plans.py

│   │   │   ├── approvals.py

│   │   │   └── infrastructure_operations.py

│   │   │

│   │   ├── core/

│   │   │   ├── auth_dependencies.py

│   │   │   ├── database.py

│   │   │   ├── dependencies.py

│   │   │   ├── security.py

│   │   │   └── terraform_pricing.py

│   │   │

│   │   ├── models/

│   │   ├── schemas/

│   │   ├── services/

│   │   │   ├── ai/

│   │   │   └── terraform/

│   │   ├── workers/

│   │   │   ├── celery_app.py

│   │   │   └── tasks.py

│   │   └── main.py

│   │

│   └── migrations/

│

├── frontend/

│   ├── public/

│   ├── src/

│   │   ├── App.tsx

│   │   ├── ChatPanel.tsx

│   │   ├── TerraformPreview\.tsx

│   │   ├── TerraformPlanReview\.tsx

│   │   ├── ApprovalPanel.tsx

│   │   ├── ApprovalReviewPanel.tsx

│   │   ├── InfrastructureOperationsPanel.tsx

│   │   ├── AuthPanel.tsx

│   │   ├── MessageRenderer.tsx

│   │   └── api.ts

│   └── package.json

│

├── tests/

│   └── backend/

│

├── .terraform-artifacts/       # Local generated Terraform artifacts; do not commit secrets

├── requirements.txt

└── README.md

\`\`\`



**------------------------------------------------------------------------**



**## How the workflow operates**



**### Step 1 --- User authentication**



A user registers or signs in through the frontend. The backend issues an

authentication token used by protected API operations.



**### Step 2 --- Start an infrastructure conversation**



The frontend creates or resumes a conversation and connects to the

backend WebSocket endpoint. Messages are persisted and the assistant

responds with clarifying questions or requirement updates.



**### Step 3 --- Gather and validate requirements**



The conversation flow extracts infrastructure attributes and validates

whether the specification is complete. If required fields are missing or

invalid, the assistant continues the clarification process.



**### Step 4 --- Generate a Terraform plan**



The backend maps the validated specification to Terraform configuration,

initializes the working directory, runs Terraform planning, parses the

plan, and calculates cost/security information.



**### Step 5 --- Review and approve**



The user reviews the generated plan and submits it into the approval

workflow. Approval decisions are stored and checked by the lifecycle

operation worker.



**### Step 6 --- Create a lifecycle operation**



A provision or deprovision request is recorded as an infrastructure

operation. The Celery task processes it asynchronously and reports

status/progress through persistent operation records and audit events.



**### Step 7 --- Execute only when all safeguards permit it**



Before any real infrastructure action, the worker validates operation

ownership, approvals, specification and plan integrity,

provider/configuration artifacts, and the relevant Terraform state

snapshot. Apply and destroy execution are controlled independently by

feature flags.



**### Step 8 --- Reconcile state and inventory**



After a successful Terraform action, the worker reads the resulting

remote state and synchronizes the state metadata and sanitized

inventory. A successful command should not be treated as fully

reconciled until this verification succeeds.



**------------------------------------------------------------------------**



**## Terraform lifecycle and safety controls**



**### Plan generation**



The trusted plan workflow creates and stores:



\-   Terraform configuration artifact.

\-   Provider lock artifact.

\-   Binary saved-plan artifact.

\-   SHA-256 checksums/fingerprints for integrity verification.

\-   Plan summary and resource action information.

\-   Specification fingerprint tying the plan to the validated

&#x20;   requirements.

\-   Remote S3 backend metadata for the infrastructure request.



The execution worker reconstructs the approved workspace from the stored

artifacts and verifies their checksums before proceeding.



**### Provision / Apply**



The provision worker is designed to:



1\.  Atomically claim a queued provision operation.

2\.  Establish an execution token and worker lease.

3\.  Revalidate the infrastructure request and approval state.

4\.  Verify the specification fingerprint.

5\.  Verify the saved plan, provider lock, and configuration artifacts.

6\.  Reconstruct the exact approved Terraform workspace.

7\.  Initialize Terraform and complete preflight checks.

8\.  Check \`AIOPS_TERRAFORM_APPLY_ENABLED\`.

9\.  Execute only the approved saved plan when the feature gate is

&#x20;   enabled.

10\. Reconcile the resulting S3 state and inventory after successful

&#x20;   execution.



If the apply feature flag is disabled, the operation is blocked after

preflight. This is intentional and prevents an ordinary request from

silently creating AWS resources.



**### Deprovision / Destroy**



The deprovision worker is designed to:



1\.  Atomically claim a queued deprovision operation.

2\.  Require explicit destroy confirmation.

3\.  Revalidate the approved destroy request and associated plan.

4\.  Verify the plan, provider lock, and configuration artifacts.

5\.  Verify the active Terraform state snapshot and inventory

&#x20;   relationship.

6\.  Reconstruct and initialize the exact approved destroy workspace.

7\.  Recheck the state snapshot immediately before execution.

8\.  Check \`AIOPS_TERRAFORM_DESTROY_ENABLED\`.

9\.  Execute only the approved saved destroy plan when the feature gate

&#x20;   is enabled.

10\. Reconcile the resulting state and inventory after successful

&#x20;   execution.



If the state has changed since the destroy plan was approved, the

operation must be blocked and a new destroy plan should be generated and

approved. If the destroy feature flag is disabled, the operation is

blocked even when confirmation and preflight checks succeed.



**### Feature flags**



The lifecycle worker reads these independent flags:



\`\`\` dotenv

AIOPS_TERRAFORM_APPLY_ENABLED=false

AIOPS_TERRAFORM_DESTROY_ENABLED=false

\`\`\`



Keep both disabled for local development, demos, and automated tests

unless a separately reviewed AWS execution environment is ready.

Enabling a flag is not a substitute for approval, IAM least privilege,

state protection, backups, or a controlled test plan.



**### Important distinction**



The codebase contains gated Terraform execution paths, but a passing

unit test or successful preflight does not prove that an AWS resource

was actually created or destroyed. Record a live lifecycle operation as

successful only after confirming:



\-   Terraform process result.

\-   Operation record and audit events.

\-   S3 state object and state version.

\-   Inventory reconciliation.

\-   Expected AWS resource state in the target account.



**------------------------------------------------------------------------**



**## Prerequisites**



Install and configure the following before running the application:



\-   Python compatible with the project's pinned backend dependencies.

\-   Node.js and npm compatible with the frontend lockfile.

\-   PostgreSQL.

\-   Redis.

\-   Terraform CLI (the generated Terraform configuration specifies

&#x20;   Terraform \`>= 1.10.0\`).

\-   AWS CLI or another supported AWS credential mechanism when using

&#x20;   AWS-backed Terraform features.

\-   An AWS account and IAM permissions appropriate to the resources and

&#x20;   S3 state backend being used.

\-   An OpenAI API key if the configured GenAI flow uses OpenAI.



Use the versions pinned in the repository wherever available.



**------------------------------------------------------------------------**



**## Local development setup**



**### 1. Clone the repository**



\`\`\` bash

git clone \<YOUR_GITHUB_REPOSITORY_URL>

cd aiops-infrastructure-platform

\`\`\`



**### 2. Create and activate a Python virtual environment**



Linux/macOS:



\`\`\` bash

python3 -m venv .venv

source .venv/bin/activate

\`\`\`



Windows PowerShell:



\`\`\` powershell

python -m venv .venv

.venv\Scripts\Activate.ps1

\`\`\`



**### 3. Install backend dependencies**



\`\`\` bash

pip install --upgrade pip

pip install -r requirements.txt

\`\`\`



**### 4. Configure PostgreSQL and Redis**



Create a PostgreSQL database for the application and ensure PostgreSQL

is reachable from the backend.



Start Redis and verify that it is reachable. The Celery configuration

shown in the project uses these defaults when corresponding environment

variables are not supplied:



\`\`\` text

Broker:        redis\://localhost:6379/1

Result backend: redis\://localhost:6379/2

\`\`\`



**### 5. Configure environment variables**



Create a root \`.env\` file. Use the exact variable names expected by the

current backend configuration and keep secrets out of Git. A sample

configuration is shown below; confirm database/authentication variable

names against \`backend/app/core/database.py\` and

\`backend/app/core/security.py\` in your checkout before starting the

backend.



\`\`\` dotenv

\# Application database

DATABASE_URL=postgresql+psycopg://\<db_user>:\<db_password>@localhost:5432/\<db_name>



\# Redis / Celery

CELERY_BROKER_URL=redis\://localhost:6379/1

CELERY_RESULT_BACKEND=redis\://localhost:6379/2



\# GenAI

OPENAI_API_KEY=\<your_openai_api_key>



\# AWS region and remote Terraform state

AWS_DEFAULT_REGION=ap-south-1

TF_STATE_BUCKET=\<your-private-terraform-state-bucket>

TF_STATE_PREFIX=training/requests



\# Terraform lifecycle safety gates

AIOPS_TERRAFORM_APPLY_ENABLED=false

AIOPS_TERRAFORM_DESTROY_ENABLED=false

\`\`\`



**\*\*Do not copy this example blindly:\*\*** the exact database URL key and JWT

secret key are determined by the current application settings. Inspect

the corresponding source modules and use the names they actually read.

Do not put real credentials in this README or commit a populated \`.env\`

file.



**### 6. Apply database migrations**



Run migrations from the repository root using the project's Alembic

configuration:



\`\`\` bash

alembic upgrade head

\`\`\`



If the project has a different Alembic configuration path in your

checkout, use the repository's configured migration command.



**### 7. Install frontend dependencies**



\`\`\` bash

npm --prefix frontend install

\`\`\`



Create \`frontend/.env.local\` if needed:



\`\`\` dotenv

VITE_API_BASE_URL=http\://localhost:8000

\`\`\`



The frontend defaults to \`http\://localhost:8000\` when

\`VITE_API_BASE_URL\` is not set.



**------------------------------------------------------------------------**



**## Running the application**



Run each process in a separate terminal from the repository root.



**### Terminal 1 --- FastAPI backend**



\`\`\` bash

uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000

\`\`\`



**### Terminal 2 --- Celery worker**



\`\`\` bash

celery -A backend.app.workers.celery_app:celery_app worker --loglevel=info

\`\`\`



**### Terminal 3 --- React frontend**



\`\`\` bash

npm --prefix frontend run dev

\`\`\`



Open the frontend at:



\`\`\` text

http\://localhost:5173

\`\`\`



The API documentation is available at:



\`\`\` text

http\://localhost:8000/docs

\`\`\`



The frontend's API documentation link is configured to open the backend

Swagger UI directly.



\> The repository's Celery configuration uses Redis for the broker and

\> result backend. Start Redis before starting the worker. A Celery Beat

\> scheduler is only required if you configure periodic tasks; do not

\> start it unless the project configuration calls for it.



**------------------------------------------------------------------------**



**## API documentation and health checks**



FastAPI exposes interactive Swagger documentation at \`/docs\`.



The application registers routers for:



\-   Authentication.

\-   Conversations.

\-   WebSocket chat.

\-   Infrastructure requirements.

\-   Terraform preview.

\-   Trusted Terraform plans.

\-   Approval workflows.

\-   Infrastructure lifecycle operations.



Useful health endpoints in the application include:



&#x20; Endpoint                 Purpose

**&#x20; ------------------------ ----------------------------------------**

&#x20; \`GET /\`                  Root service information and docs path

&#x20; \`GET /health\`            Backend health

&#x20; \`GET /health/database\`   Database connectivity health



Use \`/docs\` for the exact request schemas, response models, and endpoint

paths in the currently running version. The API surface may evolve as

the project changes.



**------------------------------------------------------------------------**



**## Testing**



Run the backend test suite from the repository root:



\`\`\` bash

pytest -q

\`\`\`



Run a focused lifecycle worker test module:



\`\`\` bash

pytest -q tests/backend/test_infrastructure_worker.py

\`\`\`



Run the frontend production build:



\`\`\` bash

npm --prefix frontend run build

\`\`\`



The frontend build runs TypeScript project checks followed by the Vite

production build.



The latest verified full backend test-suite run completed with

**444 passed and 2 deprecation warnings in 32.06 seconds**. Re-run the

commands above after subsequent changes and use the current terminal

output as the authoritative result.



**### Recommended lifecycle test coverage**



\-   Missing or invalid approval.

\-   Missing destroy confirmation.

\-   Duplicate operation submission.

\-   Expired worker lease and lost worker ownership.

\-   Missing or corrupted Terraform plan artifact.

\-   Provider lock checksum mismatch.

\-   Configuration artifact checksum mismatch.

\-   Specification fingerprint mismatch.

\-   Changed S3 state snapshot before destroy.

\-   Disabled apply/destroy feature flags.

\-   Terraform command failure.

\-   Successful execution followed by state/inventory reconciliation.

\-   Audit event creation and status transition consistency.



Tests should mock cloud-side execution unless they are explicitly

designed as controlled integration tests in a dedicated AWS test

account.



**------------------------------------------------------------------------**



**## AWS and Terraform configuration**



**### Remote state**



The Terraform backend configuration is generated per infrastructure

request. The state key follows this pattern:



\`\`\` text

\<triggered-prefix>/\<infrastructure_request_id>/terraform.tfstate

\`\`\`



The configured default prefix in the implementation is:



\`\`\` text

training/requests

\`\`\`



The S3 backend configuration enables encryption and Terraform's S3

lockfile mechanism. The bucket must already exist and must be private.

Configure bucket versioning, restrictive bucket policies, and

appropriate IAM permissions as part of the AWS environment setup.



**### IAM guidance**



Use a dedicated IAM role or user for the application/worker with only

the permissions required by the selected Terraform resources and state

backend. Avoid administrator access for routine operation. Separate

permissions for:



\-   Reading and writing the designated state bucket/prefix.

\-   Managing the selected infrastructure resources.

\-   Reading required AWS pricing data, if used.

\-   Inspecting resources needed for inventory/state reconciliation.



Do not store AWS access keys in source code, README files, frontend

variables, or committed environment files. Prefer short-lived

credentials or an IAM role where available.



**### Cost awareness**



The project includes EC2 pricing and monthly cost-estimation logic.

Estimates are indicative and may not include every AWS charge, such as

storage, data transfer, public IPv4, load balancing, taxes, or regional

price changes. Always verify current AWS pricing before approving a

production deployment.



Use small, disposable resources in a dedicated test environment. Apply

AWS budgets and billing alerts before running live infrastructure

operations.



**------------------------------------------------------------------------**



**## Security considerations**



\-   Keep \`.env\` and \`frontend/.env.local\` out of version control.

\-   Never expose API keys, JWT secrets, AWS credentials, Terraform

&#x20;   state, or sensitive plan artifacts in logs or screenshots.

\-   Use HTTPS and secure deployment settings outside local development.

\-   Restrict CORS origins to the actual frontend origin.

\-   Enforce authentication and authorization on protected endpoints.

\-   Use least-privilege IAM permissions.

\-   Keep the Terraform state bucket private and encrypted.

\-   Enable S3 versioning and protect state objects from accidental

&#x20;   deletion.

\-   Preserve Terraform plan/configuration/provider-lock artifacts and

&#x20;   verify their checksums before execution.

\-   Require explicit destroy confirmation and current-state verification

&#x20;   before destructive actions.

\-   Keep apply/destroy feature flags disabled until a reviewed

&#x20;   deployment process is in place.

\-   Do not treat a frontend-only preview or pasted plan JSON as

&#x20;   authorization to execute infrastructure changes.



**------------------------------------------------------------------------**



**## Troubleshooting**



**### Frontend cannot reach the backend**



1\.  Confirm FastAPI is running on port \`8000\`.

2\.  Open \`http\://localhost:8000/health\`.

3\.  Confirm \`VITE_API_BASE_URL\` is set correctly in

&#x20;   \`frontend/.env.local\`.

4\.  Confirm the backend CORS configuration includes the frontend origin.

5\.  Restart Vite after changing frontend environment variables.



**### Swagger UI does not open**



Open the backend URL directly:



\`\`\` text

http\://localhost:8000/docs

\`\`\`



The frontend development server runs on port \`5173\`; \`/docs\` on that

origin is not the FastAPI documentation endpoint.



**### Database connection fails**



1\.  Confirm PostgreSQL is running.

2\.  Confirm the database exists.

3\.  Check the database URL variable used by

&#x20;   \`backend/app/core/database.py\`.

4\.  Verify credentials and port.

5\.  Run \`alembic upgrade head\` after confirming the database connection.



**### Celery worker cannot connect to Redis**



1\.  Confirm Redis is running.

2\.  Check \`CELERY_BROKER_URL\` and \`CELERY_RESULT_BACKEND\`.

3\.  Confirm the worker and backend use the same Redis instance and

&#x20;   database indexes.

4\.  Restart the worker after configuration changes.



**### Terraform plan fails**



1\.  Confirm the Terraform CLI is installed and meets the generated

&#x20;   configuration's required version.

2\.  Confirm AWS credentials and region are available to the backend

&#x20;   process.

3\.  Confirm the S3 state bucket exists and the worker has access.

4\.  Inspect the safe error code returned by the backend.

5\.  Verify that the Terraform configuration, provider lock, and plan

&#x20;   artifacts are present and their checksums match.

6\.  Do not bypass artifact or state verification to force a plan

&#x20;   through.



**### Apply or destroy is blocked**



Check the operation's status, error code, and audit history. Common

intentional blockers include disabled lifecycle feature flags, missing

approvals, missing explicit destroy confirmation, lost worker ownership,

invalid artifacts, and Terraform state snapshot mismatch. Resolve the

underlying condition and generate/review a new plan where required; do

not manually mark an operation successful.



**------------------------------------------------------------------------**



**## Current implementation status**



This section distinguishes application functionality from live cloud

verification.



**&#x20; -----------------------------------------------------------------------**

&#x20; Area                                Documented implementation

**&#x20; ----------------------------------- -----------------------------------**

&#x20; React dashboard and workflow UI     Implemented



&#x20; Authentication UI and backend       Implemented

&#x20; routes



&#x20; Conversation persistence and        Implemented

&#x20; WebSocket chat



&#x20; Requirement collection and          Implemented

&#x20; validation



&#x20; Terraform configuration/plan        Implemented

&#x20; workflow



&#x20; Plan parsing and cost/security      Implemented

&#x20; analysis



&#x20; Approval records and staged review  Implemented



&#x20; Infrastructure operation records    Implemented

&#x20; and audit events



&#x20; S3 state metadata and inventory     Implemented

&#x20; synchronization



&#x20; Apply worker preflight and gated    Implemented in code; live AWS

&#x20; saved-plan execution path           execution must be independently

&#x20;                                     verified



&#x20; Destroy confirmation, state checks, Implemented in code; live AWS

&#x20; and gated saved-plan execution path execution must be independently

&#x20;                                     verified



&#x20; Live AWS                            Not claimed as verified by this

&#x20; provisioning/deprovisioning         README

&#x20; \-----------------------------------------------------------------------



**------------------------------------------------------------------------**



**## Roadmap**



\-   Complete controlled end-to-end AWS Apply validation in a dedicated

&#x20;   test account.

\-   Complete controlled Destroy validation and confirm post-destroy

&#x20;   state/inventory reconciliation.

\-   Validate recovery behavior and failure handling against real worker

&#x20;   interruptions.

\-   Add deployment-specific operational monitoring and alerting.

\-   Review production IAM, network, secrets, and S3 state protections.

\-   Expand automated integration tests for Terraform and AWS behavior.

\-   Finalize deployment packaging and environment-specific runbooks.



**------------------------------------------------------------------------**



**## Acknowledgements**



This project was developed as an AIOps infrastructure automation

platform, combining conversational AI, infrastructure-as-code, approval

governance, and cloud lifecycle management.



**## License**



Add the license applicable to this repository before distributing or

reusing the project.