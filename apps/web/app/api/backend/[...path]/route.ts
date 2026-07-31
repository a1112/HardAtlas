import { NextRequest, NextResponse } from "next/server";
import {
  ACCESS_COOKIE,
  REFRESH_COOKIE,
  authMode,
  refreshAccessToken,
  tokenCookieOptions,
} from "../../../../lib/server-oidc";

interface RouteContext {
  params: Promise<{ path: string[] }>;
}

function backendBaseUrl() {
  const value = process.env.HARDATLAS_API_URL;
  if (!value && process.env.NODE_ENV === "production") {
    throw new Error("HARDATLAS_API_URL is required in production");
  }
  return (value ?? "http://localhost:8000").replace(/\/$/, "");
}

function forwardedHeaders(request: NextRequest, accessToken?: string) {
  const headers = new Headers();
  for (const name of [
    "accept",
    "content-type",
    "idempotency-key",
    "x-request-id",
  ]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (authMode() === "development") {
    headers.set("x-hardatlas-dev-principal", "atlas-web-reader");
    headers.set("x-hardatlas-dev-display-name", "Atlas Local Reader");
    headers.set("x-hardatlas-dev-roles", "");
  } else if (accessToken) {
    headers.set("authorization", `Bearer ${accessToken}`);
  }
  return headers;
}

async function proxy(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  if (
    !path.length ||
    path.some((segment) => segment === "." || segment === "..")
  ) {
    return NextResponse.json(
      { detail: "invalid backend path" },
      { status: 400 },
    );
  }
  const backendPath =
    path[0] === "api" && path[1] === "v1" ? path.slice(2) : path;
  const target = new URL(
    `/api/v1/${backendPath.map(encodeURIComponent).join("/")}`,
    backendBaseUrl(),
  );
  target.search = request.nextUrl.search;
  const body =
    request.method === "GET" || request.method === "HEAD"
      ? undefined
      : await request.arrayBuffer();
  let accessToken = request.cookies.get(ACCESS_COOKIE)?.value;
  let refreshToken = request.cookies.get(REFRESH_COOKIE)?.value;
  let refreshed: Awaited<ReturnType<typeof refreshAccessToken>> | undefined;

  if (authMode() !== "development" && !accessToken && refreshToken) {
    try {
      refreshed = await refreshAccessToken(refreshToken);
      accessToken = refreshed.access_token;
      refreshToken = refreshed.refresh_token ?? refreshToken;
    } catch {
      refreshToken = undefined;
    }
  }

  const send = (token?: string) => {
    const init: RequestInit = {
      method: request.method,
      headers: forwardedHeaders(request, token),
      cache: "no-store",
      redirect: "manual",
    };
    if (body !== undefined) init.body = body;
    return fetch(target, init);
  };
  let backendResponse = await send(accessToken);
  if (
    authMode() !== "development" &&
    backendResponse.status === 401 &&
    refreshToken
  ) {
    try {
      await backendResponse.body?.cancel();
      refreshed = await refreshAccessToken(refreshToken);
      accessToken = refreshed.access_token;
      refreshToken = refreshed.refresh_token ?? refreshToken;
      backendResponse = await send(accessToken);
    } catch {
      accessToken = undefined;
      refreshToken = undefined;
    }
  }

  const responseHeaders = new Headers();
  for (const name of ["content-type", "cache-control", "x-request-id"]) {
    const value = backendResponse.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  const response = new NextResponse(backendResponse.body, {
    status: backendResponse.status,
    headers: responseHeaders,
  });
  if (refreshed && accessToken) {
    response.cookies.set(
      ACCESS_COOKIE,
      accessToken,
      tokenCookieOptions(refreshed.expires_in),
    );
    if (refreshToken) {
      response.cookies.set(
        REFRESH_COOKIE,
        refreshToken,
        tokenCookieOptions(refreshed.refresh_expires_in ?? 3600),
      );
    }
  } else if (authMode() !== "development" && !accessToken) {
    response.cookies.delete(ACCESS_COOKIE);
    response.cookies.delete(REFRESH_COOKIE);
  }
  return response;
}

export const GET = proxy;
export const POST = proxy;
export const DELETE = proxy;
