import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Checkbox,
  FormControlLabel,
  Divider,
  TextField,
  Typography,
} from "@mui/material";

import RefreshIcon from "@mui/icons-material/Refresh";
import HistoryIcon from "@mui/icons-material/History";
import SecurityIcon from "@mui/icons-material/Security";
import PaymentsIcon from "@mui/icons-material/Payments";
import CloudIcon from "@mui/icons-material/Cloud";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";

import {
  apiRequest,
  getCurrentUser,
  getApprovalStages,
  decideApprovalStage,
  createProvisionOperation,
  generateTrustedTerraformDestroyPlan,
  createApproval,
  createDeprovisionOperation,
  getInfrastructureOperations,
  type InfrastructureOperationResponse,
  type ApprovalResponse,
  type ApprovalStage,
  type ApprovalStageReviewResponse,
  type AuthUser,
  type TerraformCostEstimate,
  type TerraformSecurityAnalysis,
} from "./api";

type ApprovalAuditEvent = {
  id: number;
  approval_request_id: number;
  actor_id: number;
  event_type: string;
  previous_status: string | null;
  new_status: string;
  comments: string | null;
  event_metadata: Record<string, unknown>;
  created_at: string;
};

function formatMoney(
  value: string | null | undefined,
  currency = "USD",
): string {
  if (value === null || value === undefined) {
    return "Unavailable";
  }

  const numericValue = Number(value);

  if (!Number.isFinite(numericValue)) {
    return "Unavailable";
  }

  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(numericValue);
}

function StatusChip({ status }: { status: string }) {
  const normalized = status.toLowerCase();

  let color: "success" | "error" | "warning" | "default" = "default";

  if (
    ["approved", "passed", "within_threshold", "create"].includes(normalized)
  ) {
    color = "success";
  } else if (
    ["rejected", "failed", "exceeds_threshold", "delete"].includes(normalized)
  ) {
    color = "error";
  } else if (
    ["pending", "unavailable", "not_evaluated", "update"].includes(normalized)
  ) {
    color = "warning";
  }

  return (
    <Chip
      label={normalized.replaceAll("_", " ")}
      color={color}
      size="small"
      sx={{ textTransform: "capitalize", fontWeight: 600 }}
    />
  );
}

function isDestroyPlanApproval(approval: ApprovalResponse): boolean {
  const counts = approval.plan_summary.resource_summary ?? {};
  const deletes = Number(counts.delete ?? 0);
  const creates = Number(counts.create ?? 0);
  const updates = Number(counts.update ?? 0);
  const replaces = Number(counts.replace ?? 0);

  // A trusted destroy plan contains deletions only. Do not classify a normal
  // change plan that happens to replace or delete one resource as a destroy.
  return deletes > 0 && creates === 0 && updates === 0 && replaces === 0;
}

function SectionTitle({
  icon,
  children,
}: {
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        mb: 2,
      }}
    >
      {icon}
      <Typography variant="h6" sx={{ fontWeight: 700 }}>
        {children}
      </Typography>
    </Box>
  );
}

function CostReview({
  estimate,
}: {
  estimate?: TerraformCostEstimate;
}) {
  if (!estimate) {
    return (
      <Alert severity="warning">
        No cost estimate is available in this Terraform plan.
      </Alert>
    );
  }

  const estimated = estimate.status === "estimated";

  return (
    <Card variant="outlined" sx={{ height: "100%", borderRadius: 2 }}>
      <CardContent>
        <SectionTitle icon={<PaymentsIcon color="primary" />}>
          Cost review
        </SectionTitle>

        <Typography variant="body2" color="text.secondary">
          Estimated monthly EC2 compute cost
        </Typography>

        <Typography variant="h4" sx={{ fontWeight: 800, my: 1 }}>
          {estimated
            ? formatMoney(
                estimate.estimated_monthly_compute_cost,
                estimate.currency,
              )
            : "Unavailable"}
        </Typography>

        <Divider sx={{ my: 2 }} />

        <Box sx={{ display: "flex", justifyContent: "space-between", mb: 1 }}>
          <Typography variant="body2" color="text.secondary">
            Instance type
          </Typography>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {estimate.instance_type}
          </Typography>
        </Box>

        <Box sx={{ display: "flex", justifyContent: "space-between", mb: 1 }}>
          <Typography variant="body2" color="text.secondary">
            Region
          </Typography>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {estimate.region}
          </Typography>
        </Box>

        <Box sx={{ display: "flex", justifyContent: "space-between", mb: 1 }}>
          <Typography variant="body2" color="text.secondary">
            Budget threshold
          </Typography>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {estimate.budget_threshold === null
              ? "Not configured"
              : formatMoney(estimate.budget_threshold, estimate.currency)}
          </Typography>
        </Box>

        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 1,
            mb: 1,
          }}
        >
          <Typography variant="body2" color="text.secondary">
            Budget status
          </Typography>
          <StatusChip status={estimate.budget_status} />
        </Box>

        {estimate.pricing_source && (
          <Box sx={{ mt: 2 }}>
            <Divider sx={{ mb: 2 }} />

            <Typography variant="body2">
              <strong>Pricing source:</strong> {estimate.pricing_source}
            </Typography>

            <Typography variant="caption" color="text.secondary">
              Verified on: {estimate.pricing_verified_on}
            </Typography>
          </Box>
        )}

        <Alert severity={estimated ? "info" : "warning"} sx={{ mt: 2 }}>
          {estimate.note}
        </Alert>

        {estimate.assumptions?.excludes && (
          <Box sx={{ mt: 2 }}>
            <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 0.5 }}>
              Excluded from this estimate
            </Typography>

            <Typography variant="body2" color="text.secondary">
              {estimate.assumptions.excludes.join(", ")}
            </Typography>
          </Box>
        )}
      </CardContent>
    </Card>
  );
}

function SecurityReview({
  analysis,
}: {
  analysis?: TerraformSecurityAnalysis;
}) {
  if (!analysis) {
    return (
      <Alert severity="warning">
        Security analysis is missing. This must not be interpreted as a
        security pass.
      </Alert>
    );
  }

  const securityPassed =
    analysis.checks_completed && analysis.passed;

  return (
    <Card variant="outlined" sx={{ borderRadius: 2 }}>
      <CardContent>
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: { xs: "flex-start", sm: "center" },
            flexDirection: { xs: "column", sm: "row" },
            gap: 2,
          }}
        >
          <SectionTitle icon={<SecurityIcon color="primary" />}>
            Security review
          </SectionTitle>

          <Chip
            icon={
              securityPassed ? (
                <CheckCircleIcon />
              ) : (
                <WarningAmberIcon />
              )
            }
            label={
              !analysis.checks_completed
                ? "Checks incomplete"
                : analysis.passed
                  ? "No findings detected"
                  : `${analysis.finding_count} finding(s)`
            }
            color={securityPassed ? "success" : "warning"}
          />
        </Box>

        <Typography variant="caption" color="text.secondary">
          Policy version: {analysis.policy_version}
        </Typography>

        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: {
              xs: "repeat(2, 1fr)",
              sm: "repeat(4, 1fr)",
            },
            gap: 1.5,
            mt: 2,
          }}
        >
          {(
            ["critical", "high", "medium", "low"] as const
          ).map((severity) => (
            <Box
              key={severity}
              sx={{
                p: 1.5,
                bgcolor: "action.hover",
                borderRadius: 2,
              }}
            >
              <Typography
                variant="h5"
                sx={{
                  fontWeight: 800,
                  color:
                    severity === "critical" || severity === "high"
                      ? "error.main"
                      : "text.primary",
                }}
              >
                {analysis.severity_counts[severity]}
              </Typography>

              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ textTransform: "capitalize" }}
              >
                {severity}
              </Typography>
            </Box>
          ))}
        </Box>

        {analysis.findings.length > 0 ? (
          <Box sx={{ mt: 2 }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 700, mb: 1 }}>
              Security findings
            </Typography>

            {analysis.findings.map((finding, index) => (
              <Box
                key={`${finding.rule_id}-${index}`}
                sx={{
                  p: 2,
                  mb: 1.5,
                  border: 1,
                  borderColor: "divider",
                  borderRadius: 2,
                }}
              >
                <Box
                  sx={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    flexWrap: "wrap",
                    gap: 1,
                  }}
                >
                  <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
                    {finding.title}
                  </Typography>

                  <StatusChip status={finding.severity} />
                </Box>

                <Typography variant="caption" color="text.secondary">
                  {finding.rule_id} · {finding.address}
                </Typography>

                <Typography variant="body2" sx={{ mt: 1 }}>
                  {finding.detail}
                </Typography>

                <Alert severity="info" sx={{ mt: 1 }}>
                  <strong>Remediation:</strong> {finding.remediation}
                </Alert>
              </Box>
            ))}
          </Box>
        ) : (
          <Alert severity={securityPassed ? "success" : "warning"} sx={{ mt: 2 }}>
            {securityPassed
              ? "No findings were detected by the configured initial security policy. This is not a complete CIS benchmark or a guarantee of production security."
              : "No findings are listed, but the security checks have not passed or are incomplete."}
          </Alert>
        )}

        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 2 }}
        >
          Deployment enabled: {analysis.deployment_enabled ? "Yes" : "No"}
        </Typography>
      </CardContent>
    </Card>
  );
}
const APPROVAL_STAGE_DETAILS: Record<
  ApprovalStage,
  { title: string; description: string }
> = {
  technical: {
    title: "Technical review",
    description:
      "Review the Terraform configuration and planned resource changes.",
  },
  cost: {
    title: "Cost review",
    description:
      "Review the cost estimate, budget status and any required override.",
  },
  security: {
    title: "Security review",
    description:
      "Review security policy results and any identified findings.",
  },
};

export default function ApprovalReviewPanel() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [approvals, setApprovals] = useState<ApprovalResponse[]>([]);

  const [stageReviews, setStageReviews] = useState<
    Record<number, ApprovalStageReviewResponse[]>
  >({});

  const [stageComments, setStageComments] = useState<
    Record<string, string>
  >({});

  const [costOverrides, setCostOverrides] = useState<
    Record<number, boolean>
  >({});

  const [comments, setComments] = useState<Record<number, string>>({});
  const [audit, setAudit] = useState<Record<number, ApprovalAuditEvent[]>>({});
  const [loading, setLoading] = useState(true);
  const [workingId, setWorkingId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [applyingId, setApplyingId] = useState<number | null>(null);
  const [destroyPlanRequestId, setDestroyPlanRequestId] = useState<number | null>(null);
  const [destroyingApprovalId, setDestroyingApprovalId] = useState<number | null>(null);
  const [destroyConfirmations, setDestroyConfirmations] = useState<Record<number, boolean>>({});
  const [allOperations, setAllOperations] = useState<InfrastructureOperationResponse[]>([]);
  const [operations, setOperations] = useState<
    Record<number, { id: number; status: string; operation: string }>
  >({});

  const loadApprovals = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const [currentUser, result, existingOperations] = await Promise.all([
        getCurrentUser(),
        apiRequest<ApprovalResponse[]>("/approvals"),
        getInfrastructureOperations(),
      ]);

      setUser(currentUser);
      setApprovals(result);
      setAllOperations(existingOperations);

      // Index the latest known operation by approval ID so already-applied
      // requests never display another Apply button after refresh/login.
      const operationEntries = new Map<
        number,
        InfrastructureOperationResponse
      >();
      for (const operation of existingOperations) {
        const previous = operationEntries.get(operation.approval_request_id);
        if (
          !previous ||
          new Date(operation.created_at).getTime() >
            new Date(previous.created_at).getTime()
        ) {
          operationEntries.set(operation.approval_request_id, operation);
        }
      }
      setOperations(
        Object.fromEntries(
          Array.from(operationEntries.entries()).map(([approvalId, operation]) => [
            approvalId,
            { id: operation.id, status: operation.status, operation:operation.operation},
          ]),
        ),
      );

      const stageEntries = await Promise.all(
        result.map(async (approval) => {
          try {
            const stages = await getApprovalStages(approval.id);
            return [approval.id, stages] as const;
          } catch {
            return [approval.id, []] as const;
          }
        }),
      );

      setStageReviews(Object.fromEntries(stageEntries));
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to load approval requests.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadApprovals();
  }, [loadApprovals]);

  async function loadAudit(approvalId: number) {
    setError("");
    setNotice("");

    try {
      const events = await apiRequest<ApprovalAuditEvent[]>(
        `/approvals/${approvalId}/audit`,
      );

      setAudit((previous) => ({
        ...previous,
        [approvalId]: events,
      }));
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to load audit history.",
      );
    }
  }

  async function decideStage(
    approvalId: number,
    stage: ApprovalStage,
    decision: "approved" | "rejected",
  ) {
    const key = `${approvalId}-${stage}`;

    const selectedApproval = approvals.find(
      (item) => item.id === approvalId,
    );

    const estimate = selectedApproval?.plan_summary.cost_estimate;

    const costNeedsOverride =
      stage === "cost" &&
      (!estimate ||
        estimate.status !== "estimated" ||
        [
          "exceeds_threshold",
          "not_configured",
          "not_evaluated",
        ].includes(estimate.budget_status));

    if (
      stage === "cost" &&
      decision === "approved" &&
      costNeedsOverride &&
      (!costOverrides[approvalId] || !stageComments[key]?.trim())
    ) {
      setError(
        "This cost review requires a manual override and a documented justification.",
      );
      return;
    }

    setWorkingId(approvalId);
    setError("");
    setNotice("");

    try {
      await decideApprovalStage(approvalId, stage, {
        decision,
        comments: stageComments[key]?.trim() || null,
        manual_cost_override:
          stage === "cost" && decision === "approved"
            ? costOverrides[approvalId] === true
            : false,
      });

      setNotice(
        `${APPROVAL_STAGE_DETAILS[stage].title} ${decision}.`,
      );

      await loadApprovals();
      await loadAudit(approvalId);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : `Unable to ${decision} ${stage} review.`,
      );
    } finally {
      setWorkingId(null);
    }
  }

  async function decide(
    approvalId: number,
    decision: "approved" | "rejected",
  ) {
    setWorkingId(approvalId);
    setError("");
    setNotice("");

    try {
      await apiRequest<ApprovalResponse>(
        `/approvals/${approvalId}/decision`,
        {
          method: "POST",
          body: JSON.stringify({
            decision,
            comments: comments[approvalId]?.trim() || null,
          }),
        },
      );

      setNotice(`Approval #${approvalId} was ${decision}.`);

      await loadApprovals();
      await loadAudit(approvalId);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : `Unable to ${decision} request.`,
      );
    } finally {
      setWorkingId(null);
    }
  }

  async function applyInfrastructure(approvalId: number) {
    if (applyingId !== null || operations[approvalId]) {
      return;
    }

    setApplyingId(approvalId);
    setError("");
    setNotice("");

    try {
      const operation = await createProvisionOperation(approvalId);
      setAllOperations((previous) => [...previous, operation]);

      setOperations((previous) => ({
  ...previous,
  [approvalId]: {
    id: operation.id,
    status: operation.status,
    operation: operation.operation,
  },
}));

      setNotice(
        `Deployment operation #${operation.id} was created. Current status: ${operation.status}.`,
      );
      await loadApprovals();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to start infrastructure deployment.",
      );
    } finally {
      setApplyingId(null);
    }
  }

  async function generateDestroyApproval(requestId: number) {
    if (destroyPlanRequestId !== null) return;

    setDestroyPlanRequestId(requestId);
    setError("");
    setNotice("");

    try {
      const plan = await generateTrustedTerraformDestroyPlan(requestId);
      await createApproval(requestId, plan.id);
      setNotice(
        `Destroy plan #${plan.id} was generated and submitted as a separate approval request.`,
      );
      await loadApprovals();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to generate the trusted Terraform destroy plan.",
      );
    } finally {
      setDestroyPlanRequestId(null);
    }
  }

  async function destroyInfrastructure(approvalId: number) {
    if (
      destroyingApprovalId !== null ||
      destroyConfirmations[approvalId] !== true
    ) {
      return;
    }

    setDestroyingApprovalId(approvalId);
    setError("");
    setNotice("");

    try {
      const operation = await createDeprovisionOperation(approvalId);
      setAllOperations((previous) => [...previous, operation]);
      setOperations((previous) => ({
        ...previous,
        [approvalId]: {
          id: operation.id,
          status: operation.status,
          operation: operation.operation,
        },
      }));
      setNotice(
        `Destroy operation #${operation.id} was created. Current status: ${operation.status}.`,
      );
      await loadApprovals();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to start the controlled infrastructure destroy.",
      );
    } finally {
      setDestroyingApprovalId(null);
    }
  }

  return (
    <Card
sx={{
    borderRadius: 3,
    border: "1px solid",
    borderColor: "divider",
    boxShadow: "0 4px 18px rgba(15, 23, 42, 0.04)",
  }}
>
      <CardContent sx={{ p: { xs: 2, md: 3 } }}>
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: { xs: "flex-start", sm: "center" },
            flexDirection: { xs: "column", sm: "row" },
            gap: 2,
            mb: 3,
          }}
        >
          <Box>
            <Typography variant="h5" sx={{ fontWeight: 800 }}>
              Approval Review
            </Typography>

            <Typography color="text.secondary">
              Infrastructure plan, cost and security governance.
            </Typography>
          </Box>

          <Button
  variant="outlined"
  startIcon={
    loading ? (
      <CircularProgress size={16} />
    ) : (
      <RefreshIcon />
    )
  }
  onClick={() => void loadApprovals()}
  disabled={loading}
  sx={{
    borderRadius: 2,
    px: 2,
    textTransform: "none",
    fontWeight: 700,
  }}
>
  {loading ? "Refreshing..." : "Refresh"}
</Button>
        </Box>

        {user && (
          <Alert
            severity={user.role === "admin" ? "info" : "warning"}
            sx={{ mb: 2 }}
          >
            {user.role === "admin"
              ? "Admin access: you can review requests submitted by other users."
              : "Requester view: you can see your own requests. Admins must decide pending approvals."}
          </Alert>
        )}

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {notice && (
          <Alert severity="success" sx={{ mb: 2 }}>
            {notice}
          </Alert>
        )}

        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", p: 4 }}>
            <CircularProgress />
          </Box>
        ) : approvals.length === 0 ? (
          <Alert severity="info">
            No approval requests found.
          </Alert>
        ) : (
          <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {approvals.map((approval) => {
              const isAdmin = user?.role === "admin";
              const isRequester = user?.id === approval.requester_id;

              const canDecide =
                isAdmin &&
                !isRequester &&
                approval.status === "pending";

              const summary = approval.plan_summary;
              const resources = summary.resources ?? [];
              const cost = summary.cost_estimate;
              const security = summary.security_analysis;

              return (
                                <Card
                  key={approval.id}
                  variant="outlined"
                  sx={{
                    borderRadius: 2.5,
                    borderColor: "divider",
                    overflow: "hidden",
                    transition: "box-shadow 0.2s ease, border-color 0.2s ease",
                    "&:hover": {
                      borderColor: "primary.light",
                      boxShadow: "0 4px 16px rgba(15, 23, 42, 0.06)",
                    },
                  }}
                >
                  <CardContent>
                    <Box
                      sx={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: {
                          xs: "flex-start",
                          sm: "center",
                        },
                        flexDirection: {
                          xs: "column",
                          sm: "row",
                        },
                        gap: 1,
                      }}
                    >
                      <Box>
                        <Typography
                          variant="h6"
                          sx={{ fontWeight: 800 }}
                        >
                          Approval #{approval.id}
                        </Typography>

                        <Typography
                          variant="body2"
                          color="text.secondary"
                        >
                          Infrastructure Request #
                          {approval.infrastructure_request_id}
                        </Typography>

                        <Typography
                          variant="caption"
                          color="text.secondary"
                        >
                          Submitted:{" "}
                          {new Date(
                            approval.created_at,
                          ).toLocaleString()}
                        </Typography>
                      </Box>

                      <StatusChip status={approval.status} />
                    </Box>

                    <Divider sx={{ my: 2 }} />

                    <Box
                      sx={{
                        display: "grid",
                        gridTemplateColumns: {
                          xs: "1fr",
                          md: "repeat(2, minmax(0, 1fr))",
                        },
                        gap: 2,
                      }}
                    >
                      <CostReview estimate={cost} />

                      <Card
                        variant="outlined"
                        sx={{ borderRadius: 2 }}
                      >
                        <CardContent>
                          <SectionTitle
                            icon={<CloudIcon color="primary" />}
                          >
                            Terraform resources
                          </SectionTitle>

                          <Box
                            sx={{
                              display: "grid",
                              gridTemplateColumns:
                                "repeat(2, minmax(0, 1fr))",
                              gap: 1,
                            }}
                          >
                            {Object.entries(
                              summary.resource_summary ?? {},
                            ).map(([action, count]) => (
                              <Box
                                key={action}
                                sx={{
                                  p: 1.5,
                                  bgcolor: "action.hover",
                                  borderRadius: 2,
                                }}
                              >
                                <Typography
                                  variant="h5"
                                  sx={{ fontWeight: 800 }}
                                >
                                  {count}
                                </Typography>

                                <Typography
                                  variant="caption"
                                  color="text.secondary"
                                  sx={{ textTransform: "capitalize" }}
                                >
                                  {action.replaceAll("_", " ")}
                                </Typography>
                              </Box>
                            ))}
                          </Box>

                          <Divider sx={{ my: 2 }} />

                          <Typography
                            variant="subtitle2"
                            sx={{ fontWeight: 700, mb: 1 }}
                          >
                            Planned resource changes
                          </Typography>

                          {resources.length === 0 ? (
                            <Typography
                              variant="body2"
                              color="text.secondary"
                            >
                              No resource details available.
                            </Typography>
                          ) : (
                            resources.map((resource, index) => (
                              <Box
                                key={`${resource.address}-${index}`}
                                sx={{
                                  p: 1.2,
                                  mb: 1,
                                  border: 1,
                                  borderColor: "divider",
                                  borderRadius: 1.5,
                                }}
                              >
                                <Typography
                                  variant="body2"
                                  sx={{ fontWeight: 700 }}
                                >
                                  {resource.address ??
                                    resource.name ??
                                    "Resource"}
                                </Typography>

                                <Typography
                                  variant="caption"
                                  color="text.secondary"
                                >
                                  {resource.type ?? "Unknown type"}
                                </Typography>

                                <Box
                                  sx={{
                                    display: "flex",
                                    flexWrap: "wrap",
                                    gap: 0.5,
                                    mt: 0.5,
                                  }}
                                >
                                  {resource.actions.map(
                                    (action, index) => (
                                      <StatusChip
                                        key={`${action}-${index}`}
                                        status={action}
                                      />
                                    ),
                                  )}
                                </Box>
                              </Box>
                            ))
                          )}

                          <Typography
                            variant="caption"
                            color="text.secondary"
                            sx={{ display: "block", mt: 2 }}
                          >
                            Terraform version:{" "}
                            {summary.terraform_version ?? "Unknown"}
                          </Typography>
                        </CardContent>
                      </Card>
                    </Box>

                    <Box sx={{ mt: 2 }}>
                      <SecurityReview analysis={security} />
                    </Box>
                                        {/* Three-stage approval workflow */}
                    <Divider sx={{ my: 3 }} />

                    <Typography
                      variant="h6"
                      sx={{ fontWeight: 800, mb: 1 }}
                    >
                      Multi-stage approval workflow
                    </Typography>

                    <Typography
                      variant="body2"
                      color="text.secondary"
                      sx={{ mb: 2 }}
                    >
                      Technical, cost and security reviews must all be
                      approved before final approval.
                    </Typography>

                    {(() => {
                      const stages = stageReviews[approval.id] ?? [];

                      const allStagesApproved =
                        stages.length === 3 &&
                        ([
                          "technical",
                          "cost",
                          "security",
                        ] as ApprovalStage[]).every((stage) =>
                          stages.some(
                            (review) =>
                              review.stage === stage &&
                              review.status === "approved",
                          ),
                        );

                      const stagesLoaded =
                        Object.prototype.hasOwnProperty.call(
                          stageReviews,
                          approval.id,
                        );

                      return (
                        <>
                          {!stagesLoaded ? (
                            <Alert severity="info">
                              Loading approval stages...
                            </Alert>
                          ) : stages.length !== 3 ? (
                            <Alert severity="warning">
                              Three-stage review records are unavailable.
                              Refresh or check the backend migration.
                            </Alert>
                          ) : (
                            <Box
                              sx={{
                                display: "grid",
                                gridTemplateColumns: {
                                  xs: "1fr",
                                  md: "repeat(3, minmax(0, 1fr))",
                                },
                                gap: 2,
                              }}
                            >
                              {(
                                [
                                  "technical",
                                  "cost",
                                  "security",
                                ] as ApprovalStage[]
                              ).map((stage) => {
                                const review = stages.find(
                                  (item) => item.stage === stage,
                                );

                                if (!review) {
                                  return (
                                    <Alert
                                      key={stage}
                                      severity="warning"
                                    >
                                      Missing {stage} review record.
                                    </Alert>
                                  );
                                }

                                const key = `${approval.id}-${stage}`;

                                const costNeedsOverride =
                                  stage === "cost" &&
                                  (!cost ||
                                    cost.status !== "estimated" ||
                                    [
                                      "exceeds_threshold",
                                      "not_configured",
                                      "not_evaluated",
                                    ].includes(cost.budget_status));

                                const overrideReady =
                                  !costNeedsOverride ||
                                  (costOverrides[approval.id] === true &&
                                    Boolean(
                                      stageComments[key]?.trim(),
                                    ));

                                const canReviewStage =
                                  canDecide &&
                                  review.status === "pending";

                                return (
                                  <Card
                                    key={stage}
                                    variant="outlined"
                                    sx={{
                                      borderRadius: 2,
                                      borderTop: 4,
                                      borderTopColor:
                                        review.status === "approved"
                                          ? "success.main"
                                          : review.status === "rejected"
                                            ? "error.main"
                                            : "warning.main",
                                    }}
                                  >
                                    <CardContent>
                                      <Typography
                                        variant="subtitle1"
                                        sx={{
                                          fontWeight: 800,
                                          mb: 1,
                                        }}
                                      >
                                        {APPROVAL_STAGE_DETAILS[stage].title}
                                      </Typography>

                                      <Typography
                                        variant="body2"
                                        color="text.secondary"
                                        sx={{ mb: 2 }}
                                      >
                                        {
                                          APPROVAL_STAGE_DETAILS[stage]
                                            .description
                                        }
                                      </Typography>

                                      <StatusChip
                                        status={review.status}
                                      />

                                      {review.decided_by_id !== null && (
                                        <Typography
                                          variant="caption"
                                          color="text.secondary"
                                          sx={{
                                            display: "block",
                                            mt: 1,
                                          }}
                                        >
                                          Reviewed by user #
                                          {review.decided_by_id}
                                        </Typography>
                                      )}

                                      {review.decided_at && (
                                        <Typography
                                          variant="caption"
                                          color="text.secondary"
                                          sx={{
                                            display: "block",
                                          }}
                                        >
                                          {new Date(
                                            review.decided_at,
                                          ).toLocaleString()}
                                        </Typography>
                                      )}

                                      {review.comments && (
                                        <Alert
                                          severity={
                                            review.status === "rejected"
                                              ? "error"
                                              : "info"
                                          }
                                          sx={{ mt: 2 }}
                                        >
                                          {review.comments}
                                        </Alert>
                                      )}

                                      {canReviewStage && (
                                        <Box sx={{ mt: 2 }}>
                                          <TextField
                                            label="Reviewer comments"
                                            value={
                                              stageComments[key] ?? ""
                                            }
                                            onChange={(event) =>
                                              setStageComments(
                                                (previous) => ({
                                                  ...previous,
                                                  [key]:
                                                    event.target.value,
                                                }),
                                              )
                                            }
                                            multiline
                                            minRows={2}
                                            fullWidth
                                            size="small"
                                          />

                                          {costNeedsOverride && (
                                            <Box sx={{ mt: 1 }}>
                                              <Alert severity="warning">
                                                Cost estimate or budget
                                                evaluation is incomplete.
                                                A documented manual override
                                                is required to approve this
                                                review.
                                              </Alert>

                                              <FormControlLabel
                                                control={
                                                  <Checkbox
                                                    checked={
                                                      costOverrides[
                                                        approval.id
                                                      ] === true
                                                    }
                                                    onChange={(event) =>
                                                      setCostOverrides(
                                                        (previous) => ({
                                                          ...previous,
                                                          [approval.id]:
                                                            event.target
                                                              .checked,
                                                        }),
                                                      )
                                                    }
                                                  />
                                                }
                                                label="I have manually reviewed and accept the cost risk."
                                              />
                                            </Box>
                                          )}

                                          <Box
                                            sx={{
                                              display: "flex",
                                              flexWrap: "wrap",
                                              gap: 1,
                                              mt: 1.5,
                                            }}
                                          >
                                            <Button
                                              variant="contained"
                                              color="success"
                                              size="small"
                                              disabled={
                                                workingId !== null ||
                                                !overrideReady
                                              }
                                              onClick={() =>
                                                void decideStage(
                                                  approval.id,
                                                  stage,
                                                  "approved",
                                                )
                                              }
                                            >
                                              {workingId === approval.id
                                                ? "Processing..."
                                                : "Approve"}
                                            </Button>

                                            <Button
                                              variant="outlined"
                                              color="error"
                                              size="small"
                                              disabled={
                                                workingId !== null
                                              }
                                              onClick={() =>
                                                void decideStage(
                                                  approval.id,
                                                  stage,
                                                  "rejected",
                                                )
                                              }
                                            >
                                              Reject
                                            </Button>
                                          </Box>
                                        </Box>
                                      )}
                                    </CardContent>
                                  </Card>
                                );
                              })}
                            </Box>
                          )}

                          <Box sx={{ mt: 3 }}>
                            <Typography
                              variant="subtitle1"
                              sx={{ fontWeight: 800, mb: 1 }}
                            >
                              Final approval
                            </Typography>

                            <Alert
                              severity={
                                allStagesApproved
                                  ? "success"
                                  : "warning"
                              }
                              sx={{ mb: 2 }}
                            >
                              {allStagesApproved
                                ? "All three review stages are approved. Final approval is now available."
                                : "Final approval is locked until Technical, Cost and Security reviews are all approved."}
                            </Alert>

                            {canDecide && (
                              <>
                                <TextField
                                  label="Final reviewer comments (optional)"
                                  value={comments[approval.id] ?? ""}
                                  onChange={(event) =>
                                    setComments((previous) => ({
                                      ...previous,
                                      [approval.id]:
                                        event.target.value,
                                    }))
                                  }
                                  multiline
                                  minRows={2}
                                  fullWidth
                                />

                                <Box
                                  sx={{
                                    display: "flex",
                                    flexWrap: "wrap",
                                    gap: 1,
                                    mt: 1.5,
                                  }}
                                >
                                  <Button
                                    variant="contained"
                                    color="success"
                                    disabled={
                                      workingId !== null ||
                                      !allStagesApproved
                                    }
                                    onClick={() =>
                                      void decide(
                                        approval.id,
                                        "approved",
                                      )
                                    }
                                  >
                                    {workingId === approval.id
                                      ? "Processing..."
                                      : "Final approve"}
                                  </Button>

                                  <Button
                                    variant="outlined"
                                    color="error"
                                    disabled={workingId !== null}
                                    onClick={() =>
                                      void decide(
                                        approval.id,
                                        "rejected",
                                      )
                                    }
                                  >
                                    Reject request
                                  </Button>
                                </Box>
                              </>
                            )}

                            {isRequester &&
                              approval.status === "approved" &&
                              allStagesApproved &&
                              (() => {
                                const destroyApproval = isDestroyPlanApproval(approval);
                                const requestOperations = allOperations.filter(
                                  (item) =>
                                    item.infrastructure_request_id ===
                                    approval.infrastructure_request_id,
                                );
                                const provisionOperation = requestOperations
                                  .filter((item) => item.operation === "provision")
                                  .sort(
                                    (a, b) =>
                                      new Date(b.created_at).getTime() -
                                      new Date(a.created_at).getTime(),
                                  )[0];
                                const destroyOperation = requestOperations
                                  .filter((item) => item.operation === "deprovision")
                                  .sort(
                                    (a, b) =>
                                      new Date(b.created_at).getTime() -
                                      new Date(a.created_at).getTime(),
                                  )[0];
                                const destroyApprovalsForRequest = approvals.filter(
                                  (item) =>
                                    item.infrastructure_request_id ===
                                      approval.infrastructure_request_id &&
                                    isDestroyPlanApproval(item),
                                );
                                const hasDestroyApproval =
                                  destroyApprovalsForRequest.length > 0;
                                const destroySucceeded =
                                  destroyOperation?.status.toLowerCase() ===
                                  "succeeded";

                                return (
                                  <Box
                                    sx={{
                                      mt: 3,
                                      p: 2.5,
                                      border: 1,
                                      borderColor: destroyApproval
                                        ? "warning.main"
                                        : "success.main",
                                      borderRadius: 2,
                                    }}
                                  >
                                    <Typography
                                      variant="h6"
                                      sx={{ fontWeight: 800 }}
                                    >
                                      {destroyApproval
                                        ? "Infrastructure Destruction"
                                        : "Infrastructure Deployment"}
                                    </Typography>

                                    {destroyApproval ? (
                                      destroyOperation ? (
                                        <Alert
                                          severity={
                                            destroySucceeded
                                              ? "success"
                                              : destroyOperation.status.toLowerCase() === "failed"
                                                ? "error"
                                                : "info"
                                          }
                                          sx={{ my: 2 }}
                                        >
                                          <strong>Destroy Infrastructure</strong>
                                          {" — "}
                                          {destroySucceeded
                                            ? "Done"
                                            : destroyOperation.status.toLowerCase() === "failed"
                                              ? "Failed"
                                              : "In Progress"}
                                          {" (Operation #"}
                                          {destroyOperation.id}
                                          {")."}
                                        </Alert>
                                      ) : (
                                        <>
                                          <Alert severity="warning" sx={{ my: 2 }}>
                                            All three review stages and final approval
                                            are complete. Confirm explicitly before
                                            starting Terraform destroy.
                                          </Alert>
                                          <FormControlLabel
                                            control={
                                              <Checkbox
                                                checked={
                                                  destroyConfirmations[approval.id] === true
                                                }
                                                onChange={(event) =>
                                                  setDestroyConfirmations((previous) => ({
                                                    ...previous,
                                                    [approval.id]: event.target.checked,
                                                  }))
                                                }
                                              />
                                            }
                                            label="I understand this permanently deletes the approved infrastructure resources."
                                          />
                                          <Button
                                            variant="contained"
                                            color="error"
                                            fullWidth
                                            sx={{ mt: 1 }}
                                            disabled={
                                              destroyingApprovalId !== null ||
                                              destroyConfirmations[approval.id] !== true
                                            }
                                            onClick={() =>
                                              void destroyInfrastructure(approval.id)
                                            }
                                          >
                                            {destroyingApprovalId === approval.id
                                              ? "Starting destroy..."
                                              : "Destroy Infrastructure"}
                                          </Button>
                                        </>
                                      )
                                    ) : (
                                      <>
                                        {provisionOperation ? (
                                          <Alert
                                            severity={
                                              provisionOperation.status.toLowerCase() === "succeeded"
                                                ? "success"
                                                : provisionOperation.status.toLowerCase() === "failed"
                                                  ? "error"
                                                  : "info"
                                            }
                                            sx={{ my: 2 }}
                                          >
                                            <strong>Apply Infrastructure</strong>
                                            {" — "}
                                            {provisionOperation.status.toLowerCase() === "succeeded"
                                              ? "Done"
                                              : provisionOperation.status.toLowerCase() === "failed"
                                                ? "Failed"
                                                : "In Progress"}
                                            {" (Operation #"}
                                            {provisionOperation.id}
                                            {")."}
                                          </Alert>
                                        ) : (
                                          <Alert severity="info" sx={{ my: 2 }}>
                                            All three review stages and final approval
                                            are complete. Infrastructure is ready to deploy.
                                          </Alert>
                                        )}

                                        {!provisionOperation && (
                                          <Button
                                            variant="contained"
                                            color="success"
                                            fullWidth
                                            disabled={applyingId !== null}
                                            onClick={() =>
                                              void applyInfrastructure(approval.id)
                                            }
                                          >
                                            {applyingId === approval.id
                                              ? "Starting deployment..."
                                              : "Apply Infrastructure"}
                                          </Button>
                                        )}

                                        {destroySucceeded ? (
                                          <Alert severity="success" sx={{ mt: 2 }}>
                                            <strong>Destroy Infrastructure — Done</strong>
                                            {" (Operation #"}
                                            {destroyOperation?.id}
                                            {")."}
                                            No further destroy action is available.
                                          </Alert>
                                        ) : provisionOperation?.status.toLowerCase() === "succeeded" &&
                                          !hasDestroyApproval ? (
                                          <Button
                                            variant="outlined"
                                            color="error"
                                            fullWidth
                                            sx={{ mt: 2 }}
                                            disabled={destroyPlanRequestId !== null}
                                            onClick={() =>
                                              void generateDestroyApproval(
                                                approval.infrastructure_request_id,
                                              )
                                            }
                                          >
                                            {destroyPlanRequestId ===
                                            approval.infrastructure_request_id
                                              ? "Generating destroy plan..."
                                              : "Generate Destroy Plan"}
                                          </Button>
                                        ) : hasDestroyApproval ? (
                                          <Alert severity="info" sx={{ mt: 2 }}>
                                            A separate destroy approval already exists.
                                            Review and complete that approval before
                                            destruction can proceed.
                                          </Alert>
                                        ) : provisionOperation?.status.toLowerCase() !== "succeeded" ? (
                                          <Alert severity="warning" sx={{ mt: 2 }}>
                                            Destroy planning is available only after a
                                            successful provisioning operation.
                                          </Alert>
                                        ) : null}
                                      </>
                                    )}
                                  </Box>
                                );
                              })()}
                          </Box>
                        </>
                      );
                    })()}

                    {isAdmin &&
                      isRequester &&
                      approval.status === "pending" && (
                        <Alert severity="warning" sx={{ mt: 2 }}>
                          You cannot approve or reject your own request.
                        </Alert>
                      )}

                    <Divider sx={{ my: 2 }} />

                    <Button
                      startIcon={<HistoryIcon />}
                      onClick={() => void loadAudit(approval.id)}
                    >
                      View audit history
                    </Button>

                    {audit[approval.id] && (
                      <Box
                        sx={{
                          display: "flex",
                          flexDirection: "column",
                          gap: 1.5,
                          mt: 2,
                        }}
                      >
                        {audit[approval.id].length === 0 ? (
                          <Typography
                            variant="body2"
                            color="text.secondary"
                          >
                            No audit events found.
                          </Typography>
                        ) : (
                          audit[approval.id].map((event) => (
                            <Box
                              key={event.id}
                              sx={{
                                p: 1.5,
                                bgcolor: "action.hover",
                                borderRadius: 2,
                                borderLeft: 3,
                                borderColor: "primary.main",
                              }}
                            >
                              <Typography sx={{ fontWeight: 700 }}>
                                {event.event_type.replaceAll("_", " ")}
                              </Typography>

                              <Typography variant="body2">
                                {event.previous_status ?? "—"} →{" "}
                                {event.new_status}
                              </Typography>

                              <Typography
                                variant="caption"
                                color="text.secondary"
                              >
                                Actor #{event.actor_id} ·{" "}
                                {new Date(
                                  event.created_at,
                                ).toLocaleString()}
                              </Typography>

                              {event.comments && (
                                <Typography
                                  variant="body2"
                                  sx={{ mt: 0.5 }}
                                >
                                  {event.comments}
                                </Typography>
                              )}
                            </Box>
                          ))
                        )}
                      </Box>
                    )}
                  </CardContent>
                </Card>
              );
            })}
          </Box>
        )}

        <Alert severity="info" sx={{ mt: 3 }}>
          Approval decisions are recorded in the audit trail. Requesters can
          start a controlled Terraform apply after all approval stages and the
          final decision are approved. Destroy requires a separate trusted plan,
          separate three-stage approval and explicit confirmation. Security
          findings reflect the configured initial policy checks.
        </Alert>
      </CardContent>
    </Card>
  );
}