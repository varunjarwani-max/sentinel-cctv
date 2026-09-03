"use client";

import { useState } from "react";
import CameraGrid from "@/components/CameraGrid";
import AlertFeed from "@/components/AlertFeed";
import VehicleHistoryPanel from "@/components/VehicleHistoryPanel";

export default function DashboardHomePage() {
  const [selectedPlate, setSelectedPlate] = useState<string | null>(null);

  return (
    <div className="flex h-full w-full">
      <div className="h-full" style={{ width: "60%" }}>
        <CameraGrid />
      </div>
      <div className="h-full" style={{ width: "40%" }}>
        <AlertFeed onSelectPlate={(plate) => setSelectedPlate(plate)} />
      </div>

      <VehicleHistoryPanel
        plate={selectedPlate}
        onClose={() => setSelectedPlate(null)}
      />
    </div>
  );
}
