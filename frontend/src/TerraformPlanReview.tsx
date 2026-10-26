
import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import FactCheckIcon from "@mui/icons-material/FactCheck";
import {
  parseTerraformPlan,
  type TerraformPlanReviewResponse,
} from "./api";

const EMPTY_PLAN_EXAMPLE = `{
  "terraform_version": "1.15.6",
  "format_version": "1.2",
  "resource_changes": [
    {
      "address": "aws_instance.this",
      "mode": "managed",
      "type": "aws_instance",
      "name": "this",
      "change": {
        "actions": ["create"],
        "before": null,
        "after": {
          "instance_type": "t3.small"
        },
        "after_unknown": {}
      }
    }
  ]
}`;

function formatValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "Not available";
  }

  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export default function TerraformPlanReview() {
  const [planJson, setPlanJson] = useState("");
  const [review, setReview] =
    useState<TerraformPlanReviewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function reviewPlan() {
    setError("");
    setReview(null);

    let parsed: unknown;

    try {
      parsed = JSON.parse(planJson);
    } catch {
      setError("Invalid JSON. Paste the complete output of terraform show -json.");
      return;
    }

    if (
      parsed === null ||
      typeof parsed !== "object" ||
      Array.isArray(parsed)
    ) {
      setError("The plan must be a JSON object.");
      return;
    }

    setLoading(true);

    try {
      const result = await parseTerraformPlan(
        parsed as Record<string, unknown>,
      );
      setReview(result);
    } catch (err: unknown) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to parse the Terraform plan.",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card sx={{ borderRadius: 3, mb: 3 }}>
      <CardContent sx={{ p: { xs: 2, md: 3 } }}>
        <Stack direction="row" spacing={1.5} sx={{ alignItems: "center", mb: 1 }}>
          <FactCheckIcon color="primary" fontSize="large" />
          <Box>
            <Typography component="h2" variant="h5" sx={{ fontWeight: 800 }}>
              Terraform Plan Review
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Inspect resource changes from Terraform plan JSON.
            </Typography>
          </Box>
        </Stack>

        <Alert severity="warning" sx={{ my: 2 }}>
          Review only. This endpoint parses supplied JSON; it does not run
          Terraform, verify the plan against live AWS state, approve changes,
          or deploy resources. Avoid pasting credentials or other secrets.
        </Alert>

        <TextField
          label="Terraform plan JSON"
          placeholder="Paste the complete output of terraform show -json"
          value={planJson}
          onChange={(event) => setPlanJson(event.target.value)}
          multiline
          minRows={8}
          maxRows={18}
          fullWidth
          sx={{
            mb: 2,
            "& textarea": {
              fontFamily: "monospace",
              fontSize: 13,
            },
          }}
        />

        <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap"}}>
          <Button
            variant="contained"
            onClick={reviewPlan}
            disabled={loading || !planJson.trim()}
            startIcon={
              loading ? (
                <CircularProgress size={18} color="inherit" />
              ) : (
                <FactCheckIcon />
              )
            }
          >
            {loading ? "Reviewing plan..." : "Review Terraform Plan"}
          </Button>

          <Button
            variant="outlined"
            onClick={() => {
              setPlanJson(EMPTY_PLAN_EXAMPLE);
              setReview(null);
              setError("");
            }}
            disabled={loading}
          >
            Load Example
          </Button>

          <Button
            onClick={() => {
              setPlanJson("");
              setReview(null);
              setError("");
            }}
            disabled={loading}
          >
            Clear
          </Button>
        </Stack>

        {error && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {error}
          </Alert>
        )}

        {review && (
          <Box sx={{ mt: 3 }}>
            <Divider sx={{ mb: 2 }} />

            <Typography component="h3" variant="h6" sx={{ fontWeight: 700, mb: 1.5 }}>
              Plan Summary
            </Typography>

            <Stack
              direction="row"
              spacing={1}
              useFlexGap
              sx={{ flexWrap: "wrap", mb: 2 }}
            >
              <Chip
                label={`Create: ${review.resource_summary.create}`}
                color="success"
              />
              <Chip
                label={`Update: ${review.resource_summary.update}`}
                color="info"
              />
              <Chip
                label={`Delete: ${review.resource_summary.delete}`}
                color="error"
              />
              <Chip
                label={`Replace: ${review.resource_summary.replace}`}
                color="warning"
              />
              <Chip label={`Read: ${review.resource_summary.read}`} />
              <Chip label={`No-op: ${review.resource_summary.no_op}`} />
              <Chip label={`Other: ${review.resource_summary.other}`} />
              <Chip
                label={`Total changes: ${review.resource_summary.total_resource_changes}`}
              />
            </Stack>

            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Terraform version: {review.terraform_version ?? "Unknown"} ·
              Plan format: {review.format_version ?? "Unknown"}
            </Typography>

            <Alert severity="success" sx={{ mb: 2 }}>
              JSON parsed successfully. Deployment remains disabled.
            </Alert>

            {review.resources.length === 0 ? (
              <Alert severity="info">
                No resource changes were found in this plan JSON.
              </Alert>
            ) : (
              <Stack spacing={1.5}>
                {review.resources.map((resource, index) => (
                  <Card
                    key={`${resource.address ?? "resource"}-${index}`}
                    variant="outlined"
                    sx={{ borderRadius: 2 }}
                  >
                    <CardContent>
                      <Typography sx={{ fontWeight: 700, overflowWrap: "anywhere" }}>
                        {resource.address ?? "Unknown resource address"}
                      </Typography>

                      <Stack
                        direction="row"
                        spacing={1}
                        useFlexGap
                       sx={{ flexWrap: "wrap", my: 1 }}
                      >
                        <Chip
                          size="small"
                          label={`Type: ${resource.type ?? "Unknown"}`}
                        />
                        <Chip
                          size="small"
                          label={`Action: ${resource.actions.join(", ") || "Unknown"}`}
                        />
                        <Chip
                          size="small"
                          label={`Mode: ${resource.mode}`}
                        />
                      </Stack>

                      {resource.action_reason && (
                        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                          Reason: {resource.action_reason}
                        </Typography>
                      )}

                      <Typography variant="subtitle2" sx={{ mt: 2, mb: 0.5 }}>
                        Before
                      </Typography>
                      <Box
                        component="pre"
                        sx={{
                          p: 1.5,
                          bgcolor: "action.hover",
                          borderRadius: 1,
                          whiteSpace: "pre-wrap",
                          overflowWrap: "anywhere",
                          fontSize: 12,
                          maxHeight: 220,
                          overflow: "auto",
                        }}
                      >
                        {formatValue(resource.before)}
                      </Box>

                      <Typography variant="subtitle2" sx={{ mt: 2, mb: 0.5 }}>
                        After
                      </Typography>
                      <Box
                        component="pre"
                        sx={{
                          p: 1.5,
                          bgcolor: "action.hover",
                          borderRadius: 1,
                          whiteSpace: "pre-wrap",
                          overflowWrap: "anywhere",
                          fontSize: 12,
                          maxHeight: 220,
                          overflow: "auto",
                        }}
                      >
                        {formatValue(resource.after)}
                      </Box>
                    </CardContent>
                  </Card>
                ))}
              </Stack>
            )}

            <Alert severity="warning" sx={{ mt: 2 }}>
              This report reflects the JSON submitted for parsing. Confirm its
              source and review the plan before any future approval or execution.
            </Alert>
          </Box>
        )}
      </CardContent>
    </Card>
  );
}
