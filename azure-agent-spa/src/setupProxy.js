// Manual proxy configuration for local development.
// CRA picks this up automatically — no import needed.
const { createProxyMiddleware } = require("http-proxy-middleware");

module.exports = function (app) {
  app.use(
    ["/chat", "/subscriptions", "/reports", "/events", "/health"],
    createProxyMiddleware({
      target: "http://localhost:8080",
      changeOrigin: true,
    })
  );
};
