// src/App.js â€” Azure Operations Agent SPA
import React, { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useIsAuthenticated, useMsal } from "@azure/msal-react";
import { InteractionRequiredAuthError, InteractionStatus } from "@azure/msal-browser";
import { azureManagementLoginRequest } from "./authConfig";
import "./App.css";

const API_BASE = process.env.REACT_APP_API_BASE ?? "";

// â”€â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function renderMarkdown(text) {
  const parts = [];
  const linkRegex = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g;
  let lastIndex = 0;
  let match;
  while ((match = linkRegex.exec(text)) !== null) {
    if (match.index > lastIndex) parts.push(text.slice(lastIndex, match.index));
    parts.push(
      <a key={match.index} href={match[2]} target="_blank" rel="noopener noreferrer"
        style={{ color: "#93c5fd", textDecoration: "underline" }}>{match[1]}</a>
    );
    lastIndex = linkRegex.lastIndex;
  }
  if (lastIndex < text.length) parts.push(text.slice(lastIndex));
  return parts;
}

function CollapsibleCategory({ label, defaultOpen = false, children }) {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  return (
    <div className="question-category">
      <button className="category-toggle" onClick={() => setIsOpen(o => !o)}>
        <label className="sidebar-label">{label}</label>
        <span className={`category-chevron ${isOpen ? "open" : ""}`}>&#9660;</span>
      </button>
      <div className={`category-items ${isOpen ? "open" : ""}`}>
        {children}
      </div>
    </div>
  );
}

function Bubble({ role, text, reportId, imageUri }) {
  const isUser = role === "user";
  const reportUrl = reportId ? `${API_BASE}/reports/${reportId}` : null;

  if (reportId) {
    return (
      <div style={{ margin: "8px 0", display: "flex", justifyContent: "flex-start" }}>
        <div style={{ width: "100%", maxWidth: "100%" }}>
          {text && (
            <div className="bubble assistant" style={{ marginBottom: 8 }}>
              {renderMarkdown(text)}
            </div>
          )}
          <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
            <button className="btn-report" onClick={() => window.open(reportUrl, "_blank")}>
              &#128202; Open Report in New Tab
            </button>
          </div>
          <iframe title="report" src={reportUrl} sandbox="allow-scripts allow-same-origin"
            style={{ width: "100%", height: 600, border: "1px solid var(--border)",
              borderRadius: 12, background: "#0f1117" }} />
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", justifyContent: isUser ? "flex-end" : "flex-start", margin: "8px 0" }}>
      <div className={`bubble ${isUser ? "user" : "assistant"}`}>
        {imageUri && (
          <img src={imageUri} alt="attached" className="bubble-image" />
        )}
        {isUser ? text : renderMarkdown(text)}
      </div>
    </div>
  );
}

// â”€â”€â”€ Quick questions for the chat â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const QUICK_QUESTION_CATEGORIES = [
  {
    label: "Resources & Monitoring",
    questions: [
      "Show me a summary of all my Azure resources",
      "Check the health of all my resources",
      "Find all orphaned and unused resources",
      "What are my top 10 most expensive resources?",
      "Generate a dashboard of my Azure environment",
      "Find unused resources and email the resource owners about them",
    ],
  },
  {
    label: "Cost Analysis",
    questions: [
      "Show cost breakdown by resource group for last 30 days",
      "Show cost breakdown by service for last 30 days",
      "What are my current budgets and how much is remaining?",
      "Show Advisor cost recommendations for my subscription",
    ],
  },
  {
    label: "Policy & Governance",
    questions: [
      "What policies are assigned to my subscription?",
      "Show policy compliance status for my subscription",
      "Create a policy that denies public IP addresses on my resource group",
      "Create a policy to restrict deployments to East US only",
    ],
  },
  {
    label: "Quota Management",
    questions: [
      "List all compute quota limits in East US",
      "What is my current quota for H100 GPUs in West US 3?",
      "Request a quota increase for Standard NCads H100 v5 to 40 vCPUs in West US 3",
      "Show my quota request history for Compute in East US",
    ],
  },
  {
    label: "Support Requests",
    questions: [
      "List my open support tickets",
      "Show available Azure support services",
      "Create a support ticket for a billing issue",
      "Show communications on my latest support ticket",
    ],
  },
];

// â”€â”€â”€ Main App â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export default function App() {
  const { instance, accounts, inProgress } = useMsal();
  const isAuthenticated = useIsAuthenticated();
  const account = useMemo(
    () => instance.getActiveAccount() ?? accounts?.[0] ?? null,
    [instance, accounts]
  );

  const [error, setError] = useState(null);
  const [needsConsent, setNeedsConsent] = useState(false);
  const [accessToken, setAccessToken] = useState(null);

  // View state: "chat" or "dashboard"
  const [view, setView] = useState("chat");

  // Subscription picker
  const [subscriptions, setSubscriptions] = useState([]);
  const [selectedSub, setSelectedSub] = useState("");

  // Chat state
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [messages, setMessages] = useState([]);
  const [streamingText, setStreamingText] = useState("");
  const [pendingImage, setPendingImage] = useState(null); // { dataUri, name }
  const fileInputRef = useRef(null);

  // Dashboard state
  const [dashboardReportId, setDashboardReportId] = useState(null);

  const chatEndRef = useRef(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingText, chatLoading]);

  // â”€â”€â”€ Auth â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  const login = () => {
    setError(null);
    instance.loginRedirect(azureManagementLoginRequest);
  };

  const logout = () => {
    setAccessToken(null);
    setMessages([]);
    setSubscriptions([]);
    setSelectedSub("");
    setDashboardReportId(null);
    instance.logoutRedirect({ account });
  };

  const getToken = useCallback(async () => {
    if (!account) return null;
    try {
      const res = await instance.acquireTokenSilent({ ...azureManagementLoginRequest, account });
      setAccessToken(res.accessToken);
      setNeedsConsent(false);
      return res.accessToken;
    } catch (e) {
      if (e instanceof InteractionRequiredAuthError) {
        setNeedsConsent(true);
        return null;
      }
      setError(e?.message || String(e));
      return null;
    }
  }, [account, instance]);

  const consentAndGetToken = () => {
    instance.acquireTokenRedirect({ ...azureManagementLoginRequest, account });
  };

  // Auto-acquire token after login
  useEffect(() => {
    if (isAuthenticated && account && inProgress === InteractionStatus.None) {
      getToken();
    }
  }, [isAuthenticated, account, inProgress, getToken]);

  // â”€â”€â”€ Subscription loader â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  useEffect(() => {
    if (!accessToken) return;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/subscriptions`, {
          headers: { Authorization: `Bearer ${accessToken}` },
        });
        if (res.ok) {
          const data = await res.json();
          setSubscriptions(data.subscriptions || []);
          if (data.subscriptions?.length === 1) {
            setSelectedSub(data.subscriptions[0].subscription_id);
          }
        }
      } catch { /* ignore */ }
    })();
  }, [accessToken]);

  // ——— Chat: NDJSON streaming ———————————————————————————————————————————

  const sendMessage = async (text) => {
    const msg = text || chatInput.trim();
    if ((!msg && !pendingImage) || chatLoading) return;

    const imageUri = pendingImage?.dataUri || null;

    setError(null);
    setChatInput("");
    setPendingImage(null);
    setChatLoading(true);
    setStreamingText("");
    setMessages((m) => [...m, { role: "user", text: msg || "(image)", imageUri }]);

    let token = accessToken;
    if (!token) token = await getToken();
    if (!token) { setChatLoading(false); return; }

    try {
      const res = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          message: msg || "",
          subscription_id: selectedSub || null,
          image: imageUri || null,
        }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData?.detail || `HTTP ${res.status}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let accumulated = "";
      let fullText = "";
      let reportId = null;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        accumulated += decoder.decode(value, { stream: true });
        const lines = accumulated.split("\n");
        accumulated = lines.pop(); // keep incomplete line

        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const data = JSON.parse(line);
            const rm = data.response_message;
            if (!rm) continue;

            if (rm.type === "AgentRunUpdateEvent" && rm.delta) {
              fullText += rm.delta;
              setStreamingText(fullText);
            } else if (rm.type === "ExecutorInvokedEvent" && rm.delta) {
              fullText += rm.delta;
              setStreamingText(fullText);
            } else if (rm.type === "done") {
              reportId = rm.report_id || null;
              if (rm.result) fullText = rm.result;
            } else if (rm.type === "error") {
              setError(rm.message || "Agent error");
            }
          } catch { /* skip malformed lines */ }
        }
      }

      // Add final message
      setMessages((m) => [...m, { role: "assistant", text: fullText, reportId }]);
      setStreamingText("");

      // If report was returned, also set it as dashboard
      if (reportId) setDashboardReportId(reportId);

    } catch (e) {
      setError(e?.message || String(e));
    } finally {
      setChatLoading(false);
    }
  };

  const clearChat = async () => {
    setMessages([]);
    setStreamingText("");
    setDashboardReportId(null);
    if (accessToken) {
      fetch(`${API_BASE}/chat/clear`, {
        method: "POST",
        headers: { Authorization: `Bearer ${accessToken}` },
      }).catch(() => {});
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const handlePaste = (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
      if (item.type.startsWith("image/")) {
        e.preventDefault();
        const file = item.getAsFile();
        if (!file) return;
        if (file.size > 10 * 1024 * 1024) {
          setError("Image must be under 10 MB.");
          return;
        }
        const reader = new FileReader();
        reader.onload = () => {
          setPendingImage({ dataUri: reader.result, name: "pasted-image" });
        };
        reader.readAsDataURL(file);
        return;
      }
    }
  };

  const handleImageSelect = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setError("Only image files are supported.");
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      setError("Image must be under 10 MB.");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setPendingImage({ dataUri: reader.result, name: file.name });
    };
    reader.readAsDataURL(file);
    e.target.value = "";
  };

  // â”€â”€â”€ Render â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  if (!isAuthenticated) {
    return (
      <div className="login-container">
        <div className="login-card">
          <div className="login-icon">&#9729;</div>
          <h1>Azure Operations Agent</h1>
          <p>Monitor, manage, and analyze your Azure resources through an intelligent chat interface.</p>
          <button className="btn-primary" onClick={login}>Sign in with Microsoft</button>
        </div>
      </div>
    );
  }

  return (
    <div className="app-container">
      {/* â”€â”€â”€ Sidebar â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */}
      <aside className="sidebar">
        <div className="sidebar-header">
          <span className="sidebar-icon">&#9729;</span>
          <span className="sidebar-title">Azure Ops</span>
        </div>

        <nav className="sidebar-nav">
          <button className={`nav-btn ${view === "chat" ? "active" : ""}`}
            onClick={() => setView("chat")}>
            &#128172; Chat
          </button>
          <button className={`nav-btn ${view === "dashboard" ? "active" : ""}`}
            onClick={() => setView("dashboard")}>
            &#128202; Dashboard
          </button>
        </nav>

        {subscriptions.length > 0 && (
          <div className="sidebar-section">
            <label className="sidebar-label">Subscription</label>
            <select className="sidebar-select" value={selectedSub}
              onChange={(e) => setSelectedSub(e.target.value)}>
              <option value="">Select...</option>
              {subscriptions.map((s) => (
                <option key={s.subscription_id} value={s.subscription_id}>
                  {s.display_name}
                </option>
              ))}
            </select>
          </div>
        )}

        <div className="sidebar-section sidebar-questions">
          {QUICK_QUESTION_CATEGORIES.map((cat, ci) => (
            <CollapsibleCategory key={ci} label={cat.label} defaultOpen={ci === 0}>
              {cat.questions.map((q, qi) => (
                <button key={qi} className="quick-btn"
                  disabled={chatLoading}
                  onClick={() => { setView("chat"); sendMessage(q); }}>
                  {q}
                </button>
              ))}
            </CollapsibleCategory>
          ))}
        </div>

        <div className="sidebar-footer">
          <div className="user-info">
            <span className="user-avatar">
              {(account?.name || account?.username || "U")[0].toUpperCase()}
            </span>
            <span className="user-name">{account?.name || account?.username}</span>
          </div>
          <button className="btn-logout" onClick={logout}>Sign out</button>
        </div>
      </aside>

      {/* â”€â”€â”€ Main content â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */}
      <main className="main-content">
        {error && (
          <div className="error-bar">
            <span>{error}</span>
            <button onClick={() => setError(null)} className="error-close">&times;</button>
          </div>
        )}

        {needsConsent && (
          <div className="consent-bar">
            <span>Additional consent required.</span>
            <button className="btn-primary btn-sm" onClick={consentAndGetToken}>Grant consent</button>
          </div>
        )}

        {/* â”€â”€â”€ Chat View â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */}
        {view === "chat" && (
          <div className="chat-container">
            <div className="chat-header">
              <h2>Azure Operations Chat</h2>
              <button className="btn-ghost" onClick={clearChat}>Clear chat</button>
            </div>

            <div className="chat-messages">
              {messages.length === 0 && !streamingText && (
                <div className="chat-empty">
                  <div className="chat-empty-icon">&#9729;</div>
                  <h3>Ask me about your Azure resources</h3>
                  <p>I can help you monitor performance, find unused resources, analyze costs, and generate visual reports.</p>
                </div>
              )}

              {messages.map((m, i) => (
                <Bubble key={i} role={m.role} text={m.text} reportId={m.reportId} imageUri={m.imageUri} />
              ))}

              {streamingText && (
                <Bubble role="assistant" text={streamingText} />
              )}

              {chatLoading && !streamingText && (
                <div className="typing-indicator">
                  <span></span><span></span><span></span>
                </div>
              )}

              <div ref={chatEndRef} />
            </div>

            <div className="chat-input-bar">
              {pendingImage && (
                <div className="image-preview">
                  <img src={pendingImage.dataUri} alt={pendingImage.name} />
                  <button className="image-preview-remove" onClick={() => setPendingImage(null)}>&times;</button>
                </div>
              )}
              <div className="chat-input-row">
                <input
                  type="file"
                  ref={fileInputRef}
                  accept="image/*"
                  style={{ display: "none" }}
                  onChange={handleImageSelect}
                />
                <button
                  className="btn-attach"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={chatLoading}
                  title="Attach image"
                >
                  &#128206;
                </button>
                <textarea
                  className="chat-input"
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  onPaste={handlePaste}
                  placeholder={selectedSub ? "Ask about your Azure resources..." : "Select a subscription first, or ask a general question..."}
                  rows={1}
                  disabled={chatLoading}
                />
                <button className="btn-send" onClick={() => sendMessage()} disabled={chatLoading || (!chatInput.trim() && !pendingImage)}>
                  &#10148;
                </button>
              </div>
            </div>
          </div>
        )}

        {/* â”€â”€â”€ Dashboard View â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */}
        {view === "dashboard" && (
          <div className="dashboard-container">
            <div className="dashboard-header">
              <h2>Dashboard</h2>
              <button className="btn-primary btn-sm" disabled={chatLoading}
                onClick={() => {
                  setView("chat");
                  sendMessage("Generate a comprehensive dashboard of my Azure environment showing resources, costs, and health");
                }}>
                &#8635; Generate Dashboard
              </button>
            </div>

            {dashboardReportId ? (
              <iframe title="dashboard" src={`${API_BASE}/reports/${dashboardReportId}`}
                sandbox="allow-scripts allow-same-origin"
                className="dashboard-iframe" />
            ) : (
              <div className="dashboard-empty">
                <div className="chat-empty-icon">&#128202;</div>
                <h3>No dashboard generated yet</h3>
                <p>Use the chat to ask for a dashboard or click "Generate Dashboard" above.</p>
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
