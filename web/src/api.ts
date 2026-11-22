import type {
  ApiErrorBody, CreateRunRequest, ErrorResponse, EventsPage, HealthResponse,
  RetestRequest, ReviewRequest, RunView, ScenariosResponse, VoiceSessionRequest,
  VoiceSessionResponse,
} from "./contract";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: ApiErrorBody,
    readonly retryAfterMs = 0,
    readonly unreachable = false,
  ) {
    super(detail.message);
  }
}

function retryAfter(header: string | null): number {
  if (!header) return 0;
  const seconds = Number(header);
  return Math.max(0, Number.isFinite(seconds) ? seconds * 1000 : (Date.parse(header) - Date.now()) || 0);
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { cache: "no-store", credentials: "same-origin", ...options });
  } catch (error) {
    if (options.signal?.aborted) throw error;
    throw new ApiError(0, { code: "offline", message: "The shift API could not be reached.", retryable: true }, 0, true);
  }

  const body = await response.json().catch(() => undefined) as T | ErrorResponse | undefined;
  if (response.ok && body !== undefined) return body as T;

  const status = response.status;
  const apiError = body && typeof body === "object" && "error" in body ? body.error : undefined;
  const unreachable = !apiError && (status >= 500 || response.ok);
  throw new ApiError(status, apiError ?? {
    code: `http_${status}`,
    message: unreachable ? "The shift API could not be reached." : `The shift API returned HTTP ${status}.`,
    retryable: unreachable || status === 429 || status >= 500,
  }, retryAfter(response.headers.get("Retry-After")), unreachable);
}

function post<T>(path: string, body: object, key?: string) {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(key ? { "Idempotency-Key": key } : {}) },
    body: JSON.stringify(body),
  });
}

const runPath = (id: string) => `/runs/${encodeURIComponent(id)}`;

export const api = {
  health: (signal?: AbortSignal) => request<HealthResponse>("/health", { signal }),
  scenarios: (signal?: AbortSignal) => request<ScenariosResponse>("/scenarios", { signal }),
  start: (key: string) => post<RunView>("/runs", {
    candidateId: "monitor-support", suiteId: "probation-v1",
  } satisfies CreateRunRequest, key),
  run: (id: string, signal?: AbortSignal) => request<RunView>(runPath(id), { signal }),
  events: (id: string, after: number, signal?: AbortSignal) =>
    request<EventsPage>(`${runPath(id)}/events?after=${after}`, { signal }),
  retest: (id: string, revisionId: string, key: string) =>
    post<RunView>(`${runPath(id)}/retest`, { revisionId } satisfies RetestRequest, key),
  review: (id: string) => post<RunView>(`${runPath(id)}/review`, { decision: "reviewed" } satisfies ReviewRequest),
  voiceSession: (id: string) =>
    post<VoiceSessionResponse>(`${runPath(id)}/voice-sessions`, { caseId: "monitor-valid" } satisfies VoiceSessionRequest),
};
