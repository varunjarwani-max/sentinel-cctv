"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import type { LatLngExpression } from "leaflet";
import { X, MapPin, Clock } from "lucide-react";

const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface HistoryEntry {
  camera_id: string;
  lat: number;
  lng: number;
  first_seen_at: string;
  last_seen_at: string;
}

interface VehicleHistoryPanelProps {
  plate: string | null;
  onClose: () => void;
}

function MiniMapInner({ entries }: { entries: HistoryEntry[] }) {
  const [Leaflet, setLeaflet] = useState<typeof import("react-leaflet") | null>(
    null
  );

  useEffect(() => {
    let cancelled = false;

    (async () => {
      const leafletCore = await import("leaflet");
      const RL = await import("react-leaflet");

      // Fix default marker icon paths, which webpack breaks by default.
      // @ts-expect-error - _getIconUrl is a private Leaflet internal.
      delete leafletCore.Icon.Default.prototype._getIconUrl;
      leafletCore.Icon.Default.mergeOptions({
        iconRetinaUrl:
          "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
        iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
        shadowUrl:
          "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
      });

      if (!cancelled) {
        setLeaflet(RL);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  if (!Leaflet || entries.length === 0) {
    return (
      <div className="flex h-56 w-full items-center justify-center rounded-md border border-noc-border bg-black/30 text-xs text-gray-600">
        No route to display
      </div>
    );
  }

  const { MapContainer, TileLayer, Marker, Polyline, Popup } = Leaflet;

  const positions: LatLngExpression[] = entries.map((e) => [e.lat, e.lng]);
  const center: LatLngExpression = positions[Math.floor(positions.length / 2)];

  return (
    <div className="h-56 w-full overflow-hidden rounded-md border border-noc-border">
      <MapContainer
        center={center}
        zoom={12}
        scrollWheelZoom={false}
        style={{ height: "100%", width: "100%" }}
      >
        <TileLayer
          url="https://cartodb-basemaps-{s}.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png"
          attribution='&copy; <a href="https://carto.com/attributions">CARTO</a>'
        />
        <Polyline positions={positions} pathOptions={{ color: "#3B82F6", weight: 3 }} />
        {entries.map((entry, idx) => (
          <Marker key={`${entry.camera_id}-${idx}`} position={[entry.lat, entry.lng]}>
            <Popup>
              <div className="text-xs">
                <p className="font-semibold">{entry.camera_id}</p>
                <p>First seen: {new Date(entry.first_seen_at).toLocaleString()}</p>
                <p>Last seen: {new Date(entry.last_seen_at).toLocaleString()}</p>
              </div>
            </Popup>
          </Marker>
        ))}
      </MapContainer>
    </div>
  );
}

const MiniMap = dynamic(() => Promise.resolve(MiniMapInner), { ssr: false });

export default function VehicleHistoryPanel({
  plate,
  onClose,
}: VehicleHistoryPanelProps) {
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!plate) return;

    let cancelled = false;
    setLoading(true);
    setError(null);

    (async () => {
      try {
        const res = await fetch(
          `${API_URL}/vehicle/${encodeURIComponent(plate)}/history`
        );
        if (!res.ok) {
          throw new Error(`Failed to fetch history: ${res.status}`);
        }
        const data: HistoryEntry[] = await res.json();
        const sorted = [...data].sort(
          (a, b) =>
            new Date(a.first_seen_at).getTime() -
            new Date(b.first_seen_at).getTime()
        );
        if (!cancelled) {
          setHistory(sorted);
        }
      } catch (err) {
        console.error("VehicleHistoryPanel: fetch failed", err);
        if (!cancelled) {
          setError("Unable to load vehicle history.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [plate]);

  const isOpen = plate !== null;

  return (
    <>
      <div
        className={`fixed inset-0 z-40 bg-black/50 transition-opacity ${
          isOpen ? "pointer-events-auto opacity-100" : "pointer-events-none opacity-0"
        }`}
        onClick={onClose}
      />
      <div
        className={`fixed right-0 top-0 z-50 h-full w-full max-w-md transform border-l border-noc-border bg-noc-panel shadow-2xl transition-transform duration-300 ${
          isOpen ? "translate-x-0" : "translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between border-b border-noc-border px-5 py-4">
          <div>
            <p className="text-xs uppercase tracking-widest text-gray-500">
              Vehicle History
            </p>
            <p className="plate-mono text-lg font-bold text-gray-100">
              {plate ?? "--"}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1.5 text-gray-400 hover:bg-white/5 hover:text-gray-200"
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex flex-col gap-4 overflow-y-auto p-5">
          {plate && <MiniMap entries={history} />}

          {loading && (
            <p className="text-xs text-gray-500">Loading movement history...</p>
          )}
          {error && <p className="text-xs text-noc-critical">{error}</p>}
          {!loading && !error && plate && history.length === 0 && (
            <p className="text-xs text-gray-500">
              No recorded sightings for this plate yet.
            </p>
          )}

          <ul className="flex flex-col gap-3">
            {history.map((entry, idx) => (
              <li
                key={`${entry.camera_id}-${idx}`}
                className="rounded-md border border-noc-border bg-black/20 p-3"
              >
                <div className="flex items-center gap-2 text-sm text-gray-200">
                  <MapPin size={14} className="text-noc-accent" />
                  <span className="font-medium">{entry.camera_id}</span>
                </div>
                <div className="mt-2 flex items-center gap-2 text-xs text-gray-500">
                  <Clock size={12} />
                  <span className="plate-mono">
                    {new Date(entry.first_seen_at).toLocaleString()} &rarr;{" "}
                    {new Date(entry.last_seen_at).toLocaleString()}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </>
  );
}
