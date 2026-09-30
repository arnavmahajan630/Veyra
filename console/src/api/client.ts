export const CONTROL = "/api/control";
export const LINEAGE = "/api/lineage";
export const EVIDENCE = "/api/evidence";
export const DEMO = "/api/demo";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const url = new URL(path, window.location.origin);
  const response = await fetch(url, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const data: unknown = await response.json();
      if (data && typeof data === "object" && "detail" in data && typeof data.detail === "string") {
        detail = data.detail;
      }
    } catch {
      // Not JSON: keep the status text.
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function requestBlob(method: string, path: string, body?: unknown): Promise<Blob> {
  const url = new URL(path, window.location.origin);
  const response = await fetch(url, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    throw new ApiError(response.status, response.statusText);
  }
  return await response.blob();
}

async function requestText(path: string): Promise<string> {
  const url = new URL(path, window.location.origin);
  const response = await fetch(url, { method: "GET", credentials: "same-origin" });
  if (!response.ok) throw new ApiError(response.status, response.statusText);
  return await response.text();
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  /** For endpoints that answer `text/plain`, such as the evidence API's PEM public key. */
  text: (path: string) => requestText(path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  blob: (path: string, body?: unknown) => requestBlob("POST", path, body),
};

