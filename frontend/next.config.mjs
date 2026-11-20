/** Same-origin proxy to the local API: the browser never talks to the API host directly, so the
 * API needs no CORS configuration. Local development only (D-34). */
const API = process.env.AEGIS_UI_API_URL ?? "http://127.0.0.1:8000";

export default {
  reactStrictMode: true,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },
};
