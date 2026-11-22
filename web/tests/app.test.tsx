// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "../src/App";
import { api, ApiError } from "../src/api";
import type { CaseResult, RunView } from "../src/contract";
import { previewFixture } from "../src/fixtures";

const selection = { candidate: previewFixture.run.candidate, scenarios: [] };
const serverRun: RunView = { ...previewFixture.run, id: "server-run" };
const events = previewFixture.events.map(event => ({ ...event, runId: serverRun.id }));

beforeEach(() => {
  history.replaceState(null, "", "/");
  vi.stubGlobal("scrollTo", vi.fn());
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => setTimeout(callback));
  vi.spyOn(api, "scenarios").mockResolvedValue(selection);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("offline starts require an explicit preview choice", async () => {
  vi.mocked(api.scenarios).mockRejectedValue(new ApiError(0, {code:"offline", message:"offline", retryable:true}, 0, true));
  const start = vi.spyOn(api, "start");
  render(<App />);
  const preview = await screen.findByRole("link", { name: "Open design preview" });
  expect((screen.getByRole("button", {name: "Start probation shift"}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(preview);
  fireEvent.click(await screen.findByRole("button", { name: "Start design preview" }));
  expect(location.pathname).toContain(previewFixture.run.id);
  expect(start).not.toHaveBeenCalled();
});

test("evidence pages are drained, deduplicated, and sorted by event sequence", async () => {
  history.replaceState(null, "", "/runs/server-run?view=evidence");
  vi.spyOn(api, "run").mockResolvedValue(serverRun);
  const first = Array.from({ length: 100 }, (_, i) => ({ ...events[0], seq: i + 1, id: `record-${i + 1}` }));
  const last = { ...events[0], seq: 101, id: "record-101" };
  const pages = vi.spyOn(api, "events").mockResolvedValueOnce({events:first, nextAfter:100})
    .mockResolvedValueOnce({events:[last, first[99]], nextAfter:101});
  render(<App />);
  const timeline = (await screen.findByRole("heading", {name:"Evidence timeline"})).closest("section")!;
  const rows = await within(timeline).findAllByRole("listitem");
  expect(rows).toHaveLength(101);
  expect(rows.at(-1)?.id).toBe("event-record-101");
  expect(pages.mock.calls.map(([, cursor]) => cursor)).toEqual([0, 100]);
});

test("report marks scripted runs and excludes incomplete pairs from outcome counts", async () => {
  history.replaceState(null, "", "/runs/server-run?view=report");
  const cases = serverRun.cases.map((leg): CaseResult =>
    leg.version === "revised" && leg.caseId === "monitor-valid"
      ? {...leg, outcome:"incomplete", score:undefined, incompleteReason:"Provider timed out."} : leg);
  vi.spyOn(api, "run").mockResolvedValue({...serverRun, cases});
  vi.spyOn(api, "events").mockResolvedValue({events, nextAfter:events.length});
  render(<App />);
  expect(await screen.findByText("These results come from a scripted defender.")).toBeTruthy();
  expect(screen.getByText(/1 comparable case pair.*2 incomplete/)).toBeTruthy();
  expect(screen.getByText("Provider timed out.")).toBeTruthy();
  expect(screen.queryByText("Design preview")).toBeNull();
  fireEvent.click(screen.getByRole("button", {name:"Held out"}));
  expect(screen.getByRole("heading", {name:"Held-out case"})).toBeTruthy();
});

test("a stale retest refreshes the run and shows the server's conflict message", async () => {
  history.replaceState(null, "", "/runs/server-run?view=evidence");
  const ready: RunView = {
    ...serverRun, status:"ready_to_retest", actions:{...serverRun.actions, canRetest:true},
  };
  const load = vi.spyOn(api, "run").mockResolvedValueOnce(ready).mockResolvedValue(serverRun);
  vi.spyOn(api, "events").mockResolvedValue({events, nextAfter:events.length});
  const retest = vi.spyOn(api, "retest").mockRejectedValue(new ApiError(409,
    { code:"stale_revision", message:"The revision has changed.", retryable:false }));
  render(<App />);
  fireEvent.click(await screen.findByRole("button", {name:"Apply in lab and retest"}));
  expect((await screen.findByRole("alert")).textContent).toMatch(/The revision has changed/);
  expect(await screen.findByRole("link", {name:"View report"})).toBeTruthy();
  expect(retest).toHaveBeenCalledOnce();
  expect(load.mock.calls.length).toBeGreaterThan(1);
});

test("a rate-limited start honors the retry delay and reuses its key", async () => {
  const limited = new ApiError(429, {code:"queue_full", message:"Queue full.", retryable:true}, 150);
  const start = vi.spyOn(api, "start").mockRejectedValueOnce(limited).mockResolvedValue(serverRun);
  vi.spyOn(api, "run").mockResolvedValue(serverRun);
  vi.spyOn(api, "events").mockResolvedValue({events, nextAfter:events.length});
  render(<App />);
  await screen.findByRole("button", {name:"Start probation shift"});
  const button = screen.getByRole("button", {name:"Start probation shift"}) as HTMLButtonElement;
  await waitFor(() => expect(button.disabled).toBe(false));
  fireEvent.click(button);
  const wait = await screen.findByRole("button", {name:"Please wait…"});
  expect((wait as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(await screen.findByRole("button", {name:"Start probation shift"}));
  await screen.findByRole("link", {name:"View report"});
  expect(start).toHaveBeenCalledTimes(2);
  expect(start.mock.calls[1][0]).toBe(start.mock.calls[0][0]);
});

test("the recording flow waits for each phase and keeps the next action visible", async () => {
  const baseline: RunView = {
    ...serverRun, status: "baseline_running", finding: undefined, revision: undefined, review: undefined,
    actions: { canRetest: false, canReview: false, canStartVoice: false },
  };
  let current = baseline;
  vi.spyOn(api, "start").mockResolvedValue(baseline);
  vi.spyOn(api, "run").mockImplementation(async () => current);
  vi.spyOn(api, "events").mockResolvedValue({events, nextAfter: events.length});
  vi.spyOn(api, "retest").mockImplementation(async () => {
    current = { ...serverRun, status: "retest_running" };
    return current;
  });
  vi.spyOn(api, "review").mockImplementation(async () => {
    current = { ...serverRun, review: { ...serverRun.review!, status: "reviewed" } };
    return current;
  });
  render(<App />);
  const start = screen.getByRole<HTMLButtonElement>("button", { name: "Start probation shift" });
  await waitFor(() => expect(start.disabled).toBe(false));
  fireEvent.click(start);
  const inspect = await screen.findByRole<HTMLButtonElement>("button", { name: "Inspect the evidence" });
  expect(inspect.disabled).toBe(true);
  expect(screen.getByText(/Available when the baseline finishes/)).toBeTruthy();
  const validClaim = screen.getByRole("region", { name: "Valid claim" });
  expect(within(validClaim).getByRole("heading", { name: "Talk to the defender" })).toBeTruthy();
  expect(screen.getByRole("listitem", { current: "step" }).textContent).toContain("Baseline");

  current = { ...baseline, status: "investigating" };
  fireEvent.click(await screen.findByRole("link", { name: "Inspect the evidence" }, { timeout: 2500 }));
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Apply in lab and retest", description: "13 is reviewing the tool history." }).disabled).toBe(true);
  current = { ...serverRun, status: "ready_to_retest", actions: { ...serverRun.actions, canRetest: true } };
  const apply = screen.getByRole<HTMLButtonElement>("button", { name: "Apply in lab and retest" });
  await waitFor(() => expect(apply.disabled).toBe(false), { timeout: 2500 });
  const firstKey = events.find(event => serverRun.finding!.eventIds.includes(event.id))!;
  expect(document.getElementById(`event-${firstKey.id}`)?.querySelector("button")?.getAttribute("aria-current")).toBe("true");
  fireEvent.click(apply);

  const view = await screen.findByRole<HTMLButtonElement>("button", { name: "View report" });
  expect(view.disabled).toBe(true);
  expect(location.search).toContain("view=evidence");
  expect(screen.getByRole("listitem", { current: "step" }).textContent).toContain("Retest");
  current = serverRun;
  fireEvent.click(await screen.findByRole("link", { name: "View report" }, { timeout: 2500 }));
  expect(screen.getByRole("heading", { name: "Readiness report" })).toBeTruthy();
  expect(screen.getByRole("listitem", { current: "step" }).textContent).toContain("Review");
  fireEvent.click(screen.getByRole("button", { name: "Mark reviewed" }));
  expect(await screen.findByText("This run has been reviewed.")).toBeTruthy();
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Mark reviewed" }).disabled).toBe(true);
});

test("an interrupted baseline preserves partial evidence without unlocking the next step", async () => {
  history.replaceState(null, "", "/runs/server-run?view=lab");
  vi.spyOn(api, "run").mockResolvedValue({ ...serverRun, status: "failed", finding: undefined, revision: undefined, cases: [] });
  vi.spyOn(api, "events").mockResolvedValue({ events: [events[0]], nextAfter: 1 });
  render(<App />);
  const inspect = await screen.findByRole<HTMLButtonElement>("button", {
    name: "Inspect the evidence", description: "The shift ended before the baseline finished.",
  });
  expect(inspect.disabled).toBe(true);
  fireEvent.click(screen.getByRole("link", { name: "Open partial evidence" }));
  expect(await screen.findByRole<HTMLButtonElement>("button", {
    name: "Apply in lab and retest", description: "The shift was interrupted.",
  })).toHaveProperty("disabled", true);
});
