"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import Hls from "hls.js";
import { VideoOff } from "lucide-react";

const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws";

const MAX_TILES = 6;
const FLASH_DURATION_MS = 3000;

interface Camera {
  id: string;
  name: string;
  department: string;
  lat: number;
  lng: number;
  hls_url: string | null;
}

interface AlertMessage {
  type?: string;
  camera_id: string;
  [key: string]: unknown;
}

function CameraTile({
  camera,
  flashing,
}: {
  camera: Camera;
  flashing: boolean;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const hlsRef = useRef<Hls | null>(null);
  const [errored, setErrored] = useState<boolean>(false);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !camera.hls_url) {
      setErrored(true);
      return;
    }

    setErrored(false);

    if (Hls.isSupported()) {
      const hls = new Hls({
        maxBufferLength: 10,
        liveSyncDurationCount: 3,
      });
      hlsRef.current = hls;

      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (data.fatal) {
          setErrored(true);
        }
      });

      hls.loadSource(camera.hls_url);
      hls.attachMedia(video);
      video.play().catch(() => {
        /* autoplay might be blocked; ignore */
      });
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = camera.hls_url;
      video.play().catch(() => {
        /* autoplay might be blocked; ignore */
      });
    } else {
      setErrored(true);
    }

    return () => {
      if (hlsRef.current) {
        hlsRef.current.destroy();
        hlsRef.current = null;
      }
      if (video) {
        video.removeAttribute("src");
        video.load();
      }
    };
  }, [camera.hls_url]);

  return (
    <div
      className={`relative flex h-full w-full flex-col overflow-hidden rounded-md border bg-black transition-shadow ${
        flashing
          ? "border-noc-critical flash-border-active"
          : "border-noc-border"
      }`}
    >
      <div className="relative flex-1 bg-black">
        {errored ? (
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-gray-600">
            <VideoOff size={28} />
            <span className="text-xs">Feed unavailable</span>
          </div>
        ) : (
          <video
            ref={videoRef}
            muted
            playsInline
            autoPlay
            className="h-full w-full object-cover"
          />
        )}
      </div>
      <div className="flex items-center justify-between border-t border-noc-border bg-noc-panel/90 px-2 py-1">
        <span className="truncate text-xs font-medium text-gray-200">
          {camera.name}
        </span>
        <span className="shrink-0 text-[10px] uppercase tracking-wide text-gray-500">
          {camera.department}
        </span>
      </div>
    </div>
  );
}

export default function CameraGrid() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [flashingIds, setFlashingIds] = useState<Record<string, boolean>>({});
  const timersRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef<boolean>(true);

  useEffect(() => {
    let cancelled = false;

    const fetchCameras = async () => {
      try {
        const res = await fetch(`${API_URL}/cameras`);
        if (!res.ok) throw new Error(`Failed to fetch cameras: ${res.status}`);
        const data: Camera[] = await res.json();
        if (!cancelled) {
          setCameras(data.slice(0, MAX_TILES));
        }
      } catch (err) {
        console.error("CameraGrid: failed to fetch cameras", err);
      }
    };

    fetchCameras();

    return () => {
      cancelled = true;
    };
  }, []);

  const triggerFlash = useCallback((cameraId: string) => {
    setFlashingIds((prev) => ({ ...prev, [cameraId]: true }));

    if (timersRef.current[cameraId]) {
      clearTimeout(timersRef.current[cameraId]);
    }

    timersRef.current[cameraId] = setTimeout(() => {
      setFlashingIds((prev) => {
        const next = { ...prev };
        delete next[cameraId];
        return next;
      });
      delete timersRef.current[cameraId];
    }, FLASH_DURATION_MS);
  }, []);

  useEffect(() => {
    mountedRef.current = true;

    const connect = () => {
      if (!mountedRef.current) return;

      let socket: WebSocket;
      try {
        socket = new WebSocket(WS_URL);
      } catch {
        reconnectTimerRef.current = setTimeout(connect, 3000);
        return;
      }

      wsRef.current = socket;

      socket.onmessage = (event: MessageEvent<string>) => {
        try {
          const data: AlertMessage = JSON.parse(event.data);
          if (data && data.camera_id) {
            triggerFlash(data.camera_id);
          }
        } catch (err) {
          console.error("CameraGrid: failed to parse websocket message", err);
        }
      };

      socket.onclose = () => {
        if (!mountedRef.current) return;
        reconnectTimerRef.current = setTimeout(connect, 3000);
      };

      socket.onerror = () => {
        socket.close();
      };
    };

    connect();

    return () => {
      mountedRef.current = false;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.onerror = null;
        wsRef.current.close();
      }
      Object.values(timersRef.current).forEach((t) => clearTimeout(t));
      timersRef.current = {};
    };
  }, [triggerFlash]);

  const placeholders = Math.max(0, MAX_TILES - cameras.length);

  return (
    <div className="grid h-full w-full grid-cols-3 grid-rows-2 gap-2 p-2">
      {cameras.map((camera) => (
        <CameraTile
          key={camera.id}
          camera={camera}
          flashing={Boolean(flashingIds[camera.id])}
        />
      ))}
      {Array.from({ length: placeholders }).map((_, idx) => (
        <div
          key={`placeholder-${idx}`}
          className="flex h-full w-full items-center justify-center rounded-md border border-dashed border-noc-border bg-noc-panel/40 text-xs text-gray-600"
        >
          No feed assigned
        </div>
      ))}
    </div>
  );
}
