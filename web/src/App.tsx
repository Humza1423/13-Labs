// Glassy Dashboard shell adapted from @automateflows (21st.dev, MIT).
import { useEffect, useRef, useState } from "react";
import type { MouseEvent, ReactNode } from "react";
import { ArrowUpRight, ChartNoAxesColumnIncreasing, FlaskConical, ListTree, Moon, Sun } from "lucide-react";
import { api, ApiError } from "./api";
import { EvidenceDrawer } from "./components/EvidenceDrawer";
import { provenance, statusLabel } from "./components/runLabels";
import { EVENT_POLL_MS } from "./contract";
import type { RunEvent, RunView, ScenariosResponse } from "./contract";
import { previewFixture } from "./fixtures";
import { Evidence } from "./views/Evidence";
import { Lab } from "./views/Lab";
import { Report } from "./views/Report";
import "@fontsource-variable/manrope";
import "@fontsource-variable/geist";
import "./styles/glassy.css";
import "./styles/stage.css";
import "./styles/report.css";

const destinations = [
  { id: "lab", name: "Lab", icon: FlaskConical },
  { id: "evidence", name: "Evidence", icon: ListTree },
  { id: "report", name: "Report", icon: ChartNoAxesColumnIncreasing },
] as const;
type View = typeof destinations[number]["id"];
type PreviewStep = "baseline" | "ready" | "retest" | "complete" | "reviewed";
const fixture = previewFixture.run;
const previewEvents = previewFixture.events;
const noEvents: RunEvent[] = [];
const baselineEvents = previewEvents.filter(event => event.stage === "baseline");
const investigationEvents = previewEvents.filter(event => event.stage === "investigation" || event.stage === "revision");

function readLocation() {
  const params = new URLSearchParams(location.search);
  const runId = location.pathname.match(/^\/runs\/([^/]+)\/?$/)?.[1];
  const requested = params.get("view");
  const view = destinations.find(item => item.id === requested)?.id ?? "lab";
  const step = params.get("step");
  const previewStep: PreviewStep = step === "baseline" || step === "ready" || step === "retest" || step === "reviewed"
    ? step : "complete";
  return { runId, view, previewStep, eventId: params.get("event") ?? undefined, explicitPreview: params.get("mode") === "preview" };
}

function hrefFor(view: View, runId?: string, step?: PreviewStep, explicitPreview = false, eventId?: string) {
  const params = new URLSearchParams();
  if (view !== "lab" || runId) params.set("view", view);
  if (runId === fixture.id && step) params.set("step", step);
  if (!runId && explicitPreview) params.set("mode", "preview");
  if (eventId) params.set("event", eventId);
  const query = params.size ? `?${params}` : "";
  return `${runId ? `/runs/${encodeURIComponent(runId)}` : "/"}${query}`;
}

function previewState(step: PreviewStep, count: number): { run: RunView; events: RunEvent[] } {
  if (step === "complete" || step === "reviewed") {
    return {
      run: step === "reviewed" ? {
        ...fixture, actions: { ...fixture.actions, canReview: false },
        review: fixture.review && { ...fixture.review, status: "reviewed" },
      } : fixture,
      events: previewEvents,
    };
  }
  const baseline = step === "baseline";
  const ready = step === "ready";
  const investigating = baseline && count >= baselineEvents.length;
  const events = baseline ? [...baselineEvents, ...investigationEvents].slice(0, count)
    : ready ? [...baselineEvents, ...investigationEvents]
    : previewEvents.filter(event => event.stage !== "review")
      .slice(0, baselineEvents.length + investigationEvents.length + count);
  const cases = fixture.cases.filter(item => {
    if (item.version === "baseline") {
      return !baseline || investigating || (item.evidenceIds.length > 0 && item.evidenceIds.every(id => events.some(event => event.id === id)));
    }
    return step === "retest" && item.evidenceIds.length > 0 && item.evidenceIds.every(id => events.some(event => event.id === id));
  });
  return {
    run: {
      ...fixture,
      status: baseline ? investigating ? "investigating" : "baseline_running" : ready ? "ready_to_retest" : "retest_running",
      cases,
      progress: { ...fixture.progress, completed: cases.filter(item => item.outcome !== "incomplete").length },
      finding: events.some(event => event.type === "finding_published") ? fixture.finding : undefined,
      revision: ready || step === "retest" ? fixture.revision : undefined,
      review: undefined,
      actions: { canRetest: ready, canReview: false, canStartVoice: false },
    },
    events,
  };
}

type LiveState = { id: string; run?: RunView; events: RunEvent[]; error?: ApiError; drained?: boolean };
type Availability = { checking: boolean; unavailable?: boolean; data?: ScenariosResponse; error?: ApiError };
const apiError = (error: unknown) => error instanceof ApiError ? error : new ApiError(0, {
  code: "unexpected", message: "The shift could not be updated.", retryable: true,
});

function useLiveRun(id: string | undefined, generation: number, pending: boolean) {
  const [state, setState] = useState<LiveState>();
  useEffect(() => {
    if (!id || pending) return;
    const requestedId = id;
    const controller = new AbortController();
    const { signal } = controller;
    let timer = 0;
    let cursor = 0;

    async function poll() {
      try {
        const latest = await api.run(requestedId, signal);
        const events: RunEvent[] = [];
        let next = cursor;
        while (!signal.aborted) {
          const page = await api.events(requestedId, next, signal);
          events.push(...page.events);
          if (page.nextAfter <= next) break;
          next = page.nextAfter;
          if (page.events.length < 100) break;
        }
        if (signal.aborted) return;
        cursor = next;
        setState(previous => {
          const merged = new Map((previous?.id === requestedId ? previous.events : []).map(event => [event.id, event]));
          for (const event of events) merged.set(event.id, event);
          return { id: requestedId, run: latest, events: [...merged.values()].sort((a, b) => a.seq - b.seq), drained: true };
        });
        if (!["failed", "complete", "ready_to_retest"].includes(latest.status)) {
          timer = window.setTimeout(poll, EVENT_POLL_MS);
        }
      } catch (error) {
        if (signal.aborted) return;
        const failure = apiError(error);
        setState(previous => {
          const snapshot = previous?.id === requestedId ? previous : { id: requestedId, events: [] };
          return { ...snapshot, error: failure, drained: snapshot.drained || failure.status === 404 };
        });
        if (failure.unreachable || failure.detail.retryable || failure.status === 429) {
          timer = window.setTimeout(poll, Math.max(failure.retryAfterMs, EVENT_POLL_MS));
        }
      }
    }

    void poll();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [id, generation, pending]);

  return { state: state && state.id === id ? state : undefined, setState };
}

function App() {
  const [route, setRoute] = useState(readLocation);
  const [theme, setTheme] = useState(() => localStorage.getItem("13labs-theme") === "dark" ? "dark" : "light");
  const [availability, setAvailability] = useState<Availability>({ checking: true });
  const [previewCount, setPreviewCount] = useState(1);
  const [generation, refresh] = useState(0);
  const [pending, setPending] = useState(false);
  const [mutationError, setMutationError] = useState<ApiError>();
  const [retryAt, setRetryAt] = useState(0);
  const mutationFlight = useRef(false);
  const startKey = useRef<string | undefined>(undefined);
  const retestKey = useRef<{ run: string; revision: string; key: string } | undefined>(undefined);
  const returnFocus = useRef<HTMLElement | null>(null);
  const content = useRef<HTMLElement>(null);
  const inPreview = route.runId === fixture.id;
  const { state: live, setState: setLive } = useLiveRun(inPreview ? undefined : route.runId, generation, pending);
  const snapshot = inPreview ? previewState(route.previewStep, previewCount) : live;
  const run = snapshot?.run;
  const events = snapshot?.events ?? noEvents;
  const preview = inPreview || (!route.runId && route.explicitPreview);
  const lastPath = useRef(location.pathname + location.search);

  function navigate(href: string, replace = false) {
    if (lastPath.current === href) return;
    const next = new URL(href, location.href);
    const pathChanged = next.pathname !== location.pathname || next.searchParams.get("view") !== new URLSearchParams(location.search).get("view");
    history[replace ? "replaceState" : "pushState"](null, "", href);
    lastPath.current = href;
    setRoute(readLocation());
    if (pathChanged) {
      window.scrollTo(0, 0);
      if (content.current) content.current.scrollTop = 0;
      requestAnimationFrame(() => { if (!readLocation().eventId) content.current?.focus({ preventScroll: true }); });
    }
  }

  useEffect(() => {
    function pop() {
      lastPath.current = location.pathname + location.search;
      setRoute(readLocation());
    }
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, []);

  useEffect(() => {
    if ((route.runId && !live?.error && run?.status !== "failed") || route.explicitPreview) return;
    const controller = new AbortController();
    const { signal } = controller;
    void api.scenarios(signal).then(data => {
      if (!signal.aborted) setAvailability({ checking: false, data });
    }).catch(async error => {
      if (signal.aborted) return;
      const failure = apiError(error);
      // C0 responds to health before the shift routes have been installed.
      if (failure.status === 404) {
        try {
          const health = await api.health(signal);
          if (!signal.aborted && !health.capabilities.batch) {
            setAvailability({ checking: false, unavailable: true });
            return;
          }
        } catch (healthError) {
          if (signal.aborted) return;
          if (apiError(healthError).unreachable) {
            setAvailability({ checking: false, unavailable: true });
            return;
          }
        }
      }
      if (!signal.aborted) setAvailability({
        checking: false, unavailable: failure.unreachable, error: failure.unreachable ? undefined : failure,
      });
    });
    return () => controller.abort();
  }, [route.runId, route.explicitPreview, live?.error?.status, run?.status, generation]);

  useEffect(() => {
    if (!inPreview || (route.previewStep !== "baseline" && route.previewStep !== "retest")) return;
    const baseline = route.previewStep === "baseline";
    const total = baseline ? baselineEvents.length + investigationEvents.length
      : previewEvents.filter(event => event.stage === "retest").length;
    const timer = window.setTimeout(() => {
      if (previewCount < total) setPreviewCount(count => count + (baseline ? 2 : 3));
      else navigate(hrefFor(route.view, fixture.id, baseline ? "ready" : "complete", false, route.eventId), true);
    }, baseline ? 350 : 425);
    return () => window.clearTimeout(timer);
  }, [inPreview, route.previewStep, route.view, route.eventId, previewCount]);

  useEffect(() => {
    if (!retryAt) return;
    const timeout = window.setTimeout(() => setRetryAt(0), Math.max(0, retryAt - Date.now()));
    return () => window.clearTimeout(timeout);
  }, [retryAt]);

  const viewHref = (view: View, event?: string) => hrefFor(view, route.runId,
    inPreview ? route.previewStep : undefined, route.explicitPreview, event);
  const recordedHref = availability.data?.recordedRunId
    ? hrefFor("lab", availability.data.recordedRunId) : undefined;
  const blocked = pending || retryAt > 0;

  async function mutate(operation: () => Promise<RunView>, next: View, kind: "start" | "retest" | "review") {
    if (mutationFlight.current || retryAt) return;
    mutationFlight.current = true;
    setPending(true);
    setMutationError(undefined);
    try {
      const updated = await operation();
      setLive(previous => ({ id: updated.id, run: updated, events: previous?.id === updated.id ? previous.events : [] }));
      if (kind === "start") startKey.current = undefined;
      if (kind === "retest") retestKey.current = undefined;
      navigate(hrefFor(next, updated.id));
    } catch (error) {
      const failure = apiError(error);
      setMutationError(failure);
      if (failure.status === 409) {
        if (kind === "start") startKey.current = undefined;
        if (kind === "retest") retestKey.current = undefined;
        refresh(value => value + 1);
      }
      if (failure.status === 429) setRetryAt(Date.now() + (failure.retryAfterMs || 1500));
    } finally {
      mutationFlight.current = false;
      setPending(false);
    }
  }

  function start() {
    if (preview) {
      setPreviewCount(1);
      navigate(hrefFor("lab", fixture.id, "baseline"));
      return;
    }
    startKey.current ??= crypto.randomUUID();
    void mutate(() => api.start(startKey.current!), "lab", "start");
  }

  function retest() {
    if (!run?.actions.canRetest || !run.revision) return;
    if (inPreview) {
      setPreviewCount(1);
      navigate(hrefFor("evidence", fixture.id, "retest"));
      return;
    }
    const revision = run.revision.id;
    if (retestKey.current?.run !== run.id || retestKey.current.revision !== revision) {
      retestKey.current = { run: run.id, revision, key: crypto.randomUUID() };
    }
    const { key } = retestKey.current;
    void mutate(() => api.retest(run.id, revision, key), "evidence", "retest");
  }

  function review() {
    if (!run?.actions.canReview || run.mode === "replay") return;
    if (inPreview) navigate(hrefFor("report", fixture.id, "reviewed"), true);
    else void mutate(() => api.review(run.id), "report", "review");
  }

  function openEvidence(id: string, trigger: HTMLElement) {
    returnFocus.current = trigger;
    navigate(viewHref(route.view, id));
  }

  function closeEvidence() {
    const next = viewHref(route.view);
    navigate(next, true);
    if (!returnFocus.current?.isConnected) requestAnimationFrame(() => {
      const row = document.getElementById(`event-${route.eventId}`)?.querySelector("button");
      (row ?? content.current)?.focus({ preventScroll: true });
    });
    returnFocus.current = null;
  }

  function handleLinks(event: MouseEvent<HTMLDivElement>) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
    const anchor = (event.target as Element).closest("a");
    if (!anchor || anchor.target || anchor.origin !== location.origin || anchor.hash || anchor.hasAttribute("download")) return;
    event.preventDefault();
    setMutationError(undefined);
    navigate(anchor.pathname + anchor.search);
  }

  function changeTheme() {
    const next = theme === "dark" ? "light" : "dark";
    localStorage.setItem("13labs-theme", next);
    setTheme(next);
  }

  const headingStatus = run?.review?.status === "reviewed" ? "Reviewed"
    : run ? statusLabel[run.status] : route.runId ? "Opening shift" : "Ready to start";
  const startLabel = preview ? "Start design preview" : pending ? "Starting shift…" : retryAt ? "Please wait…" : "Start probation shift";
  let view: ReactNode;
  if (route.view === "evidence") {
    view = <Evidence key={route.runId ?? "new"} run={run} preview={inPreview} events={events} selected={route.eventId} open={openEvidence}
      retest={retest} actionLocked={blocked} recordedHref={recordedHref} labHref={viewHref("lab")} reportHref={viewHref("report")} />;
  } else if (route.view === "report") {
    view = <Report key={route.runId ?? "new"} run={run} events={events} review={review} actionLocked={blocked} open={openEvidence}
      labHref={viewHref("lab")} evidenceHref={viewHref("evidence")} preview={inPreview}
      shareUrl={run ? new URL(hrefFor("report", route.runId, inPreview ? route.previewStep : undefined), location.origin).href : undefined} />;
  } else {
    view = <Lab key={route.runId ?? "new"} run={run} events={events} start={start}
      startLocked={blocked || !!route.runId || (!preview && (availability.checking || !!availability.error || !!availability.unavailable))} startLabel={startLabel}
      startReason={pending ? "Starting the shift." : retryAt ? "Waiting to retry." : route.runId ? "Opening the existing shift."
        : !preview && availability.checking ? "Checking shift availability." : !preview && (availability.error || availability.unavailable) ? "The shift service is unavailable." : undefined}
      candidateName={availability.data?.candidate.name ?? fixture.candidate.name} preview={preview}
      evidenceHref={viewHref("evidence")} reportHref={viewHref("report")} open={openEvidence} recordedHref={recordedHref} />;
  }

  const error = mutationError ?? live?.error ?? (!route.runId && !preview ? availability.error : undefined);
  const newHref = hrefFor("lab", undefined, undefined, route.explicitPreview || inPreview);

  return (
    <div className="gd-app" data-theme={theme} onClick={handleLinks}>
      <a className="skip-link" href="#workspace">Skip to workspace</a>
      <div className="gd-surround">
        <header className="gd-top">
          <a className="gd-brand" href={viewHref("lab")} aria-label="13 Labs home">
            <span className="gd-logo">13</span><span>13 Labs</span>
          </a>
          <div className="gd-ask" role="status"><span className="gd-orb" aria-hidden="true" /><span><strong>AI Probation Shift</strong><span className="status-separator"> · </span><span>{headingStatus}</span></span></div>
          <div className="gd-top-actions"><button className="gd-icon-button" aria-label={theme === "light" ? "Switch to dark theme" : "Switch to light theme"} onClick={changeTheme}>
            {theme === "light" ? <Moon size={19} /> : <Sun size={19} />}
          </button></div>
        </header>
        <div className="gd-stage-wrap">
          <nav className="gd-dock" aria-label="Shift navigation">
            {destinations.map(({ id, name, icon: Icon }) => <a href={viewHref(id)} key={id} aria-current={route.view === id ? "page" : undefined}>
              <Icon size={21} /><span>{name}</span>
            </a>)}
          </nav>
          <main className="gd-stage" id="workspace" ref={content} tabIndex={-1} data-view={route.view}>
            {error && <div className="service-message" role="alert">
              <p>{error.status === 429 ? `${error.message} Please wait before trying again.`
                : error.status === 409 ? `${error.message} The latest shift state is being refreshed.` : error.message}</p>
              {!mutationError && <button className="button secondary" onClick={() => refresh(value => value + 1)}>Retry connection</button>}
              {route.runId && !run && <a className="text-action" href={newHref}>Open new shift <ArrowUpRight size={16} /></a>}
            </div>}
            {run?.status === "failed" && <div className="service-message" role="alert">
              <p>{run.error?.message ?? "The shift was interrupted. Collected evidence is available."}</p>
              <a className="text-action" href={newHref}>Start another shift <ArrowUpRight size={16} /></a>
              {recordedHref && <a className="text-action" href={recordedHref}>Open recorded run <ArrowUpRight size={16} /></a>}
            </div>}
            {route.runId && !run && !error && <p className="service-message" role="status">Loading shift…</p>}
            {availability.unavailable && !route.runId && !preview && <div className="service-message" role="status">
              <p>The shift API is unavailable.</p>
              <button className="button secondary" onClick={() => refresh(value => value + 1)}>Retry connection</button>
              <a className="text-action" href={hrefFor("lab", undefined, undefined, true)}>Open design preview <ArrowUpRight size={16} /></a>
            </div>}
            {view}
            {run && <footer className="workspace-footer">
              <span>{provenance(run, inPreview)}</span>
              <a href={newHref}>New shift <ArrowUpRight size={15} /></a>
            </footer>}
          </main>
        </div>
      </div>
      {route.eventId && <EvidenceDrawer event={events.find(event => event.id === route.eventId)}
        loading={!!route.runId && !inPreview && !live?.drained && !live?.error} onClose={closeEvidence} returnFocus={returnFocus.current} />}
    </div>
  );
}

export default App;
