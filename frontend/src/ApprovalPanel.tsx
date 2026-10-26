
import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import AccountTreeIcon from "@mui/icons-material/AccountTree";
import { createApproval, type ApprovalResponse } from "./api";

export default function ApprovalPanel() {
  const [requestId, setRequestId] = useState("");
  const [planJson, setPlanJson] = useState("");
  const [approval, setApproval] = useState<ApprovalResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  async function handleSubmit() {
    setError("");
    setSuccess("");
    setApproval(null);

    const parsedRequestId = Number(requestId);

    if (
      !requestId.trim() ||
      !Number.isSafeInteger(parsedRequestId) ||
      parsedRequestId <= 0
    ) {
      setError("Enter a valid positive infrastructure request ID.");
      return;
    }

    let plan: Record<string, unknown>;

    try {
      const parsed: unknown = JSON.parse(planJson);

      if (
        typeof parsed !== "object" ||
        parsed === null ||
        Array.isArray(parsed)
      ) {
        throw new Error("The plan must be a JSON object.");
      }

      plan = parsed as Record<string, unknown>;
    } catch {
      setError("Enter valid Terraform plan JSON before submitting.");
      return;
    }

    if (
      plan.errored !== false ||
      plan.complete !== true ||
      plan.applyable !== true
    ) {
      setError(
        "The plan must have errored=false, complete=true, and applyable=true. Use a reviewed Terraform plan JSON file.",
      );
      return;
    }

    setLoading(true);

    try {
      const result = await createApproval(parsedRequestId, plan);
      setApproval(result);
      setSuccess("Approval request submitted successfully.");
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not submit the approval request.",
      );
    } finally {
      setLoading(false);
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
          Submit a completed infrastructure request with its reviewed
          Terraform plan for approval. Submitting does not provision resources.
        </Typography>

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {success && (
          <Alert severity="success" sx={{ mb: 2 }}>
            {success}
          </Alert>
        )}

        <Stack spacing={2}>
          <TextField
            label="Infrastructure Request ID"
            type="number"
            value={requestId}
            onChange={(event) => setRequestId(event.target.value)}
            placeholder="For example, 9"
            fullWidth
            required
            slotProps={{ htmlInput: { min: 1, step: 1 } }}
          />

          <TextField
            label="Reviewed Terraform Plan JSON"
            value={planJson}
            onChange={(event) => setPlanJson(event.target.value)}
            placeholder="Paste the contents of a real terraform show -json plan file"
            multiline
            minRows={8}
            maxRows={18}
            fullWidth
            required
            helperText="Use the JSON generated from a Terraform plan. Do not paste credentials, tokens, or other secrets."
          />

          <Box>
            <Button
              variant="contained"
              onClick={handleSubmit}
              disabled={loading || !requestId.trim() || !planJson.trim()}
            >
              {loading ? (
                <>
                  <CircularProgress size={18} color="inherit" sx={{ mr: 1 }} />
                  Submitting...
                </>
              ) : (
                "Submit for Approval"
              )}
            </Button>
          </Box>
        </Stack>

        {approval && (
          <Card variant="outlined" sx={{ mt: 3, borderRadius: 2 }}>
            <CardContent>
              <Typography variant="h6" sx={{ fontWeight: 700, mb: 2 }}>
                Approval Request Created
              </Typography>

              <Typography>
                Approval ID: {approval.id}
              </Typography>

              <Typography>
                Infrastructure Request ID: {approval.infrastructure_request_id}
              </Typography>

              <Typography>
                Status: {approval.status}
              </Typography>

              <Alert severity="info" sx={{ mt: 2 }}>
                This request is awaiting the configured approval workflow.
                Terraform deployment has not been triggered.
              </Alert>
            </CardContent>
          </Card>
        )}
      </CardContent>
    </Card>
  );
}
