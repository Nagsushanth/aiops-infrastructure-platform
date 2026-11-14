
import { useEffect, useId, useState } from "react";
import type { ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import {
  Box,
  IconButton,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import CheckIcon from "@mui/icons-material/Check";

import "highlight.js/styles/github-dark.css";

function extractText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") {
    return String(node);
  }

  if (Array.isArray(node)) {
    return node.map(extractText).join("");
  }

  if (node && typeof node === "object" && "props" in node) {
    return extractText(
      (node as { props?: { children?: ReactNode } }).props?.children,
    );
  }

  return "";
}

function CodeBlock({
  children,
  language,
  className,
}: {
  children: ReactNode;
  language: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  async function copyCode() {
    try {
      await navigator.clipboard.writeText(extractText(children));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  }

  return (
    <Paper
      variant="outlined"
      sx={{
        my: 1.5,
        overflow: "hidden",
        bgcolor: "#0d1117",
        color: "#e6edf3",
        borderColor: "#30363d",
        borderRadius: 2,
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          px: 1.5,
          py: 0.5,
          borderBottom: "1px solid #30363d",
          bgcolor: "#161b22",
        }}
      >
        <Typography
          variant="caption"
          sx={{ color: "#9da7b3", fontWeight: 600 }}
        >
          {language || "Code"}
        </Typography>

        <Tooltip title={copied ? "Copied" : "Copy code"}>
          <IconButton
            size="small"
            onClick={copyCode}
            sx={{ color: "#c9d1d9" }}
            aria-label="Copy code"
          >
            {copied ? (
              <CheckIcon fontSize="small" />
            ) : (
              <ContentCopyIcon fontSize="small" />
            )}
          </IconButton>
        </Tooltip>
      </Box>

      <Box
        component="pre"
        sx={{
          m: 0,
          p: 2,
          overflowX: "auto",
          fontSize: "0.82rem",
          lineHeight: 1.65,
          "& code": {
            fontFamily: "monospace",
            whiteSpace: "pre",
          },
        }}
      >
        <code
  className={className}
  style={{
    background: "transparent",
    color: "inherit",
    padding: 0,
  }}
>
  {children}
</code>
      </Box>
    </Paper>
  );
}

function MermaidDiagram({ source }: { source: string }) {
  const generatedId = useId();
  const diagramId = `mermaid-${generatedId.replace(/[^a-zA-Z0-9_-]/g, "")}`;

  const [svg, setSvg] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;

    async function renderDiagram() {
      try {
        const mermaidModule = await import("mermaid");
        const mermaid = mermaidModule.default;

        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: "default",
        });

        const result = await mermaid.render(
          diagramId,
          source,
        );

        if (!cancelled) {
          setSvg(result.svg);
          setError("");
        }
      } catch {
        if (!cancelled) {
          setSvg("");
          setError("Unable to render this diagram. Check the Mermaid syntax.");
        }
      }
    }

    void renderDiagram();

    return () => {
      cancelled = true;
    };
  }, [diagramId, source]);

  if (error) {
    return (
      <Box sx={{ my: 1.5 }}>
        <Typography color="error" variant="body2">
          {error}
        </Typography>
                <CodeBlock language="mermaid">{source}</CodeBlock>
      </Box>
    );
  }

  if (!svg) {
    return (
      <Box sx={{ my: 1.5, minHeight: 40 }}>
        <Typography variant="caption" color="text.secondary">
          Rendering diagram...
        </Typography>
      </Box>
    );
  }

  return (
    <Paper
      variant="outlined"
      sx={{
        my: 1.5,
        p: 2,
        overflowX: "auto",
        borderRadius: 2,
        bgcolor: "#ffffff",
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          "& svg": {
            maxWidth: "100%",
            height: "auto",
          },
        }}
        dangerouslySetInnerHTML={{ __html: svg }}
      />
    </Paper>
  );
}

const markdownComponents: Components = {
  code({ className, children, ...props }) {
    const match = /language-([\w-]+)/.exec(className || "");
    const language = match?.[1] || "";
    const source = extractText(children).replace(/\n$/, "");

    if (language === "mermaid") {
      return <MermaidDiagram source={source} />;
    }

    if (language || source.includes("\n")) {
      return (
        <CodeBlock language={language} className={className}>
  {children}
</CodeBlock>
      );
    }

    return (
      <code className={className} {...props}>
        {children}
      </code>
    );
  },

  pre({ children }) {
    return <>{children}</>;
  },

  p({ children }) {
    return (
      <Typography
        component="p"
        variant="body1"
        sx={{ my: 1, lineHeight: 1.7, overflowWrap: "anywhere" }}
      >
        {children}
      </Typography>
    );
  },

  h1({ children }) {
    return <Typography variant="h5" sx={{ mt: 2, mb: 1 }}>{children}</Typography>;
  },

  h2({ children }) {
    return <Typography variant="h6" sx={{ mt: 2, mb: 1 }}>{children}</Typography>;
  },

  h3({ children }) {
    return <Typography variant="subtitle1" sx={{ mt: 1.5, mb: 0.5, fontWeight: 700 }}>{children}</Typography>;
  },

  ul({ children }) {
    return <Box component="ul" sx={{ pl: 3, my: 1 }}>{children}</Box>;
  },

  ol({ children }) {
    return <Box component="ol" sx={{ pl: 3, my: 1 }}>{children}</Box>;
  },

  li({ children }) {
    return (
      <Box component="li" sx={{ mb: 0.5, lineHeight: 1.6 }}>
        {children}
      </Box>
    );
  },

  blockquote({ children }) {
    return (
      <Box
        sx={{
          borderLeft: "4px solid",
          borderColor: "primary.main",
          pl: 2,
          py: 0.5,
          my: 1.5,
          color: "text.secondary",
        }}
      >
        {children}
      </Box>
    );
  },

  table({ children }) {
    return (
      <Box sx={{ width: "100%", overflowX: "auto", my: 1.5 }}>
        <Table size="small">{children}</Table>
      </Box>
    );
  },

  thead({ children }) {
    return <TableHead>{children}</TableHead>;
  },

  tbody({ children }) {
    return <TableBody>{children}</TableBody>;
  },

  tr({ children }) {
    return <TableRow>{children}</TableRow>;
  },

  th({ children }) {
    return (
      <TableCell sx={{ fontWeight: 700, bgcolor: "action.hover" }}>
        {children}
      </TableCell>
    );
  },

  td({ children }) {
    return <TableCell>{children}</TableCell>;
  },

  a({ href, children }) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    );
  },

  hr() {
    return <Box component="hr" sx={{ my: 2, border: 0, borderTop: 1, borderColor: "divider" }} />;
  },
};

export default function MessageRenderer({
  content,
}: {
  content: string;
}) {
  return (
    <Box
      sx={{
        width: "100%",
        minWidth: 0,
        overflowWrap: "anywhere",
        "& > :first-of-type": { mt: 0 },
        "& > :last-child": { mb: 0 },
      }}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: true }]]}
        components={markdownComponents}
      >
        {content}
      </ReactMarkdown>
    </Box>
  );
}