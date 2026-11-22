// Adapted from Alibi (rayanali2/HackPrinceton), web/src/playback.tsx at f109e4d5c6720ffa433aa65b0800994afbe416d7.
import { useEffect, useState } from "react";

/** Replays received records locally. This never changes a case or calls an API. */
export function useRecordedPlayback(total: number) {
  const [count, setCount] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const shown = count == null ? total : Math.min(count, total);
  useEffect(() => {
    if (!playing) return;
    if (shown >= total) {
      setPlaying(false);
      return;
    }
    const timer = window.setTimeout(() => setCount(shown + 1), 600 / speed);
    return () => window.clearTimeout(timer);
  }, [playing, shown, total, speed]);
  return {
    shown,
    total,
    playing,
    speed,
    play: () => {
      if (shown >= total) setCount(0);
      setPlaying(true);
    },
    pause: () => setPlaying(false),
    step: () => {
      setPlaying(false);
      setCount(shown >= total ? 1 : shown + 1);
    },
    all: () => {
      setPlaying(false);
      setCount(null);
    },
    seek: (n: number) => {
      setPlaying(false);
      setCount(Math.max(0, Math.min(n, total)));
    },
    setSpeed,
  };
}
export function PlaybackControls({
  playback,
}: {
  playback: ReturnType<typeof useRecordedPlayback>;
}) {
  const p = playback;
  return (
    <div
      className="record-playback"
      role="group"
      aria-label="Recorded event playback"
    >
      <button disabled={!p.total} onClick={p.playing ? p.pause : p.play}>
        {p.playing
          ? "Pause playback"
          : p.shown >= p.total
            ? "Replay recorded events"
            : "Resume playback"}
      </button>
      <button disabled={!p.total} onClick={p.step}>
        Next record
      </button>
      <button disabled={p.shown === p.total && !p.playing} onClick={p.all}>
        Show all records
      </button>
      <input
        type="range"
        aria-label="Playback position"
        min="0"
        max={p.total}
        value={p.shown}
        onChange={(e) => p.seek(Number(e.target.value))}
      />
      <label>
        Display pace
        <select
          aria-label="Recorded event display speed"
          value={p.speed}
          onChange={(e) => p.setSpeed(Number(e.target.value))}
        >
          <option value="0.5">0.5×</option>
          <option value="1">1×</option>
          <option value="2">2×</option>
          <option value="4">4×</option>
        </select>
      </label>
      <span className="small muted" role="status">
        {p.shown}/{p.total} records · display pace, not execution time
      </span>
    </div>
  );
}
