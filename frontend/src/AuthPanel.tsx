
import { useState, type FormEvent } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
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
        bgcolor: "#f4f7fb",
        p: 2,
      }}
    >
      <Card sx={{ width: "100%", maxWidth: 440, borderRadius: 3 }}>
        <CardContent sx={{ p: { xs: 3, sm: 4 } }}>
          <Typography
  variant="h5"
  sx={{ fontWeight: 800 }}
  gutterBottom
>
            AIOps Infrastructure Support
          </Typography>

          <Typography color="text.secondary" sx={{ mb: 2 }}>
            Sign in to manage infrastructure requests and review proposed
            changes.
          </Typography>

          <Tabs
            value={mode}
            onChange={(_, value: "login" | "register") => {
              setMode(value);
              setError("");
              setNotice("");
            }}
            variant="fullWidth"
            sx={{ mb: 3 }}
          >
            <Tab label="Login" value="login" />
            <Tab label="Register" value="register" />
          </Tabs>

          {notice && (
            <Alert severity="success" sx={{ mb: 2 }}>
              {notice}
            </Alert>
          )}

          {error && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {error}
            </Alert>
          )}

          <Box component="form" onSubmit={handleSubmit}>
            <TextField
              label="Email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
              fullWidth
              margin="normal"
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
              sx={{ mt: 3, py: 1.2 }}
            >
              {loading ? (
                <CircularProgress size={22} color="inherit" />
              ) : mode === "login" ? (
                "Log in"
              ) : (
                "Create account"
              )}
            </Button>
          </Box>
        </CardContent>
      </Card>
    </Box>
  );
}
