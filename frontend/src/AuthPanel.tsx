
import { useState, type FormEvent } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  Divider,
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import {
  loginUser,
  registerUser,
  setAccessToken,
} from "./api";

interface AuthPanelProps {
  onAuthenticated: () => void;
}

export default function AuthPanel({
  onAuthenticated,
}: AuthPanelProps) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setNotice("");

    const normalizedEmail = email.trim().toLowerCase();

    if (mode === "register" && password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    if (mode === "register" && password.length < 12) {
      setError("Password must contain at least 12 characters.");
      return;
    }

    setLoading(true);

    try {
      if (mode === "register") {
        await registerUser(normalizedEmail, password);
        setNotice("Account created. You can now log in.");
        setMode("login");
        setPassword("");
        setConfirmPassword("");
      } else {
        const result = await loginUser(normalizedEmail, password);
        setAccessToken(result.access_token);
        onAuthenticated();
      }
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Authentication failed. Please try again.",
      );
    } finally {
      setLoading(false);
    }
  }

  
  return (
    <Box
      sx={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        bgcolor: "#f3f6fc",
        backgroundImage:
          "radial-gradient(circle at 15% 20%, rgba(59,130,246,0.09), transparent 32%), radial-gradient(circle at 85% 80%, rgba(37,99,235,0.08), transparent 32%)",
        p: { xs: 2, sm: 3 },
      }}
    >
      <Card
        sx={{
          width: "100%",
          maxWidth: 460,
          borderRadius: 4,
          border: "1px solid",
          borderColor: "#e3eaf5",
          boxShadow: "0 16px 50px rgba(30, 58, 100, 0.10)",
          overflow: "hidden",
        }}
      >
        <Box
          sx={{
            height: 6,
            background: "linear-gradient(90deg, #173d75, #3478dc)",
          }}
        />

        <CardContent sx={{ p: { xs: 3, sm: 4.5 } }}>
          <Box sx={{ textAlign: "center", mb: 3 }}>
            <Box
              sx={{
                width: 62,
                height: 62,
                mx: "auto",
                mb: 2,
                borderRadius: 3,
                bgcolor: "#eaf1ff",
                color: "#2563eb",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 30,
                fontWeight: 800,
              }}
            >
              A
            </Box>

            <Typography
              variant="h5"
              sx={{ fontWeight: 800, color: "#172b4d" }}
            >
              AIOps Infrastructure
            </Typography>

            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ mt: 0.5 }}
            >
              Infrastructure Support Platform
            </Typography>
          </Box>

          <Box sx={{ mb: 3 }}>
            <Typography
              variant="h6"
              sx={{ fontWeight: 750, color: "#1e293b" }}
            >
              {mode === "login" ? "Welcome back" : "Create your account"}
            </Typography>

            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              {mode === "login"
                ? "Sign in to manage infrastructure requests and workflows."
                : "Register to access your infrastructure management workspace."}
            </Typography>
          </Box>

          <Tabs
            value={mode}
            onChange={(_, value: "login" | "register") => {
              setMode(value);
              setError("");
              setNotice("");
            }}
            variant="fullWidth"
            sx={{
              mb: 3,
              bgcolor: "#f1f5fb",
              borderRadius: 2,
              minHeight: 46,
              p: 0.5,
              "& .MuiTabs-indicator": {
                display: "none",
              },
              "& .MuiTab-root": {
                minHeight: 40,
                borderRadius: 1.5,
                textTransform: "none",
                fontWeight: 700,
                color: "text.secondary",
              },
              "& .Mui-selected": {
                bgcolor: "#ffffff",
                color: "#2563eb",
                boxShadow: "0 2px 8px rgba(15,23,42,0.08)",
              },
            }}
          >
            <Tab label="Login" value="login" />
            <Tab label="Register" value="register" />
          </Tabs>

          {notice && (
            <Alert severity="success" sx={{ mb: 2, borderRadius: 2 }}>
              {notice}
            </Alert>
          )}

          {error && (
            <Alert severity="error" sx={{ mb: 2, borderRadius: 2 }}>
              {error}
            </Alert>
          )}

          <Box component="form" onSubmit={handleSubmit}>
            <TextField
              label="Email address"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
              fullWidth
              margin="normal"
              placeholder="you@example.com"
              sx={{
                "& .MuiOutlinedInput-root": {
                  borderRadius: 2,
                  bgcolor: "#ffffff",
                },
              }}
            />

            <TextField
              label="Password"
              type="password"
              autoComplete={
                mode === "login" ? "current-password" : "new-password"
              }
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
              fullWidth
              margin="normal"
              sx={{
                "& .MuiOutlinedInput-root": {
                  borderRadius: 2,
                  bgcolor: "#ffffff",
                },
              }}
              slotProps={{
                htmlInput: {
                  maxLength: 72,
                  minLength: mode === "register" ? 12 : 1,
                },
              }}
            />

            {mode === "register" && (
              <TextField
                label="Confirm password"
                type="password"
                autoComplete="new-password"
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                required
                fullWidth
                margin="normal"
                sx={{
                  "& .MuiOutlinedInput-root": {
                    borderRadius: 2,
                    bgcolor: "#ffffff",
                  },
                }}
                slotProps={{
                  htmlInput: {
                    maxLength: 72,
                    minLength: 12,
                  },
                }}
              />
            )}

            <Button
              type="submit"
              variant="contained"
              fullWidth
              disabled={loading}
              sx={{
                mt: 3,
                py: 1.5,
                borderRadius: 2,
                textTransform: "none",
                fontSize: 15,
                fontWeight: 750,
                background: "linear-gradient(100deg, #1d4f91, #3478dc)",
                boxShadow: "0 5px 14px rgba(37,99,235,0.20)",
                "&:hover": {
                  background: "linear-gradient(100deg, #173d75, #2563c7)",
                  boxShadow: "0 7px 18px rgba(37,99,235,0.26)",
                },
              }}
            >
              {loading ? (
                <CircularProgress size={22} color="inherit" />
              ) : mode === "login" ? (
                "Sign in to workspace"
              ) : (
                "Create account"
              )}
            </Button>
          </Box>

          <Divider sx={{ my: 3 }} />

          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ textAlign:"center", display:"block"}}
            
          >
            Secure infrastructure management workspace
          </Typography>
        </CardContent>
      </Card>
    </Box>
  );
}