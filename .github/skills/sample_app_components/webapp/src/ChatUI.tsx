import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Input } from "@/components/ui/input";
import { API } from "./api";
import { SafeHTML, sanitizeHtml, isHtml, hasTable } from "@/components/ui/safehtml";
import { ChevronDown, Settings } from "lucide-react";


interface Message {
  id: string; // NEW: stable key
  role: "user" | "assistant";
  content: string;
  parts?: {
    final: string;   // only MagenticFinalResultEvent
    stream: string;  // everything else
  };
  isTypingPlaceholder?: boolean;
  isRunLogCollapsed?: boolean; // NEW: collapsible state per message
}

interface AgentSetting {
  id: string;
  agent_name: string;
  agent_instructions: string;
}


function TypingBubble() {
  return (
    <div className="inline-flex items-center gap-2 rounded-2xl px-4 py-2 bg-slate-700 text-slate-300 ring-1 ring-slate-600 shadow-sm">
      <span className="animate-pulse-fade">thinking...</span>
      <span className="typing-dots">
        <span className="dot" />
        <span className="dot" />
        <span className="dot" />
      </span>
    </div>
  );
}

export default function ChatUI() {
  const [messages, setMessages] = useState<Message[]>(() => [
    { id: crypto.randomUUID(), role: "assistant", content: "Hi! How can I help you today?" },
  ]);

  const MODE_OPTIONS = [
    { value: "graph",   label: "Magentic Graph Search" }
    //{ value: "magentic", label: "Magentic" },
    //{ value: "singleagent", label: "Single Agent" },
    //{ value: "handoff",    label: "Handoff" },
    
  ];
  const GRAPH_OPTIONS = [
    { value: "customer_graph", label: "customer_graph" },
    { value: "meetings_graph", label: "meetings_graph" },
    { value: "meetings_graph_v2", label: "meetings_graph_v2" },
  ];
  const [mode, setMode] = useState<string>(MODE_OPTIONS[0].value);
  const [selectedGraph, setSelectedGraph] = useState<string>(GRAPH_OPTIONS[0].value);
  const [isTyping, setIsTyping] = useState(false);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const [input, setInput] = useState("");
  const [user_id] = useState(() => crypto.randomUUID());
  const [progressPct, setProgressPct] = useState<number | null>(null);
  const [clientId, setClientId] = useState<string | null>(null);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [faqs, setFaqs] = useState<string[]>([]);
  const [selectedFaq, setSelectedFaq] = useState("");
  const [selectedModel, setSelectedModel] = useState("gpt-4.1");
  const [agentSettings, setAgentSettings] = useState<AgentSetting[]>([
    { id: crypto.randomUUID(), agent_name: "", agent_instructions: "" },
  ]);
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const assistantIndexRef = useRef<number | null>(null);

  function addAgentSetting() {
    setAgentSettings((prev) => [
      ...prev,
      { id: crypto.randomUUID(), agent_name: "", agent_instructions: "" },
    ]);
  }

  function updateAgentSetting(
    id: string,
    key: "agent_name" | "agent_instructions",
    value: string
  ) {
    setAgentSettings((prev) =>
      prev.map((agent) => (agent.id === id ? { ...agent, [key]: value } : agent))
    );
  }

  function appendToAssistant(text: string) {
  setMessages(prev => {
    const idx = (assistantIndexRef.current ?? prev.length - 1);
    const msg = prev[idx];
    if (!msg || msg.role !== "assistant") return prev;
    const next = [...prev];
    next[idx] = { ...msg, content: msg.content + text, isTypingPlaceholder: false };
    return next;
  });
}

/** Consume application/x-ndjson stream and emit parsed objects */
async function readNdjsonStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (evt: any) => void
) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    // handle CRLF/LF and partial lines
    let nl: number;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line) continue;
      try {
        onEvent(JSON.parse(line));
      } catch (e) {
        // If the backend ever logs non-JSON lines, just ignore them.
        console.warn("Bad NDJSON line:", line);
      }
    }
  }

  // flush any trailing line w/o newline
  const leftover = buf.trim();
  if (leftover) {
    try { onEvent(JSON.parse(leftover)); } catch {}
  }
}

function appendToAssistantFinal(text: string) {
  setMessages(prev => {
    const idx = (assistantIndexRef.current ?? prev.length - 1);
    const msg = prev[idx];
    if (!msg || msg.role !== "assistant") return prev;

    const parts = msg.parts ?? { final: "", stream: "" };
    const nextFinal = (parts.final ?? "") + text;

    const next = [...prev];
    next[idx] = {
      ...msg,
      isTypingPlaceholder: false,
      parts: { ...parts, final: nextFinal },
      // keep content as a simple concat for legacy render paths
      content: `${nextFinal}${parts.stream ?? ""}`,
    };
    return next;
  });
}

function appendToAssistantStream(text: string) {
  if (!text) return;
  setMessages(prev => {
    const idx = (assistantIndexRef.current ?? prev.length - 1);
    const msg = prev[idx];
    if (!msg || msg.role !== "assistant") return prev;

    const parts = msg.parts ?? { final: "", stream: "" };
    const nextStream = (parts.stream ?? "") + text;

    const next = [...prev];
    next[idx] = {
      ...msg,
      isTypingPlaceholder: false,
      parts: { ...parts, stream: nextStream },
      content: `${parts.final ?? ""}${nextStream}`,
    };
    return next;
  });
}

  
  // smooth scroll to bottom on updates
  const scrollToBottom = () => {
    const el = viewportRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  };
  useEffect(() => {
    scrollToBottom();
  }, [messages, isTyping]);

  // autosize textarea
  const autosize = () => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  };
  useEffect(() => {
    autosize();
  }, [input]);

  useEffect(() => {
    let mounted = true;
    setFaqs([]);
    setSelectedFaq("");

    const loadFaqs = async () => {
      try {
        const res = await fetch(API.getFaqs(selectedGraph));
        if (!res.ok) {
          if (mounted) setFaqs([]);
          return;
        }
        const data = await res.json();
        const items = Array.isArray(data?.faqs)
          ? data.faqs.filter((item: unknown) => typeof item === "string")
          : [];
        if (mounted) {
          setFaqs(items);
          setSelectedFaq("");
        }
      } catch {
        if (mounted) setFaqs([]);
      }
    };

    loadFaqs();
    return () => {
      mounted = false;
    };
  }, [selectedGraph]);

  // SSE wire-up
  useEffect(() => {
    const es = new EventSource(`${API.sseEvents}?sid=${user_id}`);

    es.addEventListener("open", (e: MessageEvent) => {
      try {
        const { client_id } = JSON.parse(e.data ?? "{}");
        setClientId(client_id ?? null);
      } catch {}
    });

    es.onmessage = (e: MessageEvent) => {
      console.log("SSE message:", e.data);
    };

    es.addEventListener("progress", (e: MessageEvent<string>) => {
      try {
        const msg = JSON.parse(e.data);
        const raw = msg?.progress ?? msg?.params?.progress;
        const pct =
          typeof raw === "number"
            ? Math.round(raw * 100)
            : Number.isFinite(Number(raw))
            ? Math.round(Number(raw) * 100)
            : null;
        if (pct !== null) setProgressPct(pct);
      } catch (err) {
        console.error("Bad JSON in SSE progress event:", err, e.data);
      }
    });

    es.addEventListener("assistant", (e: MessageEvent) => {
      try {
        const root = JSON.parse(e.data);
        const level = root?.params?.level ?? "info";
        const texts: string[] = (root?.params?.data ?? [])
          .filter((d: any) => d?.type === "text" && typeof d?.text === "string")
          .map((d: any) => d.text);
        const text = texts.join(" ").trim() || "(message)";
        const prefix = level === "error" ? "❌ " : level === "warn" ? "⚠️ " : "";
        setMessages((prev) => [...prev, { id: crypto.randomUUID(), role: "assistant", content: `${prefix}${text}` },]);
      } catch {}
    });

    es.onerror = () => {};
    return () => es.close();
  }, [user_id]);

  function toggleRunLog(id: string) {
    setMessages(prev =>
      prev.map(m =>
        m.id === id ? { ...m, isRunLogCollapsed: !m.isRunLogCollapsed } : m
      )
    );
  }
  
  // send handler
  const handleSendMessage = async (rawText: string) => {
  if (!rawText.trim() || isTyping) return;

  const text = rawText.trim();
  setInput("");
  setIsTyping(true);
  setProgressPct(0);

  // Add user message + a placeholder assistant bubble
  setMessages(prev => {
    const idx = prev.length + 1; // user message + then assistant bubble
    assistantIndexRef.current = idx;
    return [
      ...prev,
      { id: crypto.randomUUID(), role: "user", content: text },
      {
        id: crypto.randomUUID(),
        role: "assistant",
        content: "",
        isTypingPlaceholder: true,
        isRunLogCollapsed: false,   // <-- default expanded
        parts: { final: "", stream: "" },
      },
    ];
  });

  

  // new request controller
  controllerRef.current?.abort();
  const ctrl = new AbortController();
  controllerRef.current = ctrl;

  try {
    const modeForApi = (mode || MODE_OPTIONS[0].value).toLowerCase();
    const res = await fetch(API.startConversation(user_id, modeForApi), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/x-ndjson",
      },
      body: JSON.stringify({
        user_query: text,
        client_id: clientId,
        graph_name: selectedGraph,
        model_name: selectedModel,
      }),
      signal: ctrl.signal,
    });
    if (!res.ok || !res.body) {
      throw new Error(`HTTP ${res.status}`);
    }

    // optionally show "started"
    setProgressPct(p => (p === null ? 1 : Math.max(p, 1)));

    await readNdjsonStream(res.body, (obj) => {
      // New backend shape: {"response_message": {...}} plus a trailing {"response_message": {"type":"done", ...}}
      const payload = obj?.response_message ?? obj;
      const t = payload?.type as string | undefined;
      const delta = typeof payload?.delta === "string" ? payload.delta : "";
      const errorMessage = typeof payload?.message === "string" ? payload.message : "";

      if (!t) return;

      if (t === "WorkflowFinalResultEvent") {
        appendToAssistantFinal(delta);
      } else if (t === "error") {
        appendToAssistant(`⚠️ ${errorMessage || "Workflow failed."}`);
        setIsTyping(false);
        setProgressPct(null);
        return;
      } else if (t === "done") {
        setIsTyping(false);
        setProgressPct(null);
        return;
      } else {
        // Everything else goes into the Run log
        appendToAssistantStream(delta);
      }

      // keep the bar feeling alive
      setProgressPct((p) => (p == null ? 5 : Math.min(p + 1, 95)));
    });


    // If the stream ended without an explicit "done"
    setIsTyping(false);
    setProgressPct(null);
  } catch (err: any) {
    const msg = err?.name === "AbortError" ? "Request was canceled." : (err?.message ?? "Unknown error");
    // Replace the placeholder with the error text if it’s still there
    appendToAssistant(`⚠️ Error fetching reply: ${msg}`);
    setIsTyping(false);
    setProgressPct(null);
  } finally {
    if (controllerRef.current === ctrl) controllerRef.current = null;
  }
};

  const handleSend = async () => {
    await handleSendMessage(input);
  };


  // enter to send, shift+enter for newline
  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div
      className="
        h-full min-h-0 w-full text-slate-200
        grid grid-rows-[auto_1fr_auto]
      "
    >
      {/* Header */}
      <header className="sticky top-0 z-20 border-b border-slate-700 bg-slate-800/90 backdrop-blur supports-[backdrop-filter]:bg-slate-800/90 w-full">
        <div className="px-4 py-3 flex items-center justify-between w-full">
        
          <div className="text-xs text-slate-400 whitespace-nowrap flex-shrink-0">
            User ID: <span className="font-mono text-slate-300">{user_id}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-400 hidden sm:inline">FAQs</span>
            <select
              value={selectedFaq}
              onChange={async (e) => {
                const faq = e.target.value;
                setSelectedFaq(faq);
                if (faq) {
                  await handleSendMessage(faq);
                  setSelectedFaq("");
                }
              }}
              className="select-dark w-72 rounded-md px-3 py-2 text-sm outline-none
                        focus:ring-2 focus:ring-blue-500/40"
              aria-label="FAQs"
              disabled={isTyping || faqs.length === 0}
            >
              <option value="">{faqs.length ? "Select FAQ" : "No FAQs available"}</option>
              {faqs.map((faq) => (
                <option key={faq} value={faq}>
                  {faq}
                </option>
              ))}
            </select>
            <span className="text-xs text-slate-400 hidden sm:inline">Model</span>
            <select
              value={selectedModel}
              onChange={(e) => setSelectedModel(e.target.value)}
              className="select-dark w-44 rounded-md px-3 py-2 text-sm outline-none
                        focus:ring-2 focus:ring-blue-500/40"
              aria-label="Model"
              disabled={isTyping}
            >
              <option value="gpt-4.1">gpt-4.1</option>
              <option value="gpt-4.1-mini">gpt-4.1-mini</option>
              <option value="gpt-5.4-mini">gpt-5.4-mini</option>
            </select>
            <span className="text-xs text-slate-400 hidden sm:inline">Graph</span>
            <select
              value={selectedGraph}
              onChange={(e) => setSelectedGraph(e.target.value)}
              className="select-dark w-44 rounded-md px-3 py-2 text-sm outline-none
                        focus:ring-2 focus:ring-blue-500/40"
              aria-label="Graph"
              disabled={isTyping}
            >
              {GRAPH_OPTIONS.map((graph) => (
                <option key={graph.value} value={graph.value}>
                  {graph.label}
                </option>
              ))}
            </select>
            <span className="text-xs text-slate-400 hidden sm:inline">Orchestration</span>
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value)}
              className="select-dark w-44 rounded-md px-3 py-2 text-sm outline-none
                        focus:ring-2 focus:ring-blue-500/40"
              aria-label="Orchestration"
            >
              {MODE_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
            <Button
              type="button"
              variant="outline"
              className="h-9 rounded-md border-slate-600 bg-slate-700 text-slate-200 hover:bg-slate-600"
              onClick={() => setIsSettingsOpen(true)}
              aria-label="Open settings"
            >
              <Settings className="h-4 w-4" aria-hidden="true" />
              <span>Settings</span>
            </Button>
          </div>
        </div>
        {progressPct !== null && (
          <div className="h-1 bg-slate-700">
            <div
              className="h-1 w-0 transition-[width] duration-200 bg-blue-500"
              style={{ width: `${Math.min(Math.max(progressPct, 0), 100)}%` }}
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={progressPct ?? 0}
            />
          </div>
        )}
      </header>

      {/* Chat Panel */}
      <main className="w-full px-0 py-0">
        <Card className="relative rounded-none border-0 bg-slate-800 shadow-none w-full">
          <CardContent className="p-0 w-full">
            <ScrollArea className="h-[calc(100dvh-260px)] w-full" type="always">

              <div
                className="p-1 space-y-1 w-full"
                ref={(el) => {
                  if (!el) return;
                  setTimeout(() => {
                    const viewport = el.closest("[data-radix-scroll-area-viewport]") as HTMLDivElement | null;
                    if (viewport) viewportRef.current = viewport;
                  }, 0);
                }}
              >
                {messages.map((msg) => {
                  const isUser = msg.role === "user";
                  const clean = sanitizeHtml(msg.content);
                  const tableMode = isHtml(clean) && hasTable(clean);
                  const isEmptyAssistantPlaceholder = !isUser && msg.isTypingPlaceholder && !msg.content.trim();
                  if (isEmptyAssistantPlaceholder) {
                    // Don’t render a bubble yet; TypingBubble handles the UX.
                    return null;
                  }
                  return (
                    <div
                        key={msg.id}
                        className="flex w-full min-w-0 items-start gap-2"
                      >
                      {/* Left avatar slot (48px). Show only for assistant messages. */}
                      {!isUser && (
                        <div className="w-12 flex-shrink-0 flex justify-start">
                          <div className="h-9 w-9 rounded-full bg-blue-600 ring-1 ring-blue-500/30 shadow-sm flex items-center justify-center text-white text-xs font-semibold">AI</div>
                        </div>
                      )}

                      {/* Bubble column (shrinks safely) */}
                      <div
                          className={[
                            "min-w-0 flex-1 flex",                     // ⬅️ can shrink
                            isUser ? "justify-end" : "justify-start",
                          ].join(" ")}
                        >
                          <div
                            className={[
                              "relative isolate rounded-2xl px-4 py-3 shadow-lg ring-1 text-left",
                              "inline-flex items-start min-w-0",       // ⬅️ can shrink
                              isUser ? "max-w-[85vw] md:max-w-[88%]" : "max-w-full",
                              isUser
                                ? "bg-slate-600 text-slate-100 ring-slate-500/30"
                                : "bg-slate-700 text-slate-100 ring-slate-600",
                            ].join(" ")}
                          >


                            {msg.parts ? (
                              // ===== Split Assistant Bubble =====
                              <div className="w-full space-y-0">
                                {/* Answer (MagenticFinalResultEvent only) */}
                                <div>
                                  <div className="text-[11px] uppercase tracking-wide text-slate-400 mb-1">Answer</div>
                                  {(() => {
                                    const finalRaw = msg.parts!.final ?? "";
                                    const finalClean = sanitizeHtml(finalRaw);
                                    const finalHasTable = isHtml(finalClean) && hasTable(finalClean);

                                    if (!finalRaw.trim()) {
                                      return <div className="opacity-70">…</div>;
                                    }

                                    return finalHasTable ? (
                                      <div
                                        className="w-full max-w-full overflow-x-auto overscroll-x-contain pb-1"
                                        style={{ WebkitOverflowScrolling: "touch", scrollbarGutter: "stable" }}
                                        role="region"
                                        aria-label="Answer table"
                                      >
                                        <div
                                          className="
                                            inline-block w-max align-top
                                            [&_table]:w-max [&_table]:max-w-none [&_table]:min-w-[36rem]
                                            [&_thead_th]:text-left [&_thead_th]:font-semibold [&_thead_th]:px-3 [&_thead_th]:py-2
                                            [&_tbody_td]:px-3 [&_tbody_td]:py-2 [&_tbody_td]:align-top
                                            [&_td]:whitespace-nowrap
                                            [&_code]:break-all [&_a]:break-all
                                          "
                                        >
                                          <SafeHTML html={finalClean} />
                                        </div>
                                      </div>
                                    ) : (
                                      <div className="text-[15px] md:text-base leading-relaxed whitespace-pre-wrap break-words max-w-[70ch]">
                                        {isHtml(finalClean) ? <SafeHTML html={finalClean} /> : <span>{finalRaw}</span>}
                                      </div>
                                    );
                                  })()}
                                </div>

                                {/* Run log (everything else) */}
                                <div className="mt-3">
                                  <button
                                    type="button"
                                    onClick={() => toggleRunLog(msg.id)}
                                    className="group inline-flex items-center gap-2 text-left text-[12px] font-medium text-slate-400 hover:text-slate-200 transition-colors"
                                    aria-expanded={!msg.isRunLogCollapsed}
                                    aria-controls={`runlog-${msg.id}`}
                                  >
                                    <ChevronDown
                                      className={`h-4 w-4 transition-transform ${msg.isRunLogCollapsed ? "-rotate-90" : "rotate-0"}`}
                                      aria-hidden="true"
                                    />
                                    <span className="uppercase tracking-wide">Run log</span>
                                  </button>

                                  <div
                                    id={`runlog-${msg.id}`}
                                    className="mt-2"
                                    hidden={!!msg.isRunLogCollapsed}
                                  >
                                    {/* DISTINCT BACKGROUND FOR RUN LOG */}
                                    <div className="rounded-xl bg-slate-800 ring-1 ring-slate-600 p-3 overflow-x-auto">
                                      <pre className="whitespace-pre-wrap break-all font-mono text-[13px] leading-snug text-slate-300">
                                        {msg.parts.stream || ""}
                                      </pre>
                                    </div>
                                  </div>
                                </div>

                              </div>
                            ) : (
                              // ===== Legacy single-content bubble (unchanged) =====
                              tableMode ? (
                                <div
                                  className="w-full max-w-full overflow-x-auto overscroll-x-contain pb-1"
                                  style={{ WebkitOverflowScrolling: "touch", scrollbarGutter: "stable" }}
                                  role="region"
                                  aria-label="Table content"
                                >
                                  <div
                                    className="
                                      inline-block w-max align-top
                                      [&_table]:w-max [&_table]:max-w-none [&_table]:min-w-[36rem]
                                      [&_thead_th]:text-left [&_thead_th]:font-semibold [&_thead_th]:px-3 [&_thead_th]:py-2
                                      [&_tbody_td]:px-3 [&_tbody_td]:py-2 [&_tbody_td]:align-top
                                      [&_td]:whitespace-nowrap
                                      [&_code]:break-all [&_a]:break-all
                                    "
                                  >
                                    <SafeHTML html={clean} />
                                  </div>
                                </div>
                              ) : (
                                <div className="text-[15px] md:text-base leading-relaxed whitespace-pre-wrap break-words max-w-[70ch]">
                                  {isHtml(clean) ? <SafeHTML html={clean} /> : <span>{msg.content}</span>}
                                </div>
                              )
                            )}




                          </div>
                        </div>


                      {/* Right avatar slot (48px). Show only for user messages. */}
                      {isUser && (
                        <div className="w-12 flex-shrink-0 flex justify-end">
                          <div className="h-9 w-9 rounded-full bg-slate-600 ring-1 ring-slate-500/40 shadow-sm flex items-center justify-center text-slate-300 text-xs font-semibold">You</div>
                        </div>
                      )}
                    </div>
                  );


                })}

                {/* Typing indicator (outside the array so it never replaces messages) */}
                {isTyping && (
                  <div className="flex w-full min-w-0 items-start gap-2">
                    {/* Left avatar slot */}
                    <div className="w-12 flex-shrink-0 flex justify-start">
                      <div className="h-9 w-9 rounded-full bg-blue-600 ring-1 ring-blue-500/30 shadow-sm flex items-center justify-center text-white text-xs font-semibold">AI</div>
                    </div>
                    <TypingBubble />
                  </div>
                )}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>
      </main>

      {/* Composer */}
      <footer className="border-t border-slate-700 bg-slate-800/90 backdrop-blur supports-[backdrop-filter]:bg-slate-800/90 mb-6 md:mb-10 pb-[max(0.5rem,env(safe-area-inset-bottom))]">
        <div className="px-4 py-4">
          <div className="rounded-2xl border border-slate-600 bg-slate-700 p-2 shadow-lg flex items-end gap-2">
            <textarea
              ref={taRef}
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Type a message…"
              className="flex-1 resize-none bg-transparent outline-none text-slate-100 placeholder:text-slate-400 px-3 py-2 rounded-xl leading-6"
              aria-label="Message"
            />
            <Button
              onClick={handleSend}
              className="rounded-xl px-5 py-2.5 font-medium bg-slate-600 text-slate-100 hover:bg-slate-500 shadow-sm"
            >
              Send
            </Button>
          </div>
        </div>
      </footer>

      {isSettingsOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Agent settings"
        >
          <div className="w-full max-w-3xl rounded-2xl border border-slate-600 bg-slate-800 shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-700 px-5 py-4">
              <h2 className="text-base font-semibold text-slate-100">Agent Settings</h2>
              <Button
                type="button"
                variant="ghost"
                className="text-slate-400 hover:text-slate-100"
                onClick={() => setIsSettingsOpen(false)}
              >
                Close
              </Button>
            </div>

            <div className="max-h-[70vh] overflow-y-auto px-5 py-4 space-y-4">
              {agentSettings.map((agent, index) => (
                <div key={agent.id} className="rounded-xl border border-slate-600 bg-slate-700/50 p-4 space-y-3">
                  <div className="text-xs uppercase tracking-wide text-slate-400">Agent {index + 1}</div>
                  <div className="space-y-1">
                    <label className="text-xs text-slate-300">Agent Name</label>
                    <Input
                      value={agent.agent_name}
                      onChange={(e) => updateAgentSetting(agent.id, "agent_name", e.target.value)}
                      placeholder="Enter agent name"
                      className="bg-slate-700 border-slate-600 text-slate-100"
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="text-xs text-slate-300">Agent Instructions</label>
                    <textarea
                      value={agent.agent_instructions}
                      onChange={(e) => updateAgentSetting(agent.id, "agent_instructions", e.target.value)}
                      placeholder="Enter agent instructions"
                      rows={4}
                      className="w-full resize-y rounded-md border border-slate-600 bg-slate-700 px-3 py-2 text-sm text-slate-100 outline-none focus-visible:ring-2 focus-visible:ring-blue-500/40"
                    />
                  </div>
                </div>
              ))}
            </div>

            <div className="flex items-center justify-between border-t border-slate-700 px-5 py-4">
              <Button
                type="button"
                onClick={addAgentSetting}
                className="rounded-md bg-blue-600 text-white hover:bg-blue-500"
              >
                Add Agent
              </Button>
              <Button
                type="button"
                variant="outline"
                className="border-slate-600 bg-slate-700 text-slate-200 hover:bg-slate-600"
                onClick={() => setIsSettingsOpen(false)}
              >
                Done
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
