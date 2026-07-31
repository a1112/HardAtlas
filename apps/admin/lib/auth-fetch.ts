const authMode = process.env.NEXT_PUBLIC_HARDATLAS_AUTH_MODE ?? "development";

export function adminAuthHeaders(initial?: HeadersInit): Headers {
  const headers = new Headers(initial);
  if (authMode === "development") {
    if (!headers.has("x-hardatlas-dev-principal")) {
      headers.set("x-hardatlas-dev-principal", "atlas-admin-ui");
    }
    if (!headers.has("x-hardatlas-dev-display-name")) {
      headers.set("x-hardatlas-dev-display-name", "Atlas Local Admin");
    }
    if (!headers.has("x-hardatlas-dev-roles")) {
      headers.set("x-hardatlas-dev-roles", "admin");
    }
    return headers;
  }
  return headers;
}

export function adminFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  return fetch(input, {
    ...init,
    headers: adminAuthHeaders(init.headers),
  });
}
