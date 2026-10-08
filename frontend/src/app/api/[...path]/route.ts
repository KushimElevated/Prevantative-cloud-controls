// Same-origin proxy to the FastAPI backend. BACKEND_URL is read at runtime so the same build
// works in Docker Compose (http://backend:8000) and locally (http://localhost:8000).
import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const FORWARD_REQUEST_HEADERS = ["authorization", "content-type", "x-correlation-id", "accept"];
const FORWARD_RESPONSE_HEADERS = ["content-type", "content-disposition", "x-correlation-id"];

async function proxy(req: NextRequest, { params }: { params: { path: string[] } }) {
  const backend = process.env.BACKEND_URL ?? "http://localhost:8000";
  const url = new URL(`/api/${params.path.map(encodeURIComponent).join("/")}`, backend);
  url.search = req.nextUrl.search;
  const headers = new Headers();
  for (const h of FORWARD_REQUEST_HEADERS) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  const init: RequestInit = { method: req.method, headers, cache: "no-store" };
  if (!["GET", "HEAD"].includes(req.method)) {
    init.body = await req.arrayBuffer();
  }
  let upstream: Response;
  try {
    upstream = await fetch(url, init);
  } catch {
    return Response.json(
      { error: { code: "BACKEND_UNAVAILABLE", message: "The API is not reachable." } },
      { status: 502 },
    );
  }
  const out = new Headers();
  for (const h of FORWARD_RESPONSE_HEADERS) {
    const v = upstream.headers.get(h);
    if (v) out.set(h, v);
  }
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const DELETE = proxy;
