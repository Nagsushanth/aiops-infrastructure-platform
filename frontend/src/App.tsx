
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
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import LogoutIcon from "@mui/icons-material/Logout";
import ApiIcon from "@mui/icons-material/Api";

import InfrastructureOperationsPanel from "./InfrastructureOperationsPanel";
import AuthPanel from "./AuthPanel";
import ChatPanel, { type InfrastructureSpec } from "./ChatPanel";
import TerraformPreview from "./TerraformPreview";
import TerraformPlanReview from "./TerraformPlanReview";
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
            bgcolor: "#f5f7fb",
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

      <AppBar
        position="sticky"
        elevation={0}
        sx={{
          bgcolor: "#ffffff",
          color: "#17243b",
          borderBottom: "1px solid #e8edf4",
        }}
      >
        <Toolbar
          sx={{
            maxWidth: 1440,
            width: "100%",
            mx: "auto",
            px: { xs: 2, md: 4 },
            minHeight: "72px !important",
          }}
        >
          <Box
            sx={{
              width: 42,
              height: 42,
              borderRadius: 2.5,
              bgcolor: "#eaf1ff",
              color: "#2864dc",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              mr: 1.5,
            }}
          >
            <CloudQueueIcon />
          </Box>

          <Box sx={{ flexGrow: 1 }}>
            <Typography
              sx={{
                fontWeight: 800,
                fontSize: "1rem",
                letterSpacing: "-0.3px",
              }}
            >
              AIOps Infrastructure
            </Typography>

            <Typography
              variant="caption"
              sx={{ color: "#758198", display: "block" }}
            >
              Infrastructure Support Platform
            </Typography>
          </Box>

          <Chip
            label="Development"
            size="small"
            sx={{
              bgcolor: "#edf3ff",
              color: "#285ac5",
              fontWeight: 700,
              mr: { xs: 1, sm: 2 },
            }}
          />

          <Typography
            sx={{
              mr: 2,
              color: "#64748b",
              fontSize: "0.85rem",
              display: { xs: "none", md: "block" },
            }}
          >
            {user.email}
          </Typography>

          <Button
            color="inherit"
            onClick={handleLogout}
            startIcon={<LogoutIcon />}
            sx={{
              textTransform: "none",
              fontWeight: 600,
              color: "#475569",
            }}
          >
            Logout
          </Button>
        </Toolbar>
      </AppBar>

      <Box
        sx={{
          minHeight: "100vh",
          bgcolor: "#f5f7fb",
          px: { xs: 2, sm: 3, md: 4 },
          py: { xs: 3, md: 4 },
        }}
      >
        <Box sx={{ maxWidth: 1440, mx: "auto" }}>

          {/* Dashboard heading */}
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: { xs: "flex-start", md: "center" },
              flexDirection: { xs: "column", md: "row" },
              gap: 2,
              mb: 3,
            }}
          >
            <Box>
              <Typography
                variant="h4"
                sx={{
                  color: "#17243b",
                  fontWeight: 800,
                  fontSize: { xs: "1.7rem", md: "2rem" },
                  letterSpacing: "-0.8px",
                  mb: 0.7,
                }}
              >
                Infrastructure Overview
              </Typography>

              <Typography sx={{ color: "#718096", fontSize: "0.95rem" }}>
                Manage infrastructure requests, Terraform plans, approvals
                and infrastructure operations from one place.
              </Typography>
            </Box>

            <Button
              variant="outlined"
              startIcon={<ApiIcon />}
              onClick={testConnection}
              disabled={loading}
              sx={{
                borderRadius: 2,
                textTransform: "none",
                fontWeight: 700,
                px: 2,
                py: 1,
                borderColor: "#d5deeb",
                color: "#334b70",
                bgcolor: "#ffffff",
              }}
            >
              Refresh Connection
            </Button>
          </Box>

          {/* Main status and assistant cards */}
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: {
                xs: "1fr",
                lg: "1.1fr 1.9fr",
              },
              gap: 2.5,
              mb: 4,
            }}
          >
            <Card
              elevation={0}
              sx={{
                border: "1px solid #e6ebf2",
                borderRadius: 3,
                bgcolor: "#ffffff",
              }}
            >
              <CardContent sx={{ p: 3 }}>
                <Stack
                  direction="row"
                  spacing={1.2}
                  sx={{ mb: 2.5, alignItems: "center" }}
                >
                  <Box
                    sx={{
                      width: 38,
                      height: 38,
                      borderRadius: 2,
                      bgcolor: "#edf3ff",
                      color: "#2864dc",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    <ApiIcon />
                  </Box>

                  <Typography
                    sx={{ fontWeight: 750, color: "#24334d" }}
                  >
                    Backend API
                  </Typography>
                </Stack>

                <Stack
                  direction="row"
                  spacing={1.2}
                  sx={{ mb: 1.5, alignItems: "center" }}
                >
                  {loading ? (
                    <CircularProgress size={19} />
                  ) : backend ? (
                    <CheckCircleIcon color="success" />
                  ) : (
                    <CloudQueueIcon sx={{ color: "#94a3b8" }} />
                  )}

                  <Typography
                    sx={{
                      fontWeight: 700,
                      color: backend ? "#16804a" : "#64748b",
                    }}
                  >
                    {loading
                      ? "Checking connection..."
                      : backend
                        ? "Connected"
                        : "Connection unavailable"}
                  </Typography>
                </Stack>

                {backend && (
                  <Stack
                    direction="row"
                    spacing={1}
                    sx={{ alignItems: "center", flexWrap: "wrap" }}
                  >
                    <Chip
                      label={backend.service}
                      size="small"
                      sx={{
                        bgcolor: "#f0f5ff",
                        color: "#315dba",
                        fontWeight: 600,
                      }}
                    />

                    <Chip
                      label={`Status: ${backend.status}`}
                      size="small"
                      color="success"
                      variant="outlined"
                    />
                  </Stack>
                )}

                <Typography
                  component="a"
                  href="http://localhost:8000/docs"
                  target="_blank"
                  rel="noopener noreferrer"
                  sx={{
                    display: "inline-flex",
                    mt: 1.5,
                    color: "#2864dc",
                    fontWeight: 700,
                    fontSize: "0.875rem",
                    textDecoration: "none",
                    "&:hover": {
                      textDecoration: "underline",
                    },
                  }}
                >
                  Open API Documentation →
                </Typography>

                {error && (
                  
                  <Typography
                    color="error"
                    variant="body2"
                    sx={{ mt: 1.5 }}
                  >
                    {error}
                  </Typography>
                )}
              </CardContent>
            </Card>

            <Card
              elevation={0}
              sx={{
                borderRadius: 3,
                overflow: "hidden",
                color: "#ffffff",
                background:
                  "linear-gradient(120deg, #173b70 0%, #245db2 60%, #367de0 100%)",
                position: "relative",
              }}
            >
              <CardContent
                sx={{
                  p: { xs: 3, md: 3.5 },
                  position: "relative",
                  zIndex: 1,
                }}
              >
                <Stack
                  direction="row"
                  spacing={2}
                  sx={{ mb: 2 ,alignItems:"center" }}
                >
                  <Box
                    sx={{
                      width: 48,
                      height: 48,
                      borderRadius: 2.5,
                      bgcolor: "rgba(255,255,255,0.15)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    <SmartToyIcon sx={{ fontSize: 29 }} />
                  </Box>

                  <Box>
                    <Typography
                      variant="h5"
                      sx={{ fontWeight: 800, letterSpacing: "-0.4px" }}
                    >
                      AI Operations Assistant
                    </Typography>

                    <Typography
                      sx={{
                        color: "rgba(255,255,255,0.76)",
                        fontSize: "0.83rem",
                      }}
                    >
                      Intelligent infrastructure workflows
                    </Typography>
                  </Box>
                </Stack>

                <Typography
                  sx={{
                    maxWidth: 650,
                    color: "rgba(255,255,255,0.9)",
                    lineHeight: 1.7,
                    fontSize: "0.94rem",
                  }}
                >
                  Describe your infrastructure requirements in natural
                  language. Generate Terraform plans, review cost and
                  security information, and manage approval-controlled
                  infrastructure operations.
                </Typography>
              </CardContent>
            </Card>
          </Box>

          {/* Infrastructure workflow */}
          <Box sx={{ mb: 2 }}>
            <Typography
              variant="h5"
              sx={{
                color: "#24334d",
                fontWeight: 800,
                fontSize: "1.25rem",
                mb: 0.6,
              }}
            >
              Infrastructure Workspace
            </Typography>

            <Typography
              sx={{ color: "#8190a5", fontSize: "0.88rem" }}
            >
              Your infrastructure request and management workflows.
            </Typography>
          </Box>

          <Stack spacing={3}>
            <Card
              elevation={0}
              sx={{
                border: "1px solid #e6ebf2",
                borderRadius: 3,
                overflow: "visible",
              }}
            >
              <CardContent sx={{ p: { xs: 2, md: 3 } }}>
                <TerraformPreview specification={infrastructureSpec} />
              </CardContent>
            </Card>

            <Card
              elevation={0}
              sx={{
                border: "1px solid #e6ebf2",
                borderRadius: 3,
                overflow: "visible",
              }}
            >
              <CardContent sx={{ p: { xs: 2, md: 3 } }}>
                <TerraformPlanReview />
              </CardContent>
            </Card>

            <Card
              elevation={0}
              sx={{
                border: "1px solid #e6ebf2",
                borderRadius: 3,
                overflow: "visible",
              }}
            >
              <CardContent sx={{ p: { xs: 2, md: 3 } }}>
                <ChatPanel
                  onRequirementsComplete={setInfrastructureSpec}
                  onInfrastructureRequestIdChange={
                    setInfrastructureRequestId
                  }
                />
              </CardContent>
            </Card>

            <Card
              elevation={0}
              sx={{
                border: "1px solid #e6ebf2",
                borderRadius: 3,
                overflow: "visible",
              }}
            >
              <CardContent sx={{ p: { xs: 2, md: 3 } }}>
                <ApprovalPanel
                  infrastructureRequestId={infrastructureRequestId}
                />
              </CardContent>
            </Card>

            <Card
              elevation={0}
              sx={{
                border: "1px solid #e6ebf2",
                borderRadius: 3,
                overflow: "visible",
              }}
            >
              <CardContent sx={{ p: { xs: 2, md: 3 } }}>
                <ApprovalReviewPanel />
              </CardContent>
            </Card>

            <Card
              elevation={0}
              sx={{
                border: "1px solid #e6ebf2",
                borderRadius: 3,
                overflow: "visible",
              }}
            >
              <CardContent sx={{ p: { xs: 2, md: 3 } }}>
                <InfrastructureOperationsPanel
                  infrastructureRequestId={infrastructureRequestId}
                />
              </CardContent>
            </Card>
          </Stack>

          <Box
            sx={{
              textAlign: "center",
              py: 4,
              mt: 3,
              borderTop: "1px solid #e6ebf2",
            }}
          >
            <Typography
              variant="body2"
              sx={{ color: "#94a3b8", fontSize: "0.82rem" }}
            >
              AIOps Platform for Infrastructure Support
            </Typography>
          </Box>
        </Box>
      </Box>
    </>
  );
}
