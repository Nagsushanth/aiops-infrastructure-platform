
import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  Paper,
  Stack,
  TextField,
  Typography,
  IconButton,
  LinearProgress,
} from "@mui/material";
import SendIcon from "@mui/icons-material/Send";
import AttachFileIcon from "@mui/icons-material/AttachFile";
import DownloadIcon from "@mui/icons-material/Download";

import {
  createConversation,
  downloadAttachment,
  getAccessToken,
  getConversationAttachments,
  getConversationBySession,
  getMessages,
  uploadAttachment,
  type ChatAttachment,
  type ChatMessage,
} from "./api";

import MessageRenderer from "./MessageRenderer";

const SESSION_KEY = "aiops-conversation-session";
const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

type ConnectionStatus = "connecting" | "connected" | "disconnected";

type SavedMessage = ChatMessage;

export type InfrastructureSpec = {
  cloud_provider: "aws" | "azure" | "gcp";
  resource_type: string;
  environment: "development" | "testing" | "staging" | "production";
  region: string;
  vcpus: number;
  memory_gb: number;
  public_access: boolean;
  operating_system: string;
};

type ChatPanelProps = {
  onRequirementsComplete: (spec: InfrastructureSpec | null) => void;
  onInfrastructureRequestIdChange: (id: number | null) => void;
};

type SocketEvent =
  | {
      type: "connected";
      conversation_id: number;
      message: string;
    }
  | {
      type: "message_saved";
      message: SavedMessage;
    }
  | {
      type: "requirement_update";
      status: string;
      question: string | null;
      missing_field: string | null;
      requirements: Record<string, unknown>;
      validation_errors: unknown[] | null;
      user_message: SavedMessage;
      assistant_message: SavedMessage;
    }
  | {
      type: "requirements_result";
      result?: {
        status?: string;
        question?: string | null;
        infrastructure_request_id?: number;
        requirements?: unknown;
        [key: string]: unknown;
      };
      question?: string | null;
      [key: string]: unknown;
    }
  | {
      type: "assistant_message";
      message: SavedMessage;
      [key: string]: unknown;
    }
  | {
      type: "error";
      detail: string;
    }
  | {
      type: "pong";
    };

function isInfrastructureSpec(
  value: unknown,
): value is InfrastructureSpec {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }

  const spec = value as Record<string, unknown>;

  return (
    (spec.cloud_provider === "aws" ||
      spec.cloud_provider === "azure" ||
      spec.cloud_provider === "gcp") &&
    typeof spec.resource_type === "string" &&
    spec.resource_type.trim().length > 0 &&
    (spec.environment === "development" ||
      spec.environment === "testing" ||
      spec.environment === "staging" ||
      spec.environment === "production") &&
    typeof spec.region === "string" &&
    spec.region.trim().length > 0 &&
    typeof spec.vcpus === "number" &&
    Number.isInteger(spec.vcpus) &&
    spec.vcpus >= 1 &&
    typeof spec.memory_gb === "number" &&
    Number.isInteger(spec.memory_gb) &&
    spec.memory_gb >= 1 &&
    typeof spec.public_access === "boolean" &&
    typeof spec.operating_system === "string" &&
    spec.operating_system.trim().length > 0
  );
}

export default function ChatPanel({
  onRequirementsComplete,
  onInfrastructureRequestIdChange,
}: ChatPanelProps) {
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [infrastructureRequestId, setInfrastructureRequestId] =
    useState<number | null>(null);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [creatingConversation, setCreatingConversation] = useState(false);
  const [error, setError] = useState("");

  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [uploading, setUploading] = useState(false);

  const [connectionStatus, setConnectionStatus] =
    useState<ConnectionStatus>("connecting");

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const pendingContentRef = useRef<string | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const retryCountRef = useRef(0);
  const mountedRef = useRef(false);

  const addMessages = useCallback((incoming: ChatMessage[]) => {
    setMessages((current) => {
      const existingIds = new Set(current.map((message) => message.id));
      const newMessages = incoming.filter(
        (message) => !existingIds.has(message.id),
      );

      return [...current, ...newMessages];
    });
  }, []);

  const sendPendingMessage = useCallback(() => {
    const socket = socketRef.current;
    const content = pendingContentRef.current;

    if (
      !socket ||
      socket.readyState !== WebSocket.OPEN ||
      content === null
    ) {
      return;
    }

    socket.send(JSON.stringify({ type: "message", content }));
  }, []);

  // Restore the current conversation or create one on first use.
  useEffect(() => {
    let cancelled = false;

    async function loadConversation() {
      try {
        if (!getAccessToken()) {
          throw new Error(
            "Please sign in to access the infrastructure assistant.",
          );
        }

        let sessionId: string | null = sessionStorage.getItem(SESSION_KEY);
        let conversation:
          | Awaited<ReturnType<typeof getConversationBySession>>
          | null = null;

        if (sessionId) {
          try {
            conversation = await getConversationBySession(sessionId);
          } catch (err) {
            if (
              err instanceof Error &&
              (err.message.includes("404") || err.message.includes("409"))
            ) {
              sessionId = null;
            } else {
              throw err;
            }
          }
        }

        if (!sessionId || !conversation) {
          sessionId = crypto.randomUUID();

          conversation = await createConversation(
            sessionId,
            "Infrastructure request",
          );

          sessionStorage.setItem(SESSION_KEY, sessionId);
        }

        if (cancelled) return;

        sessionIdRef.current = sessionId;

        const [history, attachmentHistory] = await Promise.all([
          getMessages(conversation.id),
          getConversationAttachments(conversation.id),
        ]);

        if (cancelled) return;

        setMessages(history);
        setAttachments(attachmentHistory);
        setConversationId(conversation.id);
        setInfrastructureRequestId(null);
        setConnectionStatus("connecting");
        setError("");
      } catch (err) {
        if (!cancelled) {
          setConnectionStatus("disconnected");
          setError(
            err instanceof Error
              ? err.message
              : "Unable to load the conversation.",
          );
        }
      }
    }

    void loadConversation();

    return () => {
      cancelled = true;
    };
  }, []);

  // Scroll to the newest message.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Clean up the socket and reconnect timer on unmount.
  useEffect(() => {
    mountedRef.current = true;

    return () => {
      mountedRef.current = false;

      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }

      socketRef.current?.close(1000, "Component unmounted");
      socketRef.current = null;
    };
  }, []);

  // Connect and authenticate the WebSocket for the current conversation.
  useEffect(() => {
    const sessionId = sessionIdRef.current;

    if (!conversationId || !sessionId) return;

    let stopped = false;
    let socket: WebSocket | null = null;

    function connect() {
      if (stopped) return;

      const token = getAccessToken();

      if (!token) {
        setConnectionStatus("disconnected");
        setError("Your session has expired. Please sign in again.");
        setLoading(false);
        return;
      }

      setConnectionStatus("connecting");

      const wsBase = API_BASE_URL.replace(/^http:/, "ws:").replace(
        /^https:/,
        "wss:",
      );

      socket = new WebSocket(
        `${wsBase}/ws/conversations/${encodeURIComponent(sessionId!)}`,
      );

      socketRef.current = socket;

      socket.onopen = () => {
        const currentToken = getAccessToken();

        if (!currentToken) {
          setError("Your session has expired. Please sign in again.");
          socket?.close(4401, "Missing authentication token");
          return;
        }

        socket?.send(
          JSON.stringify({
            type: "authenticate",
            token: currentToken,
          }),
        );
      };

      socket.onmessage = (event: MessageEvent<string>) => {
        let data: SocketEvent;

        try {
          data = JSON.parse(event.data) as SocketEvent;
        } catch {
          setError("Received an invalid WebSocket response.");
          return;
        }

        if (data.type === "connected") {
          retryCountRef.current = 0;
          setConnectionStatus("connected");
          setError("");
          sendPendingMessage();
          return;
        }

        if (data.type === "pong") {
          return;
        }

        if (data.type === "message_saved") {
          addMessages([data.message]);
          pendingContentRef.current = null;
          return;
        }

        if (data.type === "requirement_update") {
          addMessages([data.user_message, data.assistant_message]);
          pendingContentRef.current = null;
          setInput("");
          setLoading(false);
          setError("");
          return;
        }

        if (data.type === "assistant_message") {
          addMessages([data.message]);
          pendingContentRef.current = null;
          setInput("");
          setLoading(false);
          setError("");
          return;
        }

        if (data.type === "requirements_result") {
          const result = data.result;
          const requestId = result?.infrastructure_request_id;

          if (typeof requestId === "number") {
            setInfrastructureRequestId(requestId);
            onInfrastructureRequestIdChange(requestId);
          }

          if (result?.status === "complete") {
            const requirements = result.requirements;

            if (isInfrastructureSpec(requirements)) {
              onRequirementsComplete(requirements);
              setError("");
            } else {
              onRequirementsComplete(null);
              setError(
                "The completed requirements are missing valid fields. Please review the conversation.",
              );
            }

            pendingContentRef.current = null;
            setInput("");
            setLoading(false);
          }

          return;
        }

        if (data.type === "error") {
          pendingContentRef.current = null;
          setLoading(false);
          setError(data.detail);
        }
      };

      socket.onerror = () => {
        if (mountedRef.current) {
          setError("WebSocket connection error. Reconnecting...");
        }
      };

      socket.onclose = (event: CloseEvent) => {
        if (socketRef.current === socket) {
          socketRef.current = null;
        }

        if (stopped) return;

        setConnectionStatus("disconnected");

        if ([4401, 4404, 4408].includes(event.code)) {
          setLoading(false);

          setError(
            event.code === 4404
              ? "This conversation was not found or you do not have access to it. Please start a new request or sign in again."
              : "WebSocket authentication failed or expired. Please sign in again.",
          );

          return;
        }

        const delay = Math.min(
          1000 * 2 ** retryCountRef.current,
          10000,
        );

        retryCountRef.current += 1;
        reconnectTimerRef.current = window.setTimeout(connect, delay);
      };
    }

    connect();

    return () => {
      stopped = true;

      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }

      if (socketRef.current) {
        socketRef.current.close(1000, "WebSocket effect cleanup");
        socketRef.current = null;
      }
    };
  }, [
    conversationId,
    addMessages,
    sendPendingMessage,
    onRequirementsComplete,
  ]);

  // Start an independent conversation for a new infrastructure request.
  async function handleNewRequest() {
    if (loading || creatingConversation || uploading) return;

    if (!getAccessToken()) {
      setError("Please sign in to start a new request.");
      return;
    }

    setCreatingConversation(true);
    setError("");

    try {
      const newSessionId = crypto.randomUUID();

      const newConversation = await createConversation(
        newSessionId,
        "Infrastructure request",
      );

      pendingContentRef.current = null;
      retryCountRef.current = 0;

      sessionStorage.setItem(SESSION_KEY, newSessionId);
      sessionIdRef.current = newSessionId;

      setMessages([]);
      setAttachments([]);
      setInfrastructureRequestId(null);
      onRequirementsComplete(null);
      onInfrastructureRequestIdChange(null);
      setInput("");
      setLoading(false);
      setError("");
      setConnectionStatus("connecting");

      setConversationId(newConversation.id);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to start a new infrastructure request.",
      );
    } finally {
      setCreatingConversation(false);
    }
  }

  function handleSend() {
    const content = input.trim();

    if (!content || loading || creatingConversation) return;

    if (content.length > 50000) {
      setError("Message must be 50,000 characters or fewer.");
      return;
    }

    if (connectionStatus !== "connected") {
      setError("The assistant is not connected yet. Please try again shortly.");
      return;
    }

    pendingContentRef.current = content;
    setLoading(true);
    setError("");

    sendPendingMessage();
  }

  // Upload attachments through REST only.
  // This does not send a WebSocket message or trigger Terraform extraction.
  async function handleFileSelected(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];

    if (!file || !conversationId) return;

    const allowedExtensions = [
      ".pdf",
      ".txt",
      ".csv",
      ".json",
      ".png",
      ".jpg",
      ".jpeg",
      ".docx",
    ];

    const dotIndex = file.name.lastIndexOf(".");
    const extension =
      dotIndex >= 0 ? file.name.slice(dotIndex).toLowerCase() : "";

    if (!allowedExtensions.includes(extension)) {
      setError(
        "Unsupported file type. Allowed: PDF, TXT, CSV, JSON, PNG, JPG, JPEG, DOCX.",
      );
      event.target.value = "";
      return;
    }

    if (file.size === 0 || file.size > 10 * 1024 * 1024) {
      setError("File must be greater than 0 bytes and no larger than 10 MB.");
      event.target.value = "";
      return;
    }

    if (uploading || creatingConversation) {
      event.target.value = "";
      return;
    }

    setUploading(true);
    setError("");

    try {
      await uploadAttachment(conversationId, file);

      const [history, attachmentHistory] = await Promise.all([
        getMessages(conversationId),
        getConversationAttachments(conversationId),
      ]);

      setMessages(history);
      setAttachments(attachmentHistory);
    } catch (err) {
      setError(err instanceof Error ? err.message : "File upload failed.");
    } finally {
      setUploading(false);
      event.target.value = "";
    }
  }

  async function handleDownloadAttachment(attachment: ChatAttachment) {
    if (!conversationId) return;

    try {
      const blob = await downloadAttachment(conversationId, attachment.id);
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");

      link.href = objectUrl;
      link.download = attachment.original_filename;
      document.body.appendChild(link);
      link.click();
      link.remove();

      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "File download failed.");
    }
  }

  function formatFileSize(sizeBytes: number): string {
    if (sizeBytes < 1024) return `${sizeBytes} B`;

    if (sizeBytes < 1024 * 1024) {
      return `${(sizeBytes / 1024).toFixed(1)} KB`;
    }

    return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  return (
    <Paper
      elevation={0}
      sx={{
        p: { xs: 2, md: 3 },
        borderRadius: 3,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
      }}
    >
      <Stack spacing={2}>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={1}
          sx={{
            alignItems: { xs: "stretch", sm: "center" },
            justifyContent: "space-between",
          }}
        >
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            Infrastructure Assistant
          </Typography>

          <Stack
            direction="row"
            spacing={1}
            sx={{ alignItems: "center", justifyContent: "flex-end" }}
          >
            <Chip
              size="small"
              label={
                connectionStatus === "connected"
                  ? "Live"
                  : connectionStatus === "connecting"
                    ? "Connecting"
                    : "Disconnected"
              }
              color={
                connectionStatus === "connected"
                  ? "success"
                  : connectionStatus === "connecting"
                    ? "info"
                    : "default"
              }
            />

            <Button
              variant="outlined"
              size="small"
              onClick={handleNewRequest}
              disabled={
                creatingConversation ||
                uploading ||
                connectionStatus === "connecting"
              }
            >
              {creatingConversation ? "Starting..." : "New Request"}
            </Button>
          </Stack>
        </Stack>

        <Typography color="text.secondary" sx={{ textAlign: "center" }}>
          Describe the infrastructure you want to provision.
        </Typography>

        <Divider />

        {error && (
          <Alert severity="error" onClose={() => setError("")}>
            {error}
          </Alert>
        )}

        <Box
          sx={{
            height: 280,
            overflowY: "auto",
            p: 2,
            bgcolor: "grey.50",
            borderRadius: 2,
          }}
        >
          {messages.length === 0 && (
            <Typography color="text.secondary" sx={{ textAlign: "center" }}>
              Try: "I need a development EC2 instance."
            </Typography>
          )}

          <Stack spacing={1.5}>
            {messages.map((message) => (
              <Box
                key={message.id}
                sx={{
                  display: "flex",
                  justifyContent:
                    message.role === "user" ? "flex-end" : "flex-start",
                }}
              >
                <Box
                  sx={{
                    maxWidth: "85%",
                    p: 1.5,
                    borderRadius: 2,
                    bgcolor:
                      message.role === "user" ? "primary.100" : "white",
                    border: "1px solid",
                    borderColor: "divider",
                    overflowWrap: "anywhere",
                  }}
                >
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{
                      display: "block",
                      textAlign: message.role === "user" ? "right" : "left",
                    }}
                  >
                    {message.role === "user" ? "You" : "Assistant"}
                  </Typography>

                  <MessageRenderer content={message.content} />

                  {attachments
                    .filter(
                      (attachment) => attachment.message_id === message.id,
                    )
                    .map((attachment) => (
                      <Stack
                        key={attachment.id}
                        direction="row"
                        spacing={1}
                        sx={{
                          alignItems: "center",
                          mt: 1,
                          p: 1,
                          border: "1px solid",
                          borderColor: "divider",
                          borderRadius: 1.5,
                          bgcolor: "background.paper",
                        }}
                      >
                        <AttachFileIcon fontSize="small" color="action" />

                        <Box sx={{ minWidth: 0, flex: 1 }}>
                          <Typography variant="body2" noWrap>
                            {attachment.original_filename}
                          </Typography>

                          <Typography variant="caption" color="text.secondary">
                            {formatFileSize(attachment.size_bytes)}
                          </Typography>
                        </Box>

                        <IconButton
                          size="small"
                          aria-label={`Download ${attachment.original_filename}`}
                          title="Download attachment"
                          onClick={() =>
                            void handleDownloadAttachment(attachment)
                          }
                        >
                          <DownloadIcon fontSize="small" />
                        </IconButton>
                      </Stack>
                    ))}
                </Box>
              </Box>
            ))}

            {(loading || creatingConversation) && (
              <Stack
                direction="row"
                spacing={1}
                sx={{ alignItems: "center" }}
              >
                <CircularProgress size={16} />

                <Typography variant="body2" color="text.secondary">
                  {creatingConversation
                    ? "Starting a new request..."
                    : "Gathering requirements..."}
                </Typography>
              </Stack>
            )}

            <div ref={bottomRef} />
          </Stack>
        </Box>

        {uploading && (
          <Box>
            <LinearProgress />
            <Typography variant="caption" color="text.secondary">
              Uploading attachment...
            </Typography>
          </Box>
        )}

        <input
          ref={fileInputRef}
          type="file"
          hidden
          accept=".pdf,.txt,.csv,.json,.png,.jpg,.jpeg,.docx"
          onChange={handleFileSelected}
        />

        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={1}
          sx={{ alignItems: "stretch" }}
        >
          <Button
            variant="outlined"
            startIcon={<AttachFileIcon />}
            onClick={() => fileInputRef.current?.click()}
            disabled={
              !conversationId ||
              uploading ||
              creatingConversation ||
              connectionStatus !== "connected"
            }
            sx={{ minWidth: { xs: "100%", sm: 120 } }}
          >
            Attach
          </Button>

          <TextField
            fullWidth
            multiline
            maxRows={4}
            placeholder="Describe your infrastructure requirements..."
            value={input}
            disabled={
              loading ||
              creatingConversation ||
              connectionStatus !== "connected"
            }
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !event.nativeEvent.isComposing
              ) {
                event.preventDefault();
                handleSend();
              }
            }}
          />

          <Button
            variant="contained"
            endIcon={<SendIcon />}
            onClick={handleSend}
            disabled={
              loading ||
              creatingConversation ||
              !input.trim() ||
              connectionStatus !== "connected"
            }
            sx={{ minWidth: 100 }}
          >
            Send
          </Button>
        </Stack>

        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ textAlign: "center" }}
        >
          Messages and requirement progress are saved to PostgreSQL.
          AI-assisted requirement extraction is enabled, with rule-based
          fallback when the AI service is unavailable.
        </Typography>
      </Stack>

      {infrastructureRequestId !== null && (
        <Typography variant="caption" color="text.secondary">
          Infrastructure Request ID: {infrastructureRequestId}
        </Typography>
      )}
    </Paper>
  );
}
