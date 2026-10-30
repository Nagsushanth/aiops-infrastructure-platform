
import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
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
  type ApprovalResponse,
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

export default function ApprovalReviewPanel() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [approvals, setApprovals] = useState<ApprovalResponse[]>([]);
  const [comments, setComments] = useState<Record<number, string>>({});
  const [audit, setAudit] = useState<Record<number, ApprovalAuditEvent[]>>({});
  const [loading, setLoading] = useState(true);
  const [workingId, setWorkingId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const loadApprovals = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const [currentUser, result] = await Promise.all([
        getCurrentUser(),
        apiRequest<ApprovalResponse[]>("/approvals"),
      ]);

      setUser(currentUser);
      setApprovals(result);
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

  return (
    <Card sx={{ borderRadius: 3 }}>
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
            startIcon={<RefreshIcon />}
            onClick={() => void loadApprovals()}
            disabled={loading}
          >
            Refresh
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
          <Alert severity="info">No approval requests found.</Alert>
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
                  sx={{ borderRadius: 2 }}
                >
                  <CardContent>
                    <Box
                      sx={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: { xs: "flex-start", sm: "center" },
                        flexDirection: { xs: "column", sm: "row" },
                        gap: 1,
                      }}
                    >
                      <Box>
                        <Typography variant="h6" sx={{ fontWeight: 800 }}>
                          Approval #{approval.id}
                        </Typography>

                        <Typography variant="body2" color="text.secondary">
                          Infrastructure Request #
                          {approval.infrastructure_request_id}
                        </Typography>

                        <Typography variant="caption" color="text.secondary">
                          Submitted:{" "}
                          {new Date(approval.created_at).toLocaleString()}
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
                          <SectionTitle icon={<CloudIcon color="primary" />}>
                            Terraform resources
                          </SectionTitle>

                          <Box
                            sx={{
                              display: "grid",
                              gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
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
                                <Typography variant="h5" sx={{ fontWeight: 800 }}>
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
                                  {resource.actions.map((action, index) => (
                                    <StatusChip
                                      key={`${action}-${index}`}
                                      status={action}
                                    />
                                  ))}
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

                    {approval.reviewer_comments && (
                      <Alert severity="info" sx={{ mt: 2 }}>
                        <strong>Reviewer comments:</strong>{" "}
                        {approval.reviewer_comments}
                      </Alert>
                    )}

                    {canDecide && (
                      <Box sx={{ mt: 2 }}>
                        <Typography
                          variant="h6"
                          sx={{ fontWeight: 700, mb: 1.5 }}
                        >
                          Review decision
                        </Typography>

                        <TextField
                          label="Reviewer comments (optional)"
                          value={comments[approval.id] ?? ""}
                          onChange={(event) =>
                            setComments((previous) => ({
                              ...previous,
                              [approval.id]: event.target.value,
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
                            disabled={workingId !== null}
                            onClick={() =>
                              void decide(approval.id, "approved")
                            }
                          >
                            {workingId === approval.id
                              ? "Processing..."
                              : "Approve"}
                          </Button>

                          <Button
                            variant="outlined"
                            color="error"
                            disabled={workingId !== null}
                            onClick={() =>
                              void decide(approval.id, "rejected")
                            }
                          >
                            Reject
                          </Button>
                        </Box>
                      </Box>
                    )}

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
                                {new Date(event.created_at).toLocaleString()}
                              </Typography>

                              {event.comments && (
                                <Typography variant="body2" sx={{ mt: 0.5 }}>
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
          Approval decisions are recorded in the audit trail. This interface
          does not execute Terraform or provision infrastructure. Security
          findings reflect the configured initial policy checks.
        </Alert>
      </CardContent>
    </Card>
  );
}
