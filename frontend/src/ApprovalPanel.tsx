
import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import AccountTreeIcon from "@mui/icons-material/AccountTree";
import {
  createApproval,
  generateTrustedTerraformPlan,
  type ApprovalResponse,
  type TrustedTerraformPlanResponse,
} from "./api";

export default function ApprovalPanel() {
  const [requestId, setRequestId] = useState("");
  const [plan, setPlan] = useState<TrustedTerraformPlanResponse | null>(null);
  const [approval, setApproval] = useState<ApprovalResponse | null>(null);
  const [loadingPlan, setLoadingPlan] = useState(false);
  const [loadingApproval, setLoadingApproval] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

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
        setNotice("Trusted Terraform plan generated. Review its summary before submitting.");
      }
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not generate the Terraform plan.",
      );
    } finally {
      setLoadingPlan(false);
    }
  }

  async function handleSubmitApproval() {
    setError("");
    setNotice("");
    setApproval(null);

    const id = validRequestId();
    if (id === null) {
      setError("Enter a valid positive infrastructure request ID.");
      return;
    }

    if (!plan || plan.status !== "succeeded" || plan.infrastructure_request_id !== id) {
      setError("Generate a successful trusted plan for this request first.");
      return;
    }

    setLoadingApproval(true);
    try {
      const result = await createApproval(id, plan.id);
      setApproval(result);
      setNotice("Approval request submitted successfully.");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not submit the approval request.",
      );
    } finally {
      setLoadingApproval(false);
    }
  }

  return (
    <Card sx={{ borderRadius: 3 }}>
      <CardContent sx={{ p: { xs: 2, md: 3 } }}>
        <Stack direction="row" spacing={1.5} sx={{ alignItems: "center", mb: 1 }}>
          <AccountTreeIcon color="primary" fontSize="large" />
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            Workflow & Approvals
          </Typography>
        </Stack>

        <Typography color="text.secondary" sx={{ mb: 3 }}>
          Generate a trusted Terraform plan for a completed infrastructure
          request, review its summary, and submit it for approval.
          Submitting does not provision resources.
        </Typography>

        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        {notice && <Alert severity="success" sx={{ mb: 2 }}>{notice}</Alert>}

        <Stack spacing={2}>
          <Box>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              Infrastructure Request ID
            </Typography>
            <input
              type="number"
              min="1"
              step="1"
              value={requestId}
              onChange={(event) => {
                setRequestId(event.target.value);
                setPlan(null);
                setApproval(null);
                setError("");
                setNotice("");
              }}
              placeholder="Enter an existing request ID"
              style={{
                boxSizing: "border-box",
                width: "100%",
                padding: "14px",
                borderRadius: "8px",
                border: "1px solid #999",
                fontSize: "16px",
              }}
            />
          </Box>

          <Box>
            <Button
              variant="contained"
              onClick={() => void handleGeneratePlan()}
              disabled={loadingPlan || loadingApproval || !requestId.trim()}
            >
              {loadingPlan ? (
                <>
                  <CircularProgress size={18} color="inherit" sx={{ mr: 1 }} />
                  Generating plan...
                </>
              ) : (
                "Generate Trusted Terraform Plan"
              )}
            </Button>
          </Box>
        </Stack>

        {plan && (
          <Card variant="outlined" sx={{ mt: 3, borderRadius: 2 }}>
            <CardContent>
              <Typography variant="h6" sx={{ fontWeight: 700, mb: 1 }}>
                Terraform Plan Review
              </Typography>

              <Typography>Plan Record ID: {plan.id}</Typography>
              <Typography>Request ID: {plan.infrastructure_request_id}</Typography>
              <Typography>Status: {plan.status}</Typography>
              <Typography>
                Deployment enabled: {plan.deployment_enabled ? "Yes" : "No"}
              </Typography>

              <Typography sx={{ fontWeight: 600, mt: 2, mb: 1 }}>
                Stored plan summary
              </Typography>

              <Box
                component="pre"
                sx={{
                  p: 1.5,
                  borderRadius: 1,
                  bgcolor: "action.hover",
                  overflowX: "auto",
                  whiteSpace: "pre-wrap",
                  overflowWrap: "anywhere",
                  fontSize: 12,
                }}
              >
                {JSON.stringify(plan.plan_summary, null, 2)}
              </Box>

              {plan.status === "succeeded" && (
                <Button
                  variant="contained"
                  onClick={() => void handleSubmitApproval()}
                  disabled={loadingApproval || loadingPlan}
                  sx={{ mt: 2 }}
                >
                  {loadingApproval ? (
                    <>
                      <CircularProgress size={18} color="inherit" sx={{ mr: 1 }} />
                      Submitting...
                    </>
                  ) : (
                    "Submit for Approval"
                  )}
                </Button>
              )}

              <Alert severity="info" sx={{ mt: 2 }}>
                A generated plan is not an approval. Terraform deployment remains disabled.
              </Alert>
            </CardContent>
          </Card>
        )}

        {approval && (
          <Card variant="outlined" sx={{ mt: 3, borderRadius: 2 }}>
            <CardContent>
              <Typography variant="h6" sx={{ fontWeight: 700, mb: 2 }}>
                Approval Request Created
              </Typography>
              <Typography>Approval ID: {approval.id}</Typography>
              <Typography>
                Infrastructure Request ID: {approval.infrastructure_request_id}
              </Typography>
              <Typography>Plan Record ID: {approval.terraform_plan_record_id}</Typography>
              <Typography>Status: {approval.status}</Typography>
              <Alert severity="info" sx={{ mt: 2 }}>
                The request is awaiting the configured approval workflow. No deployment was triggered.
              </Alert>
            </CardContent>
          </Card>
        )}
      </CardContent>
    </Card>
  );
}
