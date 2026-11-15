import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  Chip,
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

  useEffect(() => {
    setRequestId(
      infrastructureRequestId === null
        ? ""
        : String(infrastructureRequestId),
    );
    setPlan(null);
    setApproval(null);
    setError("");
    setNotice("");
  }, [infrastructureRequestId]);
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
          sx={{ mb: 1, alignItems:"center"}}
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
            <Typography variant="h5" sx={{ fontWeight:750 }}>
              Workflow & Approvals
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Trusted plan generation and approval management
            </Typography>
          </Box>
        </Stack>

        <Typography color="text.secondary" sx={{ mb: 3, mt: 2 }}>
          Generate a trusted Terraform plan for a completed infrastructure
          request, review its summary, and submit it for approval.
          Submitting does not provision resources.
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
          <Typography  sx={{ mb: 0.5, fontWeight:700 }}>
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
            disabled={loadingPlan || loadingApproval || !requestId.trim()}
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
                <CircularProgress size={18} color="inherit" sx={{ mr: 1 }} />
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
                sx={{ mb: 2, justifyContent:"space-between", alignItems:{ xs: "flex-start", sm: "center" } }}
              >
                <Box>
                  <Typography variant="h6" sx={{fontWeight:750}}>
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
                    <Typography  sx={{ mt: 0.5, fontWeight:700 }}>
                      {item.value}
                    </Typography>
                  </Box>
                ))}
              </Box>

              <Typography  sx={{ mb: 1, fontWeight:700 }}>
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
                  disabled={loadingApproval || loadingPlan}
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
                  ) : (
                    "Submit for Approval"
                  )}
                </Button>
              )}

              <Alert severity="info" sx={{ mt: 2, borderRadius: 2 }}>
                A generated plan is not an approval. Terraform deployment
                remains disabled.
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
              <Stack direction="row" spacing={1}  sx={{ mb: 2, alignItems:"center" }}>
                <AccountTreeIcon color="success" />
                <Typography variant="h6" sx={{fontWeight:750}}>
                  Approval Request Created
                </Typography>
              </Stack>

              <Chip
                label={approval.status}
                color="success"
                size="small"
                sx={{ mb: 2, fontWeight: 700, textTransform: "capitalize" }}
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
                      justifyContent:"space-between"
                    }}
                  >
                    <Typography variant="body2" color="text.secondary">
                      {item.label}
                    </Typography>
                    <Typography variant="body2" sx={{fontWeight:700}}>
                      {item.value}
                    </Typography>
                  </Stack>
                ))}
              </Stack>

              <Alert severity="info" sx={{ mt: 2, borderRadius: 2 }}>
                The request is awaiting the configured approval workflow.
                No deployment was triggered.
              </Alert>
            </CardContent>
          </Card>
        )}
      </CardContent>
    </Card>
  );
}
