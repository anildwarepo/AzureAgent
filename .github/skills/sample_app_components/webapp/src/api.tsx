// src/lib/api.ts

// In production, use relative URLs (nginx proxies to backend)
// In development, use localhost
export const BASE_URL = import.meta.env.DEV
  ? (import.meta.env.VITE_API_BASE_URL || "http://localhost:8080")
  : "";

export const API = {
  sseEvents: `${BASE_URL}/events`,
  startConversation: (user_id: string, mode: string) => `${BASE_URL}/conversation/${user_id}?mode=${encodeURIComponent(mode)}`,
  getFaqs: (graphName: string) => `${BASE_URL}/get_faqs?graph_name=${encodeURIComponent(graphName)}`,
  signupBusiness: (email: string) => `${BASE_URL}/actor/signup/business/email/${encodeURIComponent(email)}`,
  getIndividualByEmail: (email: string) => `${BASE_URL}/party/individual/email/${encodeURIComponent(email)}`,
};

