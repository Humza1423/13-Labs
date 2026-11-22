import { afterEach, expect, test, vi } from "vitest";
import { api, ApiError } from "../src/api";

afterEach(() => vi.unstubAllGlobals());

test("start and retest carry the caller's idempotency keys", async () => {
  const fetcher = vi.fn().mockImplementation(async () => Response.json({}, { status: 202 }));
  vi.stubGlobal("fetch", fetcher);
  await api.start("stable-start");
  await api.retest("run-1", "revision-1", "stable-retest");
  expect(fetcher.mock.calls.map(([url, init]) => [url, init.method, init.headers["Idempotency-Key"], JSON.parse(init.body)])).toEqual([
    ["/api/runs", "POST", "stable-start", {candidateId:"monitor-support", suiteId:"probation-v1"}],
    ["/api/runs/run-1/retest", "POST", "stable-retest", {revisionId:"revision-1"}],
  ]);
});

test.each([
  { status:409, code:"stale_revision", retryable:false, retryAfter:0 },
  { status:429, code:"queue_full", retryable:true, retryAfter:15 },
])("preserves HTTP $status error detail and retry timing", async ({status, code, retryable, retryAfter}) => {
  const detail = {code, message:"Please refresh the run.", retryable};
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({error:detail}, {
    status, headers:{"Retry-After":String(retryAfter)},
  })));
  const failure = await api.start("one").catch(error => error);
  expect(failure).toBeInstanceOf(ApiError);
  expect(failure).toMatchObject({status, detail, retryAfterMs:retryAfter * 1000});
});
