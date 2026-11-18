
import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  Chip,
  CardContent,
  CircularProgress,
  Divider,
  Stack,
  Typography,
} from "@mui/material";
import AccountTreeIcon from "@mui/icons-material/AccountTree";
import {
  apiRequest,
  createApproval,
  createProvisionOperation,
  generateTrustedTerraformPlan,
  getApprovalStages,
  type ApprovalResponse,
  type ApprovalStageReviewResponse,
  type InfrastructureOperationResponse,
  type TrustedTerraformPlanResponse,
} from "./api";

type ApprovalPanelProps = {
  infrastructureRequestId: number | null;
};

export default function ApprovalPanel({
  infrastructureRequestId,
}: ApprovalPanelProps) {
  const [requestId, setRequestId] = useState(
    infrastructureRequestId === null
      ? ""
      : String(infrastructureRequestId),
  );

  const [plan, setPlan] = useState<TrustedTerraformPlanResponse | null>(null);
  const [approval, setApproval] = useState<ApprovalResponse | null>(null);
  const [stageReviews, setStageReviews] = useState<
    ApprovalStageReviewResponse[]
  >([]);
  const [operation, setOperation] =
    useState<InfrastructureOperationResponse | null>(null);

  const [loadingPlan, setLoadingPlan] = useState(false);
  const [loadingApproval, setLoadingApproval] = useState(false);
  const [refreshingApproval, setRefreshingApproval] = useState(false);
  const [applying, setApplying] = useState(false);

  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    setRequestId(
      infrastructureRequestId === null
        ? ""
        : String(infrastructureRequestId),
    );
    setPlan(null);
    setApproval(null);
    setStageReviews([]);
    setOperation(null);
    setError("");
    setNotice("");
  }, [infrastructureRequestId]);

  function validRequestId(): number | null {
    const id = Number(requestId);

    return requestId.trim() && Number.isSafeInteger(id) && id > 0
      ? id
      : null;
  }

  async function handleGeneratePlan() {
    setError("");
    setNotice("");
    setPlan(null);
    setApproval(null);
    setStageReviews([]);
    setOperation(null);

    const id = validRequestId();

    if (id === null) {
      setError("Enter a valid positive infrastructure request ID.");
      return;
    }

    setLoadingPlan(true);

    try {
      const result = await generateTrustedTerraformPlan(id);
      setPlan(result);

      if (result.status !== "succeeded") {
        setError(
          `Terraform plan generation failed${
            result.error_code ? ` (${result.error_code})` : ""
          }.`,
        );
      } else {
        setNotice(
          "Trusted Terraform plan generated. Review its summary before submitting.",
        );
      }
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not generate the Terraform plan.",
      );
    } finally {
      setLoadingPlan(false);
    }
  }

  async function handleSubmitApproval() {
    setError("");
    setNotice("");
    setApproval(null);
    setStageReviews([]);
    setOperation(null);

    const id = validRequestId();

    if (id === null) {
      setError("Enter a valid positive infrastructure request ID.");
      return;
    }

    if (
      !plan ||
      plan.status !== "succeeded" ||
      plan.infrastructure_request_id !== id
    ) {
      setError(
        "Generate a successful trusted plan for this request first.",
      );
      return;
    }

    setLoadingApproval(true);

    try {
      const result = await createApproval(id, plan.id);
      setApproval(result);

      try {
        const stages = await getApprovalStages(result.id);
        setStageReviews(stages);
      } catch {
        // The user can retrieve stage statuses using Refresh Approval Status.
      }

      setNotice("Approval request submitted successfully.");
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not submit the approval request.",
      );
    } finally {
      setLoadingApproval(false);
    }
  }

  async function handleRefreshApprovalStatus() {
    if (!approval) return;

    setError("");
    setNotice("");
    setRefreshingApproval(true);

    try {
      const [approvals, stages] = await Promise.all([
        apiRequest<ApprovalResponse[]>("/approvals"),
        getApprovalStages(approval.id),
      ]);

      const latestApproval = approvals.find(
        (item) => item.id === approval.id,
      );

      if (!latestApproval) {
        throw new Error("Approval request was not found.");
      }

      setApproval(latestApproval);
      setStageReviews(stages);

      setNotice(
        latestApproval.status === "approved"
          ? "Final approval is complete. Apply is available."
          : `Approval status refreshed: ${latestApproval.status}.`,
      );
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not refresh approval status.",
      );
    } finally {
      setRefreshingApproval(false);
    }
  }

  async function handleApply() {
    if (!approval || approval.status !== "approved") {
      setError("Final approval is required before applying.");
      return;
    }

    const requiredStages = ["technical", "cost", "security"];

    const allStagesApproved =
      stageReviews.length === requiredStages.length &&
      requiredStages.every((stage) =>
        stageReviews.some(
          (review) =>
            review.stage === stage && review.status === "approved",
        ),
      );

    if (!allStagesApproved) {
      setError(
        "Technical, cost and security reviews must all be approved.",
      );
      return;
    }

    if (operation) {
      setError(
        `Provision operation #${operation.id} has already been submitted.`,
      );
      return;
    }

    setError("");
    setNotice("");
    setApplying(true);

    try {
      const result = await createProvisionOperation(approval.id);

      setOperation(result);
      setNotice(
        `Provision operation #${result.id} has been submitted successfully.`,
      );
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not start the provision operation.",
      );
    } finally {
      setApplying(false);
    }
  }

  const allStagesApproved =
    stageReviews.length === 3 &&
    ["technical", "cost", "security"].every((stage) =>
      stageReviews.some(
        (review) =>
          review.stage === stage && review.status === "approved",
      ),
    );

  const canApply =
    approval?.status === "approved" &&
    allStagesApproved &&
    !operation &&
    !applying;

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
        <Stack
          direction="row"
          spacing={1.5}
          sx={{ mb: 1, alignItems: "center" }}
        >
          <Box
            sx={{
              width: 44,
              height: 44,
              borderRadius: 2,
              bgcolor: "primary.light",
              color: "primary.dark",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <AccountTreeIcon fontSize="medium" />
          </Box>

          <Box>
            <Typography variant="h5" sx={{ fontWeight: 750 }}>
              Workflow & Approvals
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Trusted plan generation and approval management
            </Typography>
          </Box>
        </Stack>

        <Typography color="text.secondary" sx={{ mb: 3, mt: 2 }}>
          Generate a trusted Terraform plan, submit it for approval,
          track the review stages, and apply only after final approval.
        </Typography>

        {error && (
          <Alert severity="error" sx={{ mb: 2, borderRadius: 2 }}>
            {error}
          </Alert>
        )}

        {notice && (
          <Alert severity="success" sx={{ mb: 2, borderRadius: 2 }}>
            {notice}
          </Alert>
        )}

        <Box
          sx={{
            p: { xs: 2, md: 2.5 },
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 2.5,
            bgcolor: "background.default",
          }}
        >
          <Typography sx={{ mb: 0.5, fontWeight: 700 }}>
            Infrastructure request
          </Typography>

          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Enter the ID of an existing infrastructure request.
          </Typography>

          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            Infrastructure Request ID
          </Typography>

          <Box
            component="input"
            type="number"
            min="1"
            step="1"
            value={requestId}
            onChange={(event) => {
              setRequestId(event.target.value);
              setPlan(null);
              setApproval(null);
              setStageReviews([]);
              setOperation(null);
              setError("");
              setNotice("");
            }}
            placeholder="Enter an existing request ID"
            sx={{
              boxSizing: "border-box",
              width: "100%",
              p: 1.75,
              borderRadius: 2,
              border: "1px solid",
              borderColor: "divider",
              bgcolor: "background.paper",
              color: "text.primary",
              fontSize: 15,
              outline: "none",
              "&:focus": {
                borderColor: "primary.main",
                boxShadow: "0 0 0 2px rgba(25, 118, 210, 0.12)",
              },
            }}
          />

          <Button
            variant="contained"
            onClick={() => void handleGeneratePlan()}
            disabled={
              loadingPlan ||
              loadingApproval ||
              refreshingApproval ||
              applying ||
              !requestId.trim()
            }
            sx={{
              mt: 2,
              borderRadius: 2,
              px: 2.5,
              py: 1.2,
              textTransform: "none",
              fontWeight: 700,
            }}
          >
            {loadingPlan ? (
              <>
                <CircularProgress
                  size={18}
                  color="inherit"
                  sx={{ mr: 1 }}
                />
                Generating plan...
              </>
            ) : (
              "Generate Trusted Terraform Plan"
            )}
          </Button>
        </Box>

        {plan && (
          <Card
            variant="outlined"
            sx={{ mt: 3, borderRadius: 2.5, overflow: "hidden" }}
          >
            <CardContent sx={{ p: { xs: 2, md: 2.5 } }}>
              <Stack
                direction={{ xs: "column", sm: "row" }}
                spacing={1}
                sx={{
                  mb: 2,
                  justifyContent: "space-between",
                  alignItems: {
                    xs: "flex-start",
                    sm: "center",
                  },
                }}
              >
                <Box>
                  <Typography variant="h6" sx={{ fontWeight: 750 }}>
                    Terraform Plan Review
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    Review the generated plan before requesting approval.
                  </Typography>
                </Box>

                <Chip
                  label={plan.status}
                  color={plan.status === "succeeded" ? "success" : "error"}
                  size="small"
                  sx={{ fontWeight: 700, textTransform: "capitalize" }}
                />
              </Stack>

              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: {
                    xs: "1fr",
                    sm: "repeat(2, minmax(0, 1fr))",
                  },
                  gap: 1.5,
                  mb: 2.5,
                }}
              >
                {[
                  { label: "Plan Record ID", value: plan.id },
                  {
                    label: "Infrastructure Request",
                    value: plan.infrastructure_request_id,
                  },
                  {
                    label: "Deployment Enabled",
                    value: plan.deployment_enabled ? "Yes" : "No",
                  },
                  { label: "Plan Status", value: plan.status },
                ].map((item) => (
                  <Box
                    key={item.label}
                    sx={{
                      p: 1.75,
                      borderRadius: 2,
                      bgcolor: "action.hover",
                    }}
                  >
                    <Typography variant="caption" color="text.secondary">
                      {item.label}
                    </Typography>
                    <Typography sx={{ mt: 0.5, fontWeight: 700 }}>
                      {item.value}
                    </Typography>
                  </Box>
                ))}
              </Box>

              <Typography sx={{ mb: 1, fontWeight: 700 }}>
                Stored plan summary
              </Typography>

              <Box
                component="pre"
                sx={{
                  p: 2,
                  m: 0,
                  borderRadius: 2,
                  bgcolor: "#101827",
                  color: "#d8e5f5",
                  overflowX: "auto",
                  whiteSpace: "pre-wrap",
                  overflowWrap: "anywhere",
                  fontSize: 12,
                  lineHeight: 1.7,
                  fontFamily: "monospace",
                }}
              >
                {JSON.stringify(plan.plan_summary, null, 2)}
              </Box>

              {plan.status === "succeeded" && (
                <Button
                  variant="contained"
                  onClick={() => void handleSubmitApproval()}
                  disabled={
                    loadingApproval ||
                    loadingPlan ||
                    Boolean(approval)
                  }
                  sx={{
                    mt: 2.5,
                    borderRadius: 2,
                    px: 2.5,
                    py: 1.2,
                    textTransform: "none",
                    fontWeight: 700,
                  }}
                >
                  {loadingApproval ? (
                    <>
                      <CircularProgress
                        size={18}
                        color="inherit"
                        sx={{ mr: 1 }}
                      />
                      Submitting...
                    </>
                  ) : approval ? (
                    "Approval Submitted"
                  ) : (
                    "Submit for Approval"
                  )}
                </Button>
              )}

              <Alert severity="info" sx={{ mt: 2, borderRadius: 2 }}>
                A generated plan is not an approval. Deployment requires
                completion of all approval stages and final approval.
              </Alert>
            </CardContent>
          </Card>
        )}

        {approval && (
          <Card
            variant="outlined"
            sx={{
              mt: 3,
              borderRadius: 2.5,
              borderColor: "success.light",
            }}
          >
            <CardContent sx={{ p: { xs: 2, md: 2.5 } }}>
              <Stack
                direction="row"
                spacing={1}
                sx={{ mb: 2, alignItems: "center" }}
              >
                <AccountTreeIcon color="success" />
                <Typography variant="h6" sx={{ fontWeight: 750 }}>
                  Approval Request
                </Typography>
              </Stack>

              <Chip
                label={approval.status}
                color={
                  approval.status === "approved"
                    ? "success"
                    : approval.status === "rejected"
                      ? "error"
                      : "warning"
                }
                size="small"
                sx={{
                  mb: 2,
                  fontWeight: 700,
                  textTransform: "capitalize",
                }}
              />

              <Stack spacing={1.5}>
                {[
                  { label: "Approval ID", value: approval.id },
                  {
                    label: "Infrastructure Request ID",
                    value: approval.infrastructure_request_id,
                  },
                  {
                    label: "Plan Record ID",
                    value: approval.terraform_plan_record_id,
                  },
                  { label: "Status", value: approval.status },
                ].map((item) => (
                  <Stack
                    key={item.label}
                    direction={{ xs: "column", sm: "row" }}
                    spacing={0.5}
                    sx={{
                      py: 1,
                      borderBottom: "1px solid",
                      borderColor: "divider",
                      justifyContent: "space-between",
                    }}
                  >
                    <Typography variant="body2" color="text.secondary">
                      {item.label}
                    </Typography>
                    <Typography variant="body2" sx={{ fontWeight: 700 }}>
                      {item.value}
                    </Typography>
                  </Stack>
                ))}
              </Stack>

              <Divider sx={{ my: 2 }} />

              <Typography variant="subtitle1" sx={{ mb: 1, fontWeight: 700 }}>
                Approval Stage Reviews
              </Typography>

              {stageReviews.length === 0 ? (
                <Alert severity="info" sx={{ mb: 2, borderRadius: 2 }}>
                  Stage review details are not available yet. Refresh the
                  approval status to retrieve them.
                </Alert>
              ) : (
                <Stack spacing={1.5}>
                  {(["technical", "cost", "security"] as const).map(
                    (stage) => {
                      const review = stageReviews.find(
                        (item) => item.stage === stage,
                      );

                      return (
                        <Stack
                          key={stage}
                          direction="row"
                          sx={{
                            p: 1.5,
                            borderRadius: 2,
                            bgcolor: "action.hover",
                            alignItems:"center",
                            justifyContent:"space-between",
                          }}
                        >
                          <Typography
                            variant="body2"
                            sx={{
                              fontWeight: 700,
                              textTransform: "capitalize",
                            }}
                          >
                            {stage}
                          </Typography>

                          <Chip
                            size="small"
                            label={review?.status ?? "not available"}
                            color={
                              review?.status === "approved"
                                ? "success"
                                : review?.status === "rejected"
                                  ? "error"
                                  : "warning"
                            }
                            sx={{ textTransform: "capitalize" }}
                          />
                        </Stack>
                      );
                    },
                  )}
                </Stack>
              )}

              <Button
                variant="outlined"
                onClick={() => void handleRefreshApprovalStatus()}
                disabled={refreshingApproval || applying}
                sx={{
                  mt: 2,
                  borderRadius: 2,
                  textTransform: "none",
                  fontWeight: 700,
                }}
              >
                {refreshingApproval ? (
                  <>
                    <CircularProgress
                      size={17}
                      sx={{ mr: 1 }}
                    />
                    Refreshing...
                  </>
                ) : (
                  "Refresh Approval Status"
                )}
              </Button>

              {approval.status !== "approved" && (
                <Alert severity="info" sx={{ mt: 2, borderRadius: 2 }}>
                  The request is awaiting completion of its review stages
                  and final approval by an authorized reviewer.
                </Alert>
              )}

              {approval.status === "approved" && !allStagesApproved && (
                <Alert severity="warning" sx={{ mt: 2, borderRadius: 2 }}>
                  The overall approval is marked approved, but all three
                  stage reviews are not confirmed as approved. Refresh
                  the status before applying.
                </Alert>
              )}

              {approval.status === "rejected" && (
                <Alert severity="error" sx={{ mt: 2, borderRadius: 2 }}>
                  This approval request was rejected. A new approved plan
                  and approval request are required before provisioning.
                </Alert>
              )}

              <Button
                variant="contained"
                color="success"
                onClick={() => void handleApply()}
                disabled={!canApply}
                sx={{
                  mt: 2,
                  borderRadius: 2,
                  px: 3,
                  py: 1.2,
                  textTransform: "none",
                  fontWeight: 700,
                }}
              >
                {applying ? (
                  <>
                    <CircularProgress
                      size={18}
                      color="inherit"
                      sx={{ mr: 1 }}
                    />
                    Starting Apply...
                  </>
                ) : operation ? (
                  "Apply Submitted"
                ) : (
                  "Apply Infrastructure"
                )}
              </Button>

              {operation && (
                <Card
                  variant="outlined"
                  sx={{ mt: 2, borderRadius: 2 }}
                >
                  <CardContent>
                    <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
                      Provision Operation
                    </Typography>

                    <Stack spacing={1} sx={{ mt: 1 }}>
                      <Typography variant="body2">
                        Operation ID: <strong>{operation.id}</strong>
                      </Typography>
                      <Typography variant="body2">
                        Status: <strong>{operation.status}</strong>
                      </Typography>
                      <Typography variant="body2">
                        Approval ID:{" "}
                        <strong>{operation.approval_request_id}</strong>
                      </Typography>
                    </Stack>

                    <Alert severity="info" sx={{ mt: 2 }}>
                      The provision operation has been queued for execution.
                      Check the Infrastructure Operations panel for progress
                      and its final result.
                    </Alert>
                  </CardContent>
                </Card>
              )}
            </CardContent>
          </Card>
        )}

      </CardContent>
    </Card>
  );
}