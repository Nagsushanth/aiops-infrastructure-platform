
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
  Typography,
} from "@mui/material";

import {
  getInfrastructureOperations,
  getInfrastructureOperationAudit,
  getInfrastructureResourceHealth,
  type InfrastructureOperationResponse,
  type InfrastructureOperationAuditEvent,
  type InfrastructureResourceHealthResponse,
} from "./api";

interface InfrastructureOperationsPanelProps {
  infrastructureRequestId: number | null;
}

function statusColor(
  status: string,
): "success" | "error" | "warning" | "info" | "default" {
  switch (status.toLowerCase()) {
    case "succeeded":
    case "completed":
    case "approved":
    case "healthy":
    case "ok":
      return "success";
    case "failed":
    case "rejected":
    case "impaired":
      return "error";
    case "running":
    case "queued":
    case "pending":
    case "initializing":
    case "not_running":
      return "warning";
    case "blocked":
      return "info";
    default:
      return "default";
  }
}

export default function InfrastructureOperationsPanel({
  infrastructureRequestId,
}: InfrastructureOperationsPanelProps) {
  const [operations, setOperations] = useState<
    InfrastructureOperationResponse[]
  >([]);

  const [selectedOperationId, setSelectedOperationId] =
    useState<number | null>(null);

  const [audit, setAudit] = useState<
    InfrastructureOperationAuditEvent[]
  >([]);

  const [resourceHealth, setResourceHealth] =
    useState<InfrastructureResourceHealthResponse | null>(null);

  const [loading, setLoading] = useState(true);
  const [auditLoading, setAuditLoading] = useState(false);
  const [healthLoading, setHealthLoading] = useState(false);

  const [error, setError] = useState("");
  const [auditError, setAuditError] = useState("");
  const [healthError, setHealthError] = useState("");

  const loadOperations = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const result = await getInfrastructureOperations();
      setOperations(result);
    } catch {
      setError("Unable to load infrastructure operations.");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadAudit = useCallback(async (operationId: number) => {
    setAuditLoading(true);
    setAuditError("");

    try {
      const result =
        await getInfrastructureOperationAudit(operationId);
      setAudit(result);
    } catch {
      setAudit([]);
      setAuditError("Unable to load operation audit history.");
    } finally {
      setAuditLoading(false);
    }
  }, []);

  const loadResourceHealth = useCallback(async () => {
    if (infrastructureRequestId === null) {
      setResourceHealth(null);
      setHealthError("");
      return;
    }

    setHealthLoading(true);
    setHealthError("");

    try {
      const result = await getInfrastructureResourceHealth(
        infrastructureRequestId,
      );
      setResourceHealth(result);
    } catch {
      setResourceHealth(null);
      setHealthError("Unable to load EC2 resource health.");
    } finally {
      setHealthLoading(false);
    }
  }, [infrastructureRequestId]);

  useEffect(() => {
    void loadOperations();
  }, [loadOperations]);

  useEffect(() => {
    void loadResourceHealth();
  }, [loadResourceHealth]);

  async function handleAudit(operationId: number) {
    if (selectedOperationId === operationId) {
      setSelectedOperationId(null);
      setAudit([]);
      return;
    }

    setSelectedOperationId(operationId);
    setAudit([]);
    await loadAudit(operationId);
  }

  function formatDate(value: string | null) {
    if (!value) return "Not available";

    return new Date(value).toLocaleString();
  }

  return (
    <Card sx={{ borderRadius: 3 }}>
      <CardContent sx={{ p: 3 }}>
        {infrastructureRequestId !== null && (
          <Box sx={{ mb: 3 }}>
            <Stack
              direction="row"
              sx={{ justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 1 }}
            >
              <Box>
                <Typography variant="h6" sx={{ fontWeight: 700 }}>
                  EC2 Resource Health
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Live AWS instance lifecycle and status checks.
                </Typography>
              </Box>

              <Button
                variant="outlined"
                size="small"
                onClick={() => void loadResourceHealth()}
                disabled={healthLoading}
              >
                Refresh Health
              </Button>
            </Stack>

            <Divider sx={{ my: 2 }} />

            {healthLoading ? (
              <Box sx={{ display: "flex", justifyContent: "center", p: 2 }}>
                <CircularProgress size={24} />
              </Box>
            ) : healthError ? (
              <Alert severity="error">{healthError}</Alert>
            ) : resourceHealth?.total_instances === 0 ? (
              <Alert severity="info">
                No EC2 instances found in the current infrastructure inventory.
              </Alert>
            ) : resourceHealth ? (
              <Stack spacing={2}>
                {resourceHealth.health.map((instance) => (
                  <Box
                    key={instance.instance_id}
                    sx={{
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: 2,
                      p: 2,
                    }}
                  >
                    <Stack
                      direction="row"
                      sx={{ justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 1 }}
                    >
                      <Typography sx={{ fontWeight: 700 }}>
                        {instance.instance_id}
                      </Typography>

                      <Chip
                        label={instance.health_status.replace("_", " ")}
                        color={statusColor(instance.health_status)}
                        size="small"
                      />
                    </Stack>

                    <Stack
                      direction="row"
                      sx={{ spacing: 1 , flexWrap: "wrap" , mt: 1}}
                    >
                      <Chip
                        label={`Lifecycle: ${instance.lifecycle_state}`}
                        variant="outlined"
                        size="small"
                      />

                      <Chip
                        label={`System: ${instance.system_status ?? "N/A"}`}
                        variant="outlined"
                        size="small"
                      />

                      <Chip
                        label={`Instance: ${instance.instance_status ?? "N/A"}`}
                        variant="outlined"
                        size="small"
                      />
                    </Stack>
                  </Box>
                ))}
              </Stack>
            ) : null}
          </Box>
        )}

        <Stack
          direction="row"
          sx={{
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 2,
          }}
        >
          <Box>
            <Typography variant="h6" sx={{ fontWeight: 700 }}>
              Infrastructure Operations
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Track provisioning and deprovisioning requests and their audit history.
            </Typography>
          </Box>

          <Button
            variant="outlined"
            onClick={() => void loadOperations()}
            disabled={loading}
          >
            Refresh
          </Button>
        </Stack>

        <Divider sx={{ my: 2 }} />

        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", p: 3 }}>
            <CircularProgress />
          </Box>
        ) : error ? (
          <Alert severity="error">{error}</Alert>
        ) : operations.length === 0 ? (
          <Alert severity="info">
            No infrastructure operations found yet.
          </Alert>
        ) : (
          <Stack spacing={2}>
            {operations.map((operation) => (
              <Box
                key={operation.id}
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 2,
                  p: 2,
                }}
              >
                <Stack
                  direction="row"
                  sx={{
                    justifyContent: "space-between",
                    alignItems: "center",
                    flexWrap: "wrap",
                    gap: 1,
                  }}
                >
                  <Typography sx={{ fontWeight: 700 }}>
                    Operation #{operation.id}
                  </Typography>

                  <Chip
                    label={operation.status}
                    color={statusColor(operation.status)}
                    size="small"
                  />
                </Stack>

                <Stack
                  direction="row"
                  spacing={1}
                  sx={{ flexWrap: "wrap", mt: 1 }}
                >
                  <Chip
                    label={operation.operation}
                    variant="outlined"
                    size="small"
                  />
                  <Chip
                    label={`Request #${operation.infrastructure_request_id}`}
                    variant="outlined"
                    size="small"
                  />
                  <Chip
                    label={`Plan #${operation.terraform_plan_record_id}`}
                    variant="outlined"
                    size="small"
                  />
                </Stack>

                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mt: 1 }}
                >
                  Created: {formatDate(operation.created_at)}
                </Typography>

                {operation.started_at && (
                  <Typography variant="body2" color="text.secondary">
                    Started: {formatDate(operation.started_at)}
                  </Typography>
                )}

                {operation.completed_at && (
                  <Typography variant="body2" color="text.secondary">
                    Completed: {formatDate(operation.completed_at)}
                  </Typography>
                )}

                {operation.error_code && (
                  <Alert severity="warning" sx={{ mt: 1 }}>
                    Error code: {operation.error_code}
                  </Alert>
                )}

                {typeof operation.result_summary?.message === "string" && (
                  <Typography variant="body2" sx={{ mt: 1 }}>
                    {operation.result_summary.message}
                  </Typography>
                )}

                <Button
                  size="small"
                  sx={{ mt: 1 }}
                  onClick={() => void handleAudit(operation.id)}
                >
                  {selectedOperationId === operation.id
                    ? "Hide audit history"
                    : "View audit history"}
                </Button>

                {selectedOperationId === operation.id && (
                  <Box sx={{ mt: 2 }}>
                    <Divider sx={{ mb: 2 }} />

                    <Typography sx={{ fontWeight: 700, mb: 1 }}>
                      Audit History
                    </Typography>

                    {auditLoading ? (
                      <CircularProgress size={22} />
                    ) : auditError ? (
                      <Alert severity="error">{auditError}</Alert>
                    ) : audit.length === 0 ? (
                      <Typography variant="body2" color="text.secondary">
                        No audit events found.
                      </Typography>
                    ) : (
                      <Stack spacing={1.5}>
                        {audit.map((event) => (
                          <Box
                            key={`${event.source}-${event.id}`}
                            sx={{
                              borderLeft: "3px solid",
                              borderColor: "primary.main",
                              pl: 2,
                            }}
                          >
                            <Typography sx={{ fontWeight: 600 }}>
                              {event.event_type}
                            </Typography>

                            <Typography
                              variant="body2"
                              color="text.secondary"
                            >
                              {event.previous_status ?? "Initial"} → {event.new_status}
                            </Typography>

                            <Typography
                              variant="body2"
                              color="text.secondary"
                            >
                              Actor: {event.actor_type}
                              {event.actor_id !== null
                                ? ` #${event.actor_id}`
                                : ""}
                              {" · "}
                              {formatDate(event.created_at)}
                            </Typography>

                            {event.comments && (
                              <Typography variant="body2">
                                {event.comments}
                              </Typography>
                            )}
                          </Box>
                        ))}
                      </Stack>
                    )}
                  </Box>
                )}
              </Box>
            ))}
          </Stack>
        )}
      </CardContent>
    </Card>
  );
}
