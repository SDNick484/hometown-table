// The only place that talks to the server. Every call sends same-origin credentials (Cloudflare Access's cookie); every
// write also sends X-Hometown: 1 and a JSON object body, which the server's request guard insists on (DELETE
// included). The browser adds the Origin itself.
//
// Failures throw an ApiError with `status`, `error` (the server's message, ready to show) and `data` (the whole JSON
// answer, e.g. { error, duplicate_of } for a 409).

export class ApiError extends Error {
  constructor(status, error, data = null) {
    super(error);
    this.name = "ApiError";
    this.status = status;
    this.error = error;
    this.data = data;
  }
}

export const OFFLINE = "Can't reach the server. Check the phone's connection, or reload the page to sign in again.";
export const SIGNED_OUT = "You're signed out. Reload the page to sign in again; your typing is still here.";

async function request(method, path, body) {
  const headers = { Accept: "application/json" };
  const init = { method, headers, credentials: "same-origin", cache: "no-store" };
  if (method !== "GET") {
    headers["X-Hometown"] = "1";
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body ?? {});
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, OFFLINE);
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (res.ok) {
    if (data === null) throw new ApiError(res.status, "Something went wrong. Reload the page.");
    return data;
  }
  if (res.status === 401) throw new ApiError(401, SIGNED_OUT, data);
  throw new ApiError(res.status, data?.error ?? `Something went wrong (${res.status}). Try again.`, data);
}

export const get = path => request("GET", path);
export const send = (method, path, body) => request(method, path, body);
