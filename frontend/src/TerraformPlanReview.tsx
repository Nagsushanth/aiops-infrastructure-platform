
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
    <Card
      elevation={0}
      sx={{
        border: "1px solid #e3eaf3",
        borderRadius: 3,
        overflow: "hidden",
        bgcolor: "#ffffff",
      }}
    >
      <CardContent sx={{ p: { xs: 2, md: 3 } }}>
        {/* Header */}
        <Stack
          direction="row"
          spacing={1.5}
          sx={{ alignItems: "center", mb: 2 }}
        >
          <Box
            sx={{
              width: 46,
              height: 46,
              borderRadius: 2.5,
              bgcolor: "#edf3ff",
              color: "#2864dc",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <FactCheckIcon fontSize="large" />
          </Box>

          <Box>
            <Typography
              component="h2"
              variant="h5"
              sx={{ fontWeight: 800, color: "#24334d" }}
            >
              Terraform Plan Review
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Inspect infrastructure changes from Terraform plan JSON.
            </Typography>
          </Box>
        </Stack>

        <Alert severity="warning" sx={{ mb: 2.5, borderRadius: 2 }}>
          Review only. This endpoint parses supplied JSON; it does not run
          Terraform, verify the plan against live AWS state, approve changes,
          or deploy resources. Avoid pasting credentials or other secrets.
        </Alert>

        {/* JSON input */}
        <Box
          sx={{
            p: { xs: 1.5, md: 2 },
            bgcolor: "#f8fafd",
            border: "1px solid #e8edf4",
            borderRadius: 2.5,
          }}
        >
          <Typography
            variant="subtitle1"
            sx={{ fontWeight: 800, color: "#24334d", mb: 1.5 }}
          >
            Terraform Plan JSON
          </Typography>

          <TextField
            label="Paste Terraform plan JSON"
            placeholder="Paste the complete output of terraform show -json"
            value={planJson}
            onChange={(event) => setPlanJson(event.target.value)}
            multiline
            minRows={10}
            maxRows={18}
            fullWidth
            sx={{
              mb: 2,
              "& .MuiOutlinedInput-root": {
                bgcolor: "#ffffff",
                borderRadius: 2,
                alignItems: "flex-start",
              },
              "& textarea": {
                fontFamily: "monospace",
                fontSize: 13,
                lineHeight: 1.6,
              },
            }}
          />

          <Stack
            direction={{ xs: "column", sm: "row" }}
            spacing={1.2}
            useFlexGap
            sx={{ flexWrap: "wrap" }}
          >
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
              sx={{
                borderRadius: 2,
                textTransform: "none",
                fontWeight: 800,
                boxShadow: "none",
                bgcolor: "#2864dc",
                "&:hover": { bgcolor: "#1e50b5", boxShadow: "none" },
              }}
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
              sx={{ borderRadius: 2, textTransform: "none", fontWeight: 700 }}
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
              sx={{ borderRadius: 2, textTransform: "none" }}
            >
              Clear
            </Button>
          </Stack>
        </Box>

        {error && (
          <Alert severity="error" sx={{ mt: 2, borderRadius: 2 }}>
            {error}
          </Alert>
        )}

        {/* Review results */}
        {review && (
          <Box sx={{ mt: 3 }}>
            <Divider sx={{ mb: 2.5 }} />

            <Typography
              component="h3"
              variant="h6"
              sx={{ fontWeight: 800, color: "#24334d", mb: 2 }}
            >
              Plan Summary
            </Typography>

            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: {
                  xs: "repeat(2, minmax(0, 1fr))",
                  sm: "repeat(3, minmax(0, 1fr))",
                  lg: "repeat(4, minmax(0, 1fr))",
                },
                gap: 1.5,
                mb: 2,
              }}
            >
              {[
                { label: "Create", value: review.resource_summary.create, color: "#16804a" },
                { label: "Update", value: review.resource_summary.update, color: "#2864dc" },
                { label: "Delete", value: review.resource_summary.delete, color: "#d14343" },
                { label: "Replace", value: review.resource_summary.replace, color: "#b7791f" },
                { label: "Read", value: review.resource_summary.read, color: "#64748b" },
                { label: "No-op", value: review.resource_summary.no_op, color: "#64748b" },
                { label: "Other", value: review.resource_summary.other, color: "#64748b" },
                { label: "Total changes", value: review.resource_summary.total_resource_changes, color: "#24334d" },
              ].map((item) => (
                <Box
                  key={item.label}
                  sx={{
                    p: 2,
                    bgcolor: "#f8fafd",
                    border: "1px solid #e8edf4",
                    borderRadius: 2,
                  }}
                >
                  <Typography variant="caption" color="text.secondary">
                    {item.label}
                  </Typography>
                  <Typography
                    variant="h5"
                    sx={{ fontWeight: 800, color: item.color, mt: 0.5 }}
                  >
                    {item.value}
                  </Typography>
                </Box>
              ))}
            </Box>

            <Stack
              direction="row"
              spacing={1}
              useFlexGap
              sx={{ flexWrap: "wrap", mb: 2 }}
            >
              <Chip
                label={`Terraform: ${review.terraform_version ?? "Unknown"}`}
                variant="outlined"
              />
              <Chip
                label={`Format: ${review.format_version ?? "Unknown"}`}
                variant="outlined"
              />
            </Stack>

            <Alert severity="success" sx={{ mb: 2, borderRadius: 2 }}>
              JSON parsed successfully. Deployment remains disabled.
            </Alert>

            <Typography
              component="h3"
              variant="h6"
              sx={{ fontWeight: 800, color: "#24334d", mb: 1.5 }}
            >
              Resource Changes
            </Typography>

            {review.resources.length === 0 ? (
              <Alert severity="info" sx={{ borderRadius: 2 }}>
                No resource changes were found in this plan JSON.
              </Alert>
            ) : (
              <Stack spacing={2}>
                {review.resources.map((resource, index) => (
                  <Card
                    key={`${resource.address ?? "resource"}-${index}`}
                    variant="outlined"
                    sx={{
                      borderRadius: 2.5,
                      borderColor: "#e3eaf3",
                      overflow: "hidden",
                    }}
                  >
                    <CardContent sx={{ p: 2.5 }}>
                      <Typography
                        sx={{
                          fontWeight: 800,
                          color: "#24334d",
                          overflowWrap: "anywhere",
                        }}
                      >
                        {resource.address ?? "Unknown resource address"}
                      </Typography>

                      <Stack
                        direction="row"
                        spacing={1}
                        useFlexGap
                        sx={{ flexWrap: "wrap", my: 1.5 }}
                      >
                        <Chip
                          size="small"
                          label={`Type: ${resource.type ?? "Unknown"}`}
                          variant="outlined"
                        />
                        <Chip
                          size="small"
                          label={`Action: ${resource.actions.join(", ") || "Unknown"}`}
                          color="primary"
                        />
                        <Chip
                          size="small"
                          label={`Mode: ${resource.mode}`}
                          variant="outlined"
                        />
                      </Stack>

                      {resource.action_reason && (
                        <Alert severity="info" sx={{ mb: 2, borderRadius: 2 }}>
                          Reason: {resource.action_reason}
                        </Alert>
                      )}

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
                        <Box sx={{ minWidth: 0 }}>
                          <Typography
                            variant="subtitle2"
                            sx={{ fontWeight: 800, mb: 1, color: "#475569" }}
                          >
                            Before
                          </Typography>
                          <Box
                            component="pre"
                            sx={{
                              m: 0,
                              p: 1.5,
                              bgcolor: "#f8fafd",
                              border: "1px solid #e8edf4",
                              borderRadius: 2,
                              whiteSpace: "pre-wrap",
                              overflowWrap: "anywhere",
                              fontSize: 12,
                              lineHeight: 1.6,
                              maxHeight: 240,
                              overflow: "auto",
                              color: "#475569",
                            }}
                          >
                            {formatValue(resource.before)}
                          </Box>
                        </Box>

                        <Box sx={{ minWidth: 0 }}>
                          <Typography
                            variant="subtitle2"
                            sx={{ fontWeight: 800, mb: 1, color: "#475569" }}
                          >
                            After
                          </Typography>
                          <Box
                            component="pre"
                            sx={{
                              m: 0,
                              p: 1.5,
                              bgcolor: "#f0f6ff",
                              border: "1px solid #dce8fb",
                              borderRadius: 2,
                              whiteSpace: "pre-wrap",
                              overflowWrap: "anywhere",
                              fontSize: 12,
                              lineHeight: 1.6,
                              maxHeight: 240,
                              overflow: "auto",
                              color: "#31527e",
                            }}
                          >
                            {formatValue(resource.after)}
                          </Box>
                        </Box>
                      </Box>
                    </CardContent>
                  </Card>
                ))}
              </Stack>
            )}

            <Alert severity="warning" sx={{ mt: 2, borderRadius: 2 }}>
              This report reflects the JSON submitted for parsing. Confirm its
              source and review the plan before any future approval or execution.
            </Alert>
          </Box>
        )}
      </CardContent>
    </Card>
  );
}
