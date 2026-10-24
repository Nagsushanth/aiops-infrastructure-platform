
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

type Environment = "development" | "testing" | "staging" | "production";

const ENVIRONMENTS: Environment[] = [
  "development",
  "testing",
  "staging",
  "production",
];

const CPU_OPTIONS = [2, 4, 8];
const MEMORY_OPTIONS = [2, 4, 8, 16, 32];

export default function TerraformPreview() {
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

  async function generatePreview() {
    setLoading(true);
    setError("");
    setPreview(null);

    try {
      const result = await createTerraformPreview({
        cloud_provider: "aws",
        resource_type: "EC2",
        environment,
        region: region.trim(),
        vcpus,
        memory_gb: memoryGb,
        public_access: publicAccess,
        operating_system: "linux",
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
    <Card sx={{ borderRadius: 3, mb: 3 }}>
      <CardContent sx={{ p: { xs: 2, md: 3 } }}>
        <Stack
  component="div"
  direction="row"
  spacing={1.5}
  sx={{ mb: 1, alignItems: "center" }}
>
          <CloudQueueIcon color="primary" fontSize="large" />

          <Box>
            <Typography component="h2" variant="h5" sx={{ fontWeight: 800 }}>
              Terraform Infrastructure Preview
            </Typography>

            <Typography variant="body2" color="text.secondary">
              Configure an AWS EC2 request and review the proposed resources.
            </Typography>
          </Box>
        </Stack>

        <Alert severity="info" sx={{ my: 2 }}>
          Local preview only. This request does not execute Terraform plan or
          create AWS resources.
        </Alert>

        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: {
              xs: "1fr",
              sm: "repeat(2, minmax(0, 1fr))",
            },
            gap: 2,
            mb: 2,
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
                {item}
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
        >
          {loading ? "Generating preview..." : "Generate Infrastructure Preview"}
        </Button>

        {error && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {error}
          </Alert>
        )}

        {preview && (
          <Box sx={{ mt: 3 }}>
            <Divider sx={{ mb: 2 }} />

            <Typography component="h3" variant="h6" sx={{ fontWeight: 700, mb: 1.5 }}>
              Preview Summary
            </Typography>

            <Stack
  component="div"
  direction="row"
  spacing={1}
  sx={{ mb: 2, flexWrap: "wrap" }}
>
              <Chip
                label={`Status: ${preview.status}`}
                color="success"
              />
              <Chip
                label={`Create: ${preview.resource_summary.create}`}
              />
              <Chip
                label={`Update: ${preview.resource_summary.update}`}
              />
              <Chip
                label={`Delete: ${preview.resource_summary.delete}`}
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

            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Provider: {preview.cloud_provider.toUpperCase()} · Region:{" "}
              {preview.region} · Environment: {preview.environment}
            </Typography>

            <Typography component="h3" variant="h6" sx={{ fontWeight: 700, mb: 1 }}>
              Proposed Resources
            </Typography>

            {preview.resources.map((resource) => (
              <Card
                key={resource.address}
                variant="outlined"
                sx={{ mb: 1.5, borderRadius: 2 }}
              >
                <CardContent>
                  <Typography component="div" sx={{ fontWeight: 700 }}>
                    {resource.address}
                  </Typography>

                  <Typography variant="body2" color="text.secondary">
                    Type: {resource.type} · Action: {resource.action}
                  </Typography>

                  <Typography variant="body2" sx={{ mt: 1 }}>
                    {resource.description}
                  </Typography>
                </CardContent>
              </Card>
            ))}

            <Alert severity="warning" sx={{ mt: 2 }}>
              Technical, cost and security reviews are pending. This is not a
              real Terraform plan, no verified cost estimate is available,
              and no deployment has been performed.
            </Alert>
          </Box>
        )}
      </CardContent>
    </Card>
  );
}
