import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import CloudQueueIcon from "@mui/icons-material/CloudQueue";
import {
  createTerraformPreview,
  type TerraformPreviewResponse,
} from "./api";
import type { InfrastructureSpec } from "./ChatPanel";

type Environment = "development" | "testing" | "staging" | "production";

const ENVIRONMENTS: Environment[] = [
  "development",
  "testing",
  "staging",
  "production",
];

const CPU_OPTIONS = [2, 4, 8];
const MEMORY_OPTIONS = [2, 4, 8, 16, 32];

type TerraformPreviewProps = {
  specification: InfrastructureSpec | null;
};

export default function TerraformPreview({
  specification,
}: TerraformPreviewProps) {
  const [region, setRegion] = useState("ap-south-1");
  const [environment, setEnvironment] =
    useState<Environment>("development");
  const [vcpus, setVcpus] = useState(4);
  const [memoryGb, setMemoryGb] = useState(8);
  const [publicAccess, setPublicAccess] = useState(false);
  const [preview, setPreview] =
    useState<TerraformPreviewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!specification) {
      setPreview(null);
      return;
    }

    setRegion(specification.region);
    setEnvironment(specification.environment);
    setVcpus(specification.vcpus);
    setMemoryGb(specification.memory_gb);
    setPublicAccess(specification.public_access);
    setPreview(null);
    setError("");
  }, [specification]);

  async function generatePreview() {
    setLoading(true);
    setError("");
    setPreview(null);

    try {
      if (!specification) {
        throw new Error(
          "Complete your infrastructure requirements in the chat first.",
        );
      }

      if (specification.cloud_provider !== "aws") {
        throw new Error(
          "The current Terraform preview generator supports AWS only.",
        );
      }

      if (specification.resource_type.trim().toLowerCase() !== "ec2") {
        throw new Error(
          "The current Terraform preview generator supports EC2 instances only.",
        );
      }

      if (region.trim() !== "ap-south-1") {
        throw new Error(
          "The current Terraform preview generator supports the Mumbai region (ap-south-1) only.",
        );
      }

      const supportedOperatingSystems = [
        "linux",
        "amazon linux",
        "amazon linux 2023",
      ];

      if (
        !supportedOperatingSystems.includes(
          specification.operating_system.trim().toLowerCase(),
        )
      ) {
        throw new Error(
          `The requested operating system "${specification.operating_system}" is not supported by the current generator. Amazon Linux 2023 is supported; update the request in chat rather than silently substituting an OS.`,
        );
      }

      const result = await createTerraformPreview({
        cloud_provider: specification.cloud_provider,
        resource_type: specification.resource_type,
        environment,
        region: region.trim(),
        vcpus,
        memory_gb: memoryGb,
        public_access: publicAccess,
        operating_system: specification.operating_system,
      });

      setPreview(result);
    } catch (err: unknown) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to generate the infrastructure preview.",
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
          sx={{ mb: 1, alignItems: "center" }}
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
            <CloudQueueIcon fontSize="large" />
          </Box>

          <Box>
            <Typography
              component="h2"
              variant="h5"
              sx={{ fontWeight: 800, color: "#24334d" }}
            >
              Terraform Infrastructure Preview
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Configure your AWS EC2 infrastructure requirements.
            </Typography>
          </Box>
        </Stack>

        <Alert severity="info" sx={{ my: 2, borderRadius: 2 }}>
          Local preview only. This request does not execute Terraform plan or
          create AWS resources.
        </Alert>

        {/* Configuration */}
        <Box
          sx={{
            bgcolor: "#f8fafd",
            border: "1px solid #edf0f5",
            borderRadius: 2.5,
            p: { xs: 2, md: 2.5 },
            mb: 2.5,
          }}
        >
          <Typography
            variant="subtitle1"
            sx={{ fontWeight: 800, color: "#24334d", mb: 2 }}
          >
            Infrastructure Configuration
          </Typography>

          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: {
                xs: "1fr",
                sm: "repeat(2, minmax(0, 1fr))",
              },
              gap: 2,
            }}
          >
            <TextField
              label="Cloud provider"
              value="AWS"
              disabled
              fullWidth
            />

            <TextField
              select
              label="Environment"
              value={environment}
              onChange={(event) =>
                setEnvironment(event.target.value as Environment)
              }
              fullWidth
            >
              {ENVIRONMENTS.map((item) => (
                <MenuItem key={item} value={item}>
                  {item.charAt(0).toUpperCase() + item.slice(1)}
                </MenuItem>
              ))}
            </TextField>

            <TextField
              label="AWS region"
              value={region}
              onChange={(event) => setRegion(event.target.value)}
              placeholder="ap-south-1"
              required
              fullWidth
              helperText="Supported region: ap-south-1 (Mumbai)"
            />

            <TextField
              select
              label="vCPUs"
              value={vcpus}
              onChange={(event) => setVcpus(Number(event.target.value))}
              fullWidth
            >
              {CPU_OPTIONS.map((value) => (
                <MenuItem key={value} value={value}>
                  {value} vCPUs
                </MenuItem>
              ))}
            </TextField>

            <TextField
              select
              label="Memory"
              value={memoryGb}
              onChange={(event) => setMemoryGb(Number(event.target.value))}
              fullWidth
            >
              {MEMORY_OPTIONS.map((value) => (
                <MenuItem key={value} value={value}>
                  {value} GB
                </MenuItem>
              ))}
            </TextField>

            <TextField
              select
              label="Public IP preference"
              value={publicAccess ? "yes" : "no"}
              onChange={(event) =>
                setPublicAccess(event.target.value === "yes")
              }
              fullWidth
            >
              <MenuItem value="no">No — private preference</MenuItem>
              <MenuItem value="yes">Yes — request public IP</MenuItem>
            </TextField>
          </Box>
        </Box>

        {/* Generate action */}
        <Button
          variant="contained"
          size="large"
          onClick={generatePreview}
          disabled={loading || !region.trim()}
          startIcon={
            loading ? (
              <CircularProgress size={18} color="inherit" />
            ) : (
              <CloudQueueIcon />
            )
          }
          sx={{
            borderRadius: 2,
            px: 3,
            py: 1.3,
            textTransform: "none",
            fontWeight: 800,
            bgcolor: "#2864dc",
            boxShadow: "none",
            "&:hover": {
              bgcolor: "#1e50b5",
              boxShadow: "none",
            },
          }}
        >
          {loading ? "Generating preview..." : "Generate Infrastructure Preview"}
        </Button>

        {error && (
          <Alert severity="error" sx={{ mt: 2, borderRadius: 2 }}>
            {error}
          </Alert>
        )}

        {/* Preview result */}
        {preview && (
          <Box sx={{ mt: 3 }}>
            <Divider sx={{ mb: 2.5 }} />

            <Typography
              component="h3"
              variant="h6"
              sx={{ fontWeight: 800, color: "#24334d", mb: 2 }}
            >
              Preview Summary
            </Typography>

            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: {
                  xs: "repeat(2, minmax(0, 1fr))",
                  sm: "repeat(3, minmax(0, 1fr))",
                  lg: "repeat(5, minmax(0, 1fr))",
                },
                gap: 1.5,
                mb: 2,
              }}
            >
              {[
                {
                  label: "Status",
                  value: preview.status,
                },
                {
                  label: "Create",
                  value: preview.resource_summary.create,
                },
                {
                  label: "Update",
                  value: preview.resource_summary.update,
                },
                {
                  label: "Delete",
                  value: preview.resource_summary.delete,
                },
                {
                  label: "Deployment",
                  value: preview.deployment_enabled ? "Enabled" : "Disabled",
                },
              ].map((item) => (
                <Box
                  key={item.label}
                  sx={{
                    p: 1.8,
                    bgcolor: "#f7f9fc",
                    border: "1px solid #e8edf4",
                    borderRadius: 2,
                    minWidth: 0,
                  }}
                >
                  <Typography
                    variant="caption"
                    sx={{ color: "#8190a5", display: "block", mb: 0.5 }}
                  >
                    {item.label}
                  </Typography>
                  <Typography
                    sx={{
                      fontWeight: 800,
                      color: "#24334d",
                      overflowWrap: "anywhere",
                    }}
                  >
                    {item.value}
                  </Typography>
                </Box>
              ))}
            </Box>

            <Stack
              direction="row"
              spacing={1}
              sx={{ mb: 2, flexWrap: "wrap" }}
            >
              <Chip
                label={`Provider: ${preview.cloud_provider.toUpperCase()}`}
                variant="outlined"
              />
              <Chip label={`Region: ${preview.region}`} variant="outlined" />
              <Chip
                label={`Environment: ${preview.environment}`}
                variant="outlined"
              />
              <Chip
                label={
                  preview.deployment_enabled
                    ? "Deployment enabled"
                    : "Deployment disabled"
                }
                color={preview.deployment_enabled ? "success" : "warning"}
              />
            </Stack>

            {/* Proposed resources */}
            <Box sx={{ mt: 3 }}>
              <Typography
                component="h3"
                variant="h6"
                sx={{ fontWeight: 800, color: "#24334d", mb: 1.5 }}
              >
                Proposed Resources
              </Typography>

              {preview.resources.map((resource) => (
                <Card
                  key={resource.address}
                  variant="outlined"
                  sx={{
                    mb: 1.5,
                    borderRadius: 2,
                    borderColor: "#e3eaf3",
                  }}
                >
                  <CardContent sx={{ p: 2 }}>
                    <Typography
                      component="div"
                      sx={{ fontWeight: 800, color: "#24334d" }}
                    >
                      {resource.address}
                    </Typography>

                    <Stack
                      direction="row"
                      spacing={1}
                      sx={{ mt: 1, flexWrap: "wrap" }}
                    >
                      <Chip
                        label={resource.type}
                        size="small"
                        variant="outlined"
                      />
                      <Chip
                        label={resource.action}
                        size="small"
                        color="primary"
                      />
                    </Stack>

                    <Typography
                      variant="body2"
                      color="text.secondary"
                      sx={{ mt: 1.5 }}
                    >
                      {resource.description}
                    </Typography>
                  </CardContent>
                </Card>
              ))}
            </Box>

            {/* Cost and approval information */}
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: {
                  xs: "1fr",
                  md: "repeat(2, minmax(0, 1fr))",
                },
                gap: 2,
                mt: 3,
              }}
            >
              <Card
                variant="outlined"
                sx={{ borderRadius: 2.5, borderColor: "#e3eaf3" }}
              >
                <CardContent sx={{ p: 2.5 }}>
                  <Typography
                    variant="h6"
                    sx={{ fontWeight: 800, color: "#24334d", mb: 2 }}
                  >
                    Cost Estimation
                  </Typography>

                  {preview.cost_estimate?.status === "estimated" ? (
                    <>
                      <Typography
                        variant="h4"
                        sx={{ fontWeight: 800, color: "#2864dc" }}
                      >
                        {preview.cost_estimate.currency}{" "}
                        {preview.cost_estimate.estimated_monthly_compute_cost}
                      </Typography>

                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{ mt: 0.5 }}
                      >
                        Estimated monthly compute cost
                      </Typography>

                      <Alert severity="info" sx={{ mt: 2, borderRadius: 2 }}>
                        Compute estimate only. Storage, networking, data
                        transfer, taxes and other services may add costs.
                      </Alert>

                      {preview.cost_estimate.pricing_source && (
                        <Typography variant="body2" sx={{ mt: 1.5 }}>
                          Pricing source: {preview.cost_estimate.pricing_source}
                        </Typography>
                      )}

                      {preview.cost_estimate.pricing_verified_on && (
                        <Typography variant="body2" color="text.secondary">
                          Pricing verified on:{" "}
                          {preview.cost_estimate.pricing_verified_on}
                        </Typography>
                      )}
                    </>
                  ) : (
                    <Alert severity="info" sx={{ borderRadius: 2 }}>
                      Cost estimate unavailable. Verified pricing has not been
                      configured. No price is assumed.
                    </Alert>
                  )}

                  <Divider sx={{ my: 2 }} />

                  <Typography
                    variant="subtitle2"
                    sx={{ fontWeight: 800, mb: 1 }}
                  >
                    Budget Status
                  </Typography>

                  <Chip
                    label={
                      preview.cost_estimate?.budget_status?.replace(
                        /_/g,
                        " ",
                      ) ?? "not evaluated"
                    }
                    variant="outlined"
                  />

                  {preview.cost_estimate?.budget_threshold != null && (
                    <Typography variant="body2" sx={{ mt: 1 }}>
                      Budget threshold: {preview.cost_estimate.currency}{" "}
                      {preview.cost_estimate.budget_threshold}
                    </Typography>
                  )}

                  {preview.cost_estimate?.note && (
                    <Typography
                      variant="body2"
                      color="text.secondary"
                      sx={{ mt: 1 }}
                    >
                      {preview.cost_estimate.note}
                    </Typography>
                  )}
                </CardContent>
              </Card>

              <Card
                variant="outlined"
                sx={{ borderRadius: 2.5, borderColor: "#e3eaf3" }}
              >
                <CardContent sx={{ p: 2.5 }}>
                  <Typography
                    variant="h6"
                    sx={{ fontWeight: 800, color: "#24334d", mb: 2 }}
                  >
                    Approval Review
                  </Typography>

                  <Stack spacing={1.5}>
                    {[
                      {
                        label: "Technical",
                        value: preview.approval?.technical_review ?? "pending",
                      },
                      {
                        label: "Cost",
                        value: preview.approval?.cost_review ?? "pending",
                      },
                      {
                        label: "Security",
                        value: preview.approval?.security_review ?? "pending",
                      },
                    ].map((review) => (
                      <Box
                        key={review.label}
                        sx={{
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                          gap: 1,
                          p: 1.3,
                          bgcolor: "#f7f9fc",
                          borderRadius: 2,
                        }}
                      >
                        <Typography
                          variant="body2"
                          sx={{ fontWeight: 700, color: "#475569" }}
                        >
                          {review.label} review
                        </Typography>
                        <Chip
                          size="small"
                          label={review.value}
                          variant="outlined"
                        />
                      </Box>
                    ))}
                  </Stack>
                </CardContent>
              </Card>
            </Box>

            <Alert severity="warning" sx={{ mt: 2, borderRadius: 2 }}>
              This is a local preview, not a real Terraform execution plan.
              Reviews are not automatically approved, and deployment remains
              disabled.
            </Alert>
          </Box>
        )}
      </CardContent>
    </Card>
  );
}
