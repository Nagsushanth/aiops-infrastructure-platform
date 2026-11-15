
import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
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
import SmartToyIcon from "@mui/icons-material/SmartToy";

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
        border: "1px solid #e3eaf3",
        borderRadius: 3,
        overflow: "hidden",
        bgcolor: "#ffffff",
      }}
    >
      {/* Chat header */}
      <Box
        sx={{
          px: { xs: 2, md: 3 },
          py: 2,
          borderBottom: "1px solid #e8edf4",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: 2,
        }}
      >
        <Stack direction="row" spacing={1.5} sx={{ alignItems: "center" }}>
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
            <SmartToyIcon sx={{ fontSize: 27 }} />
          </Box>

          <Box>
            <Typography
              sx={{ fontWeight: 800, color: "#24334d", fontSize: "1.05rem" }}
            >
              Infrastructure Assistant
            </Typography>
            <Typography variant="caption" sx={{ color: "#8491a5" }}>
              Natural-language infrastructure management
            </Typography>
          </Box>
        </Stack>

        <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
          <Chip
            size="small"
            label={
              connectionStatus === "connected"
                ? "Live connection"
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
            variant="outlined"
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
            sx={{
              borderRadius: 2,
              textTransform: "none",
              fontWeight: 700,
            }}
          >
            {creatingConversation ? "Starting..." : "New Request"}
          </Button>
        </Stack>
      </Box>

      {/* Chat description */}
      <Box sx={{ px: { xs: 2, md: 3 }, pt: 2 }}>
        <Typography variant="body2" sx={{ color: "#718096" }}>
          Describe your infrastructure requirements. The assistant will help
          gather the details needed to prepare your infrastructure request.
        </Typography>
      </Box>

      {error && (
        <Box sx={{ px: { xs: 2, md: 3 }, pt: 2 }}>
          <Alert severity="error" onClose={() => setError("")}>
            {error}
          </Alert>
        </Box>
      )}

      {/* Conversation area */}
      <Box
        sx={{
          mx: { xs: 1.5, md: 3 },
          my: 2,
          height: { xs: 380, md: 480 },
          overflowY: "auto",
          p: { xs: 1.5, md: 2.5 },
          bgcolor: "#f7f9fc",
          border: "1px solid #edf0f5",
          borderRadius: 2.5,
        }}
      >
        {messages.length === 0 && (
          <Box
            sx={{
              minHeight: "100%",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              textAlign: "center",
              px: 2,
              py: 3,
            }}
          >
            <Box
              sx={{
                width: 64,
                height: 64,
                borderRadius: 3,
                bgcolor: "#eaf1ff",
                color: "#2864dc",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                mb: 2,
              }}
            >
              <SmartToyIcon sx={{ fontSize: 36 }} />
            </Box>

            <Typography
              variant="h6"
              sx={{ fontWeight: 800, color: "#24334d", mb: 1 }}
            >
              How can I help with your infrastructure?
            </Typography>

            <Typography
              variant="body2"
              sx={{ color: "#8190a5", maxWidth: 440, mb: 2.5 }}
            >
              Describe the cloud resources you need, and we'll work through
              the infrastructure requirements together.
            </Typography>

            <Stack spacing={1} sx={{ width: "100%", maxWidth: 440 }}>
              {[
                "I need a development EC2 instance with 2 vCPUs and 2 GB RAM.",
                "Help me create a secure AWS development environment.",
                "I need a Linux virtual machine for testing.",
              ].map((suggestion) => (
                <Button
                  key={suggestion}
                  variant="outlined"
                  onClick={() => setInput(suggestion)}
                  disabled={connectionStatus !== "connected"}
                  sx={{
                    justifyContent: "flex-start",
                    textAlign: "left",
                    textTransform: "none",
                    borderColor: "#dce5f1",
                    color: "#405572",
                    bgcolor: "#ffffff",
                    borderRadius: 2,
                    py: 1.1,
                    "&:hover": {
                      bgcolor: "#edf3ff",
                      borderColor: "#9db9ed",
                    },
                  }}
                >
                  {suggestion}
                </Button>
              ))}
            </Stack>
          </Box>
        )}

        <Stack spacing={2}>
          {messages.map((message) => {
            const isUser = message.role === "user";

            return (
              <Box
                key={message.id}
                sx={{
                  display: "flex",
                  justifyContent: isUser ? "flex-end" : "flex-start",
                }}
              >
                <Box
                  sx={{
                    width: "fit-content",
                    maxWidth: { xs: "94%", md: "82%" },
                    minWidth: 100,
                    p: 2,
                    borderRadius: isUser
                      ? "16px 16px 4px 16px"
                      : "16px 16px 16px 4px",
                    bgcolor: isUser ? "#e9f1ff" : "#ffffff",
                    border: "1px solid",
                    borderColor: isUser ? "#d5e4ff" : "#e6ebf2",
                    boxShadow: "0 2px 6px rgba(25,45,80,0.025)",
                    overflowWrap: "anywhere",
                  }}
                >
                  <Typography
                    variant="caption"
                    sx={{
                      display: "block",
                      fontWeight: 800,
                      color: isUser ? "#315eac" : "#2864dc",
                      mb: 1,
                    }}
                  >
                    {isUser ? "You" : "Infrastructure Assistant"}
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
                          mt: 1.5,
                          p: 1,
                          border: "1px solid #e3eaf3",
                          borderRadius: 2,
                          bgcolor: "#f8fafc",
                        }}
                      >
                        <AttachFileIcon
                          fontSize="small"
                          sx={{ color: "#64748b" }}
                        />

                        <Box sx={{ minWidth: 0, flex: 1 }}>
                          <Typography variant="body2" noWrap>
                            {attachment.original_filename}
                          </Typography>

                          <Typography
                            variant="caption"
                            sx={{ color: "#8491a5" }}
                          >
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
            );
          })}

          {(loading || creatingConversation) && (
            <Stack
              direction="row"
              spacing={1.2}
              sx={{ alignItems: "center", py: 1 }}
            >
              <CircularProgress size={17} />
              <Typography variant="body2" sx={{ color: "#718096" }}>
                {creatingConversation
                  ? "Starting a new request..."
                  : "Gathering infrastructure requirements..."}
              </Typography>
            </Stack>
          )}

          <div ref={bottomRef} />
        </Stack>
      </Box>

      {/* Attachment upload progress */}
      {uploading && (
        <Box sx={{ px: { xs: 2, md: 3 }, pb: 1 }}>
          <LinearProgress
            sx={{ borderRadius: 2, height: 5, mb: 0.8 }}
          />
          <Typography variant="caption" sx={{ color: "#718096" }}>
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

      {/* Message composer */}
      <Box
        sx={{
          px: { xs: 2, md: 3 },
          pb: 2.5,
          pt: 1,
          borderTop: "1px solid #edf0f5",
        }}
      >
        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={1.2}
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
            sx={{
              minWidth: { xs: "100%", sm: 115 },
              borderRadius: 2,
              textTransform: "none",
              fontWeight: 700,
              borderColor: "#dce5f1",
            }}
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
            sx={{
              "& .MuiOutlinedInput-root": {
                borderRadius: 2,
                bgcolor: "#ffffff",
              },
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
            sx={{
              minWidth: { xs: "100%", sm: 105 },
              borderRadius: 2,
              textTransform: "none",
              fontWeight: 800,
              boxShadow: "none",
              bgcolor: "#2864dc",
              "&:hover": {
                bgcolor: "#1e50b5",
                boxShadow: "none",
              },
            }}
          >
            Send
          </Button>
        </Stack>

        <Typography
          variant="caption"
          sx={{
            display: "block",
            textAlign: "center",
            color: "#94a0b2",
            mt: 1.5,
          }}
        >
          Messages and requirement progress are saved to PostgreSQL.
          AI-assisted requirement extraction is enabled, with rule-based
          fallback when the AI service is unavailable.
        </Typography>

        {infrastructureRequestId !== null && (
          <Typography
            variant="caption"
            sx={{
              display: "block",
              textAlign: "center",
              color: "#64748b",
              mt: 1,
            }}
          >
            Infrastructure Request ID: {infrastructureRequestId}
          </Typography>
        )}
      </Box>
    </Paper>
  );
}
