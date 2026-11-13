
import TerraformPlanReview from "./TerraformPlanReview";
import { useEffect, useState } from "react";
import {
  AppBar,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  CssBaseline,
  Stack,
  Toolbar,
  Typography,
} from "@mui/material";
import CloudQueueIcon from "@mui/icons-material/CloudQueue";
import SmartToyIcon from "@mui/icons-material/SmartToy";
import StorageIcon from "@mui/icons-material/Storage";
import AccountTreeIcon from "@mui/icons-material/AccountTree";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import InfrastructureOperationsPanel from "./InfrastructureOperationsPanel";
import AuthPanel from "./AuthPanel";
import ChatPanel, { type InfrastructureSpec } from "./ChatPanel";
import TerraformPreview from "./TerraformPreview";
import ApprovalPanel from "./ApprovalPanel";
import ApprovalReviewPanel from "./ApprovalReviewPanel";

import {
  checkBackendHealth,
  clearAccessToken,
  getAccessToken,
  getCurrentUser,
  type AuthUser,
} from "./api";

type BackendStatus = {
  status: string;
  service: string;
};

const features = [
  {
    title: "AI Infrastructure Assistant",
    description: "Describe infrastructure requirements in natural language.",
    icon: <SmartToyIcon fontSize="large" />,
  },
  {
    title: "Terraform Provisioning",
    description: "Generate and validate infrastructure configurations.",
    icon: <CloudQueueIcon fontSize="large" />,
  },
  {
    title: "Resource Inventory",
    description: "View infrastructure resources and their status.",
    icon: <StorageIcon fontSize="large" />,
  },
  {
    title: "Workflow & Approvals",
    description: "Review proposed infrastructure changes before execution.",
    icon: <AccountTreeIcon fontSize="large" />,
  },
];

export default function App() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [authChecking, setAuthChecking] = useState(true);
  const [infrastructureSpec, setInfrastructureSpec] =
  useState<InfrastructureSpec | null>(null);
  const [infrastructureRequestId, setInfrastructureRequestId] =
  useState<number | null>(null);

  const [backend, setBackend] = useState<BackendStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function verifyAuthentication() {
    const token = getAccessToken();

    if (!token) {
      setUser(null);
      setAuthChecking(false);
      return;
    }

    try {
      const currentUser = await getCurrentUser();
      setUser(currentUser);
    } catch {
      clearAccessToken();
      setUser(null);
    } finally {
      setAuthChecking(false);
    }
  }

  function handleLogout() {
    clearAccessToken();
    setUser(null);
  }

  async function testConnection() {
    setLoading(true);
    setError("");

    try {
      const result = await checkBackendHealth();
      setBackend(result);
    } catch {
      setBackend(null);
      setError(
        "Connection failed. Confirm that FastAPI is running on port 8000.",
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void verifyAuthentication();
  }, []);

  useEffect(() => {
    void testConnection();
  }, []);

  if (authChecking) {
    return (
      <>
        <CssBaseline />
        <Box
          sx={{
            minHeight: "100vh",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            bgcolor: "#f4f7fb",
          }}
        >
          <CircularProgress />
        </Box>
      </>
    );
  }

  if (!user) {
    return (
      <>
        <CssBaseline />
        <AuthPanel
          onAuthenticated={() => {
            void verifyAuthentication();
          }}
        />
      </>
    );
  }

  return (
    <>
      <CssBaseline />

      <AppBar position="static" elevation={0}>
        <Toolbar>
          <CloudQueueIcon sx={{ mr: 1.5 }} />

          <Typography sx={{ flexGrow: 1, fontWeight: 700 }}>
            AIOps Infrastructure Support
          </Typography>

          <Typography
            sx={{
              mr: 2,
              display: { xs: "none", sm: "block" },
            }}
          >
            {user.email}
          </Typography>

          <Chip
            label="Development"
            sx={{
              color: "white",
              bgcolor: "rgba(255,255,255,0.18)",
            }}
          />

          <Button color="inherit" onClick={handleLogout} sx={{ ml: 1 }}>
            Logout
          </Button>
        </Toolbar>
      </AppBar>

      <Box
        sx={{
          minHeight: "100vh",
          bgcolor: "#f4f7fb",
          p: { xs: 2, md: 4 },
        }}
      >
        <Box sx={{ maxWidth: 1200, mx: "auto" }}>
          <Typography variant="h4" sx={{ fontWeight: 800, mb: 1 }}>
            Infrastructure Overview
          </Typography>

          <Typography color="text.secondary" sx={{ mb: 4 }}>
            Request, review, and manage infrastructure through an AI-powered
            support platform.
          </Typography>

          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: { xs: "1fr", md: "3fr 2fr" },
              gap: 3,
              mb: 3,
            }}
          >
            <Card sx={{ borderRadius: 3 }}>
              <CardContent sx={{ p: 3 }}>
                <Typography variant="h6" sx={{ fontWeight: 700 }}>
                  Backend API Connection
                </Typography>

                <Stack
                  direction="row"
                  spacing={1}
                  sx={{ my: 2, alignItems: "center" }}
                >
                  {loading ? (
                    <CircularProgress size={20} />
                  ) : backend ? (
                    <CheckCircleIcon color="success" />
                  ) : (
                    <CloudQueueIcon color="action" />
                  )}

                  <Typography>
                    {loading
                      ? "Checking FastAPI..."
                      : backend
                        ? `Connected — ${backend.service}`
                        : "Not connected"}
                  </Typography>
                </Stack>

                {backend && (
                  <Chip
                    label={`API status: ${backend.status}`}
                    color="success"
                    size="small"
                  />
                )}

                {error && (
                  <Typography color="error" sx={{ mb: 2 }}>
                    {error}
                  </Typography>
                )}

                <Stack
                  direction="row"
                  spacing={1}
                  sx={{ mt: 3, flexWrap: "wrap" }}
                >
                  <Button
                    variant="contained"
                    onClick={testConnection}
                    disabled={loading}
                  >
                    Test Connection
                  </Button>

                  <Button
                    onClick={() =>
                      window.open("http://localhost:8000/docs", "_blank")
                    }
                  >
                    API Documentation
                  </Button>
                </Stack>
              </CardContent>
            </Card>

            <Card
              sx={{
                borderRadius: 3,
                color: "white",
                background: "linear-gradient(135deg, #173b70, #2877c7)",
              }}
            >
              <CardContent sx={{ p: 3 }}>
                <SmartToyIcon sx={{ fontSize: 42, mb: 1 }} />

                <Typography variant="h5" sx={{ fontWeight: 700, mb: 1 }}>
                  AI Operations Assistant
                </Typography>

                <Typography sx={{ mb: 2 }}>
                  Turn infrastructure requests into reviewable, controlled
                  workflows.
                </Typography>

                <Chip
                  label="AI integration pending"
                  variant="outlined"
                  sx={{ color: "white", borderColor: "white" }}
                />
              </CardContent>
            </Card>
          </Box>

          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: {
                xs: "1fr",
                sm: "repeat(2, 1fr)",
                lg: "repeat(4, 1fr)",
              },
              gap: 3,
            }}
          >
            {features.map((feature) => (
              <Card
                key={feature.title}
                sx={{ borderRadius: 3, height: "100%" }}
              >
                <CardContent sx={{ p: 2.5 }}>
                  <Box sx={{ color: "primary.main", mb: 2 }}>
                    {feature.icon}
                  </Box>

                  <Typography variant="h6" sx={{ fontWeight: 700, mb: 1 }}>
                    {feature.title}
                  </Typography>

                  <Typography variant="body2" color="text.secondary">
                    {feature.description}
                  </Typography>

                  <Chip label="Coming next" size="small" sx={{ mt: 2 }} />
                </CardContent>
              </Card>
            ))}
          </Box>

          <Box sx={{ mt: 3 }}>
            <TerraformPreview specification={infrastructureSpec} />
          </Box>

          <Box sx={{ mt: 3 }}>
            <TerraformPlanReview />
          </Box>

          <Box sx={{ mt: 3 }}>
            <ChatPanel onRequirementsComplete={setInfrastructureSpec}
            onInfrastructureRequestIdChange={setInfrastructureRequestId} />
          </Box>

          <Box sx={{ mt: 3 }}>
            <ApprovalPanel infrastructureRequestId={infrastructureRequestId} />
          </Box>

          <Box sx={{ mt: 3 }}>
            <ApprovalReviewPanel />
          </Box>

          <Box sx={{ mt: 3 }}>
            <InfrastructureOperationsPanel
              infrastructureRequestId={infrastructureRequestId}/>
          </Box>

          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ mt: 4, textAlign: "center" }}
          >
            AIOps Platform for Infrastructure Support · Development Preview
          </Typography>
        </Box>
      </Box>
    </>
  );
}
