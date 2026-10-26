
import { useCallback, useEffect, useState } from "react";
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
import RefreshIcon from "@mui/icons-material/Refresh";
import HistoryIcon from "@mui/icons-material/History";
import {
  apiRequest,
  getCurrentUser,
  type ApprovalResponse,
  type AuthUser,
} from "./api";

type ApprovalAuditEvent = {
  id: number;
  approval_request_id: number;
  actor_id: number;
  event_type: string;
  previous_status: string | null;
  new_status: string;
  comments: string | null;
  event_metadata: Record<string, unknown>;
  created_at: string;
};

export default function ApprovalReviewPanel() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [approvals, setApprovals] = useState<ApprovalResponse[]>([]);
  const [comments, setComments] = useState<Record<number, string>>({});
  const [audit, setAudit] = useState<Record<number, ApprovalAuditEvent[]>>({});
  const [loading, setLoading] = useState(true);
  const [workingId, setWorkingId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const loadApprovals = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const [currentUser, result] = await Promise.all([
        getCurrentUser(),
        apiRequest<ApprovalResponse[]>("/approvals"),
      ]);

      setUser(currentUser);
      setApprovals(result);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to load approval requests.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadApprovals();
  }, [loadApprovals]);

  async function loadAudit(approvalId: number) {
    setError("");
    setNotice("");

    try {
      const events = await apiRequest<ApprovalAuditEvent[]>(
        `/approvals/${approvalId}/audit`,
      );

      setAudit((previous) => ({
        ...previous,
        [approvalId]: events,
      }));
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Unable to load audit history.",
      );
    }
  }

  async function decide(approvalId: number, decision: "approved" | "rejected") {
    setWorkingId(approvalId);
    setError("");
    setNotice("");

    try {
      await apiRequest<ApprovalResponse>(
        `/approvals/${approvalId}/decision`,
        {
          method: "POST",
          body: JSON.stringify({
            decision,
            comments: comments[approvalId]?.trim() || null,
          }),
        },
      );

      setNotice(`Approval #${approvalId} was ${decision}.`);
      await loadApprovals();

      const events = await apiRequest<ApprovalAuditEvent[]>(
        `/approvals/${approvalId}/audit`,
      );

      setAudit((previous) => ({
        ...previous,
        [approvalId]: events,
      }));
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : `Unable to ${decision === "approved" ? "approve" : "reject"} request.`,
      );
    } finally {
      setWorkingId(null);
    }
  }

  return (
    <Card sx={{ borderRadius: 3 }}>
      <CardContent sx={{ p: { xs: 2, md: 3 } }}>
        <Box
            sx={{
            display: "flex",
            flexDirection: { xs: "column", sm: "row" },
            justifyContent: "space-between",
            alignItems: { xs: "stretch", sm: "center" },
            gap: 2,
            mb: 2,
        }}
    >
          <Box>
            <Typography variant="h5" sx={{ fontWeight: 700 }}>
              Approval Review
            </Typography>
            <Typography color="text.secondary">
              Review submitted infrastructure plans and audit decisions.
            </Typography>
          </Box>

          <Button
            startIcon={<RefreshIcon />}
            onClick={() => void loadApprovals()}
            disabled={loading}
          >
            Refresh
          </Button>
        </Box>

        {user && (
          <Alert severity={user.role === "admin" ? "info" : "warning"} sx={{ mb: 2 }}>
            {user.role === "admin"
              ? "Admin access: you can review requests submitted by other users."
              : "Requester view: you can see your own requests. Admins must decide pending approvals."}
          </Alert>
        )}

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {notice && (
          <Alert severity="success" sx={{ mb: 2 }}>
            {notice}
          </Alert>
        )}

        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", p: 4 }}>
            <CircularProgress />
          </Box>
        ) : approvals.length === 0 ? (
          <Alert severity="info">No approval requests found.</Alert>
        ) : (
          <Stack spacing={2}>
            {approvals.map((approval) => {
              const isAdmin = user?.role === "admin";
              const isRequester = user?.id === approval.requester_id;
              const canDecide =
                isAdmin && !isRequester && approval.status === "pending";

              return (
                <Card
                  key={approval.id}
                  variant="outlined"
                  sx={{ borderRadius: 2 }}
                >
                  <CardContent>
                    <Box
                        sx={{
                        display: "flex",
                        flexDirection: { xs: "column", sm: "row" },
                        justifyContent: "space-between",
                        gap: 1,
                     }}
                    >
                      <Box>
                        <Typography variant="h6" sx={{ fontWeight: 700 }}>
                          Approval #{approval.id}
                        </Typography>
                        <Typography variant="body2" color="text.secondary">
                          Infrastructure Request #{approval.infrastructure_request_id}
                        </Typography>
                        <Typography variant="body2" color="text.secondary">
                          Submitted: {new Date(approval.created_at).toLocaleString()}
                        </Typography>
                      </Box>

                      <Chip
                        label={approval.status}
                        color={
                          approval.status === "approved"
                            ? "success"
                            : approval.status === "rejected"
                              ? "error"
                              : "warning"
                        }
                        sx={{ alignSelf: { xs: "flex-start", sm: "center" } }}
                      />
                    </Box>

                    <Divider sx={{ my: 2 }} />

                    <Typography sx={{ fontWeight: 600, mb: 1 }}>
                      Plan summary
                    </Typography>

                    <Box
                      component="pre"
                      sx={{
                        p: 1.5,
                        borderRadius: 1,
                        bgcolor: "action.hover",
                        overflowX: "auto",
                        whiteSpace: "pre-wrap",
                        overflowWrap: "anywhere",
                        fontSize: 12,
                      }}
                    >
                      {JSON.stringify(approval.plan_summary, null, 2)}
                    </Box>

                    {approval.reviewer_comments && (
                      <Typography sx={{ mt: 1 }}>
                        Reviewer comments: {approval.reviewer_comments}
                      </Typography>
                    )}

                    {canDecide && (
                      <Stack spacing={1.5} sx={{ mt: 2 }}>
                        <TextField
                          label="Reviewer comments (optional)"
                          value={comments[approval.id] ?? ""}
                          onChange={(event) =>
                            setComments((previous) => ({
                              ...previous,
                              [approval.id]: event.target.value,
                            }))
                          }
                          multiline
                          minRows={2}
                          fullWidth
                        />

                        <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap" }}>
                          <Button
                            variant="contained"
                            color="success"
                            disabled={workingId !== null}
                            onClick={() => void decide(approval.id, "approved")}
                          >
                            {workingId === approval.id
                              ? "Processing..."
                              : "Approve"}
                          </Button>

                          <Button
                            variant="outlined"
                            color="error"
                            disabled={workingId !== null}
                            onClick={() => void decide(approval.id, "rejected")}
                          >
                            Reject
                          </Button>
                        </Stack>
                      </Stack>
                    )}

                    {isAdmin && isRequester && approval.status === "pending" && (
                      <Alert severity="warning" sx={{ mt: 2 }}>
                        You cannot approve or reject your own request.
                      </Alert>
                    )}

                    <Button
                      sx={{ mt: 2 }}
                      startIcon={<HistoryIcon />}
                      onClick={() => void loadAudit(approval.id)}
                    >
                      View audit history
                    </Button>

                    {audit[approval.id] && (
                      <Stack spacing={1} sx={{ mt: 1 }}>
                        {audit[approval.id].length === 0 ? (
                          <Typography variant="body2" color="text.secondary">
                            No audit events found.
                          </Typography>
                        ) : (
                          audit[approval.id].map((event) => (
                            <Box
                              key={event.id}
                              sx={{
                                p: 1.5,
                                bgcolor: "action.hover",
                                borderRadius: 1,
                              }}
                            >
                              <Typography sx={{ fontWeight: 600 }}>
                                {event.event_type}
                              </Typography>
                              <Typography variant="body2">
                                {event.previous_status ?? "—"} → {event.new_status}
                              </Typography>
                              <Typography variant="caption" color="text.secondary">
                                Actor #{event.actor_id} ·{" "}
                                {new Date(event.created_at).toLocaleString()}
                              </Typography>
                              {event.comments && (
                                <Typography variant="body2">
                                  Comments: {event.comments}
                                </Typography>
                              )}
                            </Box>
                          ))
                        )}
                      </Stack>
                    )}
                  </CardContent>
                </Card>
              );
            })}
          </Stack>
        )}

        <Alert severity="info" sx={{ mt: 3 }}>
          Approval decisions are recorded in the audit trail. This interface
          does not execute Terraform or provision infrastructure.
        </Alert>
      </CardContent>
    </Card>
  );
}
