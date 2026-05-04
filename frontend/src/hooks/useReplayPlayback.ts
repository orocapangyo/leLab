import { useCallback, useEffect, useRef, useState } from "react";
import { useApi } from "@/contexts/ApiContext";
import {
  CameraItem,
  controlReplay as apiControlReplay,
  listDatasets as apiListDatasets,
  listEpisodes as apiListEpisodes,
  startReplay as apiStartReplay,
  stopReplay as apiStopReplay,
  StartReplayResponse,
} from "@/lib/replayApi";

export type ReplayStatus = "idle" | "loading" | "playing" | "paused" | "ended" | "error";

export interface ReplaySessionState {
  status: ReplayStatus;
  repoId: string | null;
  episode: number | null;
  frame: number;
  totalFrames: number;
  fps: number;
  speed: number;
  paused: boolean;
  cameras: CameraItem[];
  jointNames: string[];
  error: string | null;
}

const INITIAL: ReplaySessionState = {
  status: "idle",
  repoId: null,
  episode: null,
  frame: 0,
  totalFrames: 0,
  fps: 30,
  speed: 1,
  paused: false,
  cameras: [],
  jointNames: [],
  error: null,
};

const SYNC_THRESHOLD_S = 0.2;

export const useReplayPlayback = () => {
  const { baseUrl, wsBaseUrl, fetchWithHeaders } = useApi();
  const [state, setState] = useState<ReplaySessionState>(INITIAL);
  const stateRef = useRef(state);
  stateRef.current = state;

  const videoRefs = useRef<HTMLVideoElement[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  const setVideoRefs = useCallback((els: (HTMLVideoElement | null)[]) => {
    videoRefs.current = els.filter((e): e is HTMLVideoElement => e !== null);
  }, []);

  // Drive videos in response to backend frame ticks.
  const onTick = useCallback((frame: number) => {
    setState((s) => (s.frame === frame ? s : { ...s, frame }));

    const fps = stateRef.current.fps || 30;
    const expected = frame / fps;
    for (const v of videoRefs.current) {
      if (Number.isFinite(v.duration) && Math.abs(v.currentTime - expected) > SYNC_THRESHOLD_S) {
        try { v.currentTime = expected; } catch { /* ignored */ }
      }
    }
  }, []);

  // Subscribe to /ws/joint-data — read only the `frame` field.
  useEffect(() => {
    const ws = new WebSocket(`${wsBaseUrl}/ws/joint-data`);
    wsRef.current = ws;
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "joint_update" && typeof msg.frame === "number") {
          onTick(msg.frame);
        }
      } catch { /* ignored */ }
    };
    return () => { ws.close(); wsRef.current = null; };
  }, [wsBaseUrl, onTick]);

  const start = useCallback(async (repoId: string, episode: number): Promise<StartReplayResponse> => {
    // If a session is already active, stop it first so backend isn't blocked by its own session.
    if (stateRef.current.status === "playing" || stateRef.current.status === "paused") {
      await apiStopReplay(baseUrl, fetchWithHeaders);
    }
    setState((s) => ({ ...s, status: "loading", error: null, repoId, episode }));
    const resp = await apiStartReplay(baseUrl, fetchWithHeaders, repoId, episode);
    if (!resp.success) {
      setState((s) => ({ ...s, status: "error", error: resp.message || "Failed to start replay" }));
      return resp;
    }
    setState({
      status: "playing",
      repoId,
      episode,
      frame: 0,
      totalFrames: resp.num_frames || 0,
      fps: resp.fps || 30,
      speed: 1,
      paused: false,
      cameras: resp.cameras || [],
      jointNames: resp.joint_names || [],
      error: null,
    });
    // Kick off video playback.
    setTimeout(() => {
      videoRefs.current.forEach((v) => {
        v.playbackRate = 1;
        v.play().catch(() => { /* autoplay block tolerated */ });
      });
    }, 50);
    return resp;
  }, [baseUrl, fetchWithHeaders]);

  const pause = useCallback(async () => {
    await apiControlReplay(baseUrl, fetchWithHeaders, "pause");
    videoRefs.current.forEach((v) => v.pause());
    setState((s) => ({ ...s, paused: true, status: "paused" }));
  }, [baseUrl, fetchWithHeaders]);

  const resume = useCallback(async () => {
    await apiControlReplay(baseUrl, fetchWithHeaders, "resume");
    videoRefs.current.forEach((v) => v.play().catch(() => { /* ignored */ }));
    setState((s) => ({ ...s, paused: false, status: "playing" }));
  }, [baseUrl, fetchWithHeaders]);

  const seek = useCallback(async (frame: number) => {
    await apiControlReplay(baseUrl, fetchWithHeaders, "seek", frame);
    setState((s) => ({ ...s, frame }));
  }, [baseUrl, fetchWithHeaders]);

  const setSpeed = useCallback(async (value: number) => {
    await apiControlReplay(baseUrl, fetchWithHeaders, "set_speed", value);
    videoRefs.current.forEach((v) => { v.playbackRate = value; });
    setState((s) => ({ ...s, speed: value }));
  }, [baseUrl, fetchWithHeaders]);

  const stop = useCallback(async () => {
    await apiStopReplay(baseUrl, fetchWithHeaders);
    videoRefs.current.forEach((v) => v.pause());
    setState(INITIAL);
  }, [baseUrl, fetchWithHeaders]);

  // Stop on unmount.
  useEffect(() => {
    return () => {
      // Best-effort fire-and-forget; stop is idempotent on the backend.
      apiStopReplay(baseUrl, fetchWithHeaders).catch(() => {});
    };
  }, [baseUrl, fetchWithHeaders]);

  return {
    state,
    setVideoRefs,
    start,
    pause,
    resume,
    seek,
    setSpeed,
    stop,
    listDatasets: () => apiListDatasets(baseUrl, fetchWithHeaders),
    listEpisodes: (repoId: string) => apiListEpisodes(baseUrl, fetchWithHeaders, repoId),
  };
};
