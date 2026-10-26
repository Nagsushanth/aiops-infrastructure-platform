
import { useCallback, useEffect, useRef, useState } from "react";
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
} from "@mui/material";
import SendIcon from "@mui/icons-material/Send";
import {
  createConversation,
  getAccessToken,
  getConversationBySession,
  getMessages,
  type ChatMessage,
} from "./api";

const SESSION_KEY = "aiops-conversation-session";
const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

type ConnectionStatus = "connecting" | "connected" | "disconnected";

type SavedMessage = ChatMessage;

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

export default function ChatPanel() {
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [infrastructureRequestId, setInfrastructureRequestId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [creatingConversation, setCreatingConversation] = useState(false);
  const [error, setError] = useState("");
  const [connectionStatus, setConnectionStatus] =
    useState<ConnectionStatus>("connecting");

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
              // The saved session is unavailable; create a new one.
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

        const history = await getMessages(conversation.id);

        if (cancelled) return;

        setMessages(history);
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

          // The backend has accepted the message. Avoid resending it.
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
          const requestId = data.result?.infrastructure_request_id;

          if (typeof requestId === "number") {
            setInfrastructureRequestId(requestId);
          }

          if (data.result?.status === "complete") {
            pendingContentRef.current = null;
            setInput("");
            setLoading(false);
            setError("");
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

        // Do not retry authentication or conversation errors indefinitely.
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
  }, [conversationId, addMessages, sendPendingMessage]);

  // Start an independent conversation for a new infrastructure request.
  async function handleNewRequest() {
    if (loading || creatingConversation) return;

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

      // Prevent an earlier unsent message from reaching the new conversation.
      pendingContentRef.current = null;
      retryCountRef.current = 0;

      // Save the new session so a page refresh restores this conversation.
      sessionStorage.setItem(SESSION_KEY, newSessionId);
      sessionIdRef.current = newSessionId;

      // Clear the previous conversation and its request ID.
      setMessages([]);
      setInfrastructureRequestId(null);
      setInput("");
      setLoading(false);
      setError("");
      setConnectionStatus("connecting");

      // This changes the conversation ID and reconnects the WebSocket.
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

                  <Typography sx={{ whiteSpace: "pre-wrap" }}>
                    {message.content}
                  </Typography>
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

        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={1}
          sx={{ alignItems: "stretch" }}
        >
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
          Requirement gathering is currently rule-based; OpenAI integration is
          not enabled yet.
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

