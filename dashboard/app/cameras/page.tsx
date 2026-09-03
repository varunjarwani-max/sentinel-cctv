"use client";

import { useEffect, useRef, useState } from "react";
import Hls from "hls.js";
import { X, Video, CircleDot } from "lucide-react";

const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface Camera {
  id: string;
  name: string;
  department: string;
  lat: number;
  lng: number;
  hls_url: string | null;
  active?: boolean;
}

function LiveModal({
  camera,
  onClose,
}: {
  camera: Camera;
  onClose: () => void;
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
      const hls = new Hls();
      hlsRef.current = hls;

      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (data.fatal) setErrored(true);
      });

      hls.loadSource(camera.hls_url);
      hls.attachMedia(video);
      video.play().catch(() => undefined);
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = camera.hls_url;
      video.play().catch(() => undefined);
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6">
      <div className="w-full max-w-2xl overflow-hidden rounded-lg border border-noc-border bg-noc-panel">
        <div className="flex items-center justify-between border-b border-noc-border px-4 py-3">
          <div>
            <p className="text-sm font-semibold text-gray-100">{camera.name}</p>
            <p className="text-xs text-gray-500">{camera.department}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1.5 text-gray-400 hover:bg-white/5 hover:text-gray-200"
          >
            <X size={18} />
          </button>
        </div>
        <div className="aspect-video w-full bg-black">
          {errored ? (
            <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-gray-600">
              <Video size={28} />
              <span className="text-xs">Live feed unavailable</span>
            </div>
          ) : (
            <video
              ref={videoRef}
              muted
              playsInline
              autoPlay
              controls
              className="h-full w-full object-contain"
            />
          )}
        </div>
      </div>
    </div>
  );
}

export default function CamerasPage() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCamera, setSelectedCamera] = useState<Camera | null>(null);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`${API_URL}/cameras`);
        if (!res.ok) throw new Error(`Failed to fetch cameras: ${res.status}`);
        const data: Camera[] = await res.json();
        if (!cancelled) setCameras(data);
      } catch (err) {
        console.error("CamerasPage: fetch failed", err);
        if (!cancelled) setError("Unable to load cameras.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="flex h-full w-full flex-col p-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold text-gray-100">Camera Fleet</h1>
        <span className="text-xs text-gray-500">
          {cameras.length} camera{cameras.length === 1 ? "" : "s"}
        </span>
      </div>

      {loading && <p className="text-sm text-gray-500">Loading cameras...</p>}
      {error && <p className="text-sm text-noc-critical">{error}</p>}

      {!loading && !error && (
        <div className="overflow-hidden rounded-lg border border-noc-border">
          <table className="w-full border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-noc-border bg-noc-panel text-xs uppercase tracking-wide text-gray-500">
                <th className="px-4 py-3 font-medium">ID</th>
                <th className="px-4 py-3 font-medium">Name</th>
                <th className="px-4 py-3 font-medium">Department</th>
                <th className="px-4 py-3 font-medium">Coordinates</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Live</th>
              </tr>
            </thead>
            <tbody>
              {cameras.map((camera) => (
                <tr
                  key={camera.id}
                  className="border-b border-noc-border bg-noc-bg/40 last:border-b-0 hover:bg-white/5"
                >
                  <td className="plate-mono px-4 py-3 text-gray-300">
                    {camera.id}
                  </td>
                  <td className="px-4 py-3 text-gray-200">{camera.name}</td>
                  <td className="px-4 py-3 text-gray-400">
                    {camera.department}
                  </td>
                  <td className="plate-mono px-4 py-3 text-gray-400">
                    {camera.lat.toFixed(4)}, {camera.lng.toFixed(4)}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`flex items-center gap-1.5 text-xs ${
                        camera.active === false
                          ? "text-noc-critical"
                          : "text-noc-online"
                      }`}
                    >
                      <CircleDot size={12} />
                      {camera.active === false ? "Offline" : "Online"}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <button
                      type="button"
                      onClick={() => setSelectedCamera(camera)}
                      className="rounded-md bg-noc-accent/10 px-3 py-1 text-xs font-medium text-noc-accent hover:bg-noc-accent/20"
                    >
                      View Feed
                    </button>
                  </td>
                </tr>
              ))}
              {cameras.length === 0 && (
                <tr>
                  <td
                    colSpan={6}
                    className="px-4 py-6 text-center text-xs text-gray-600"
                  >
                    No cameras registered.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {selectedCamera && (
        <LiveModal
          camera={selectedCamera}
          onClose={() => setSelectedCamera(null)}
        />
      )}
    </div>
  );
}
