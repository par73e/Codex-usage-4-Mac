import React from "react";

import reviewedSnapshot from "./data.json";
import { DataAppRuntime } from "./DataAppRuntime.jsx";
import { DashboardContent } from "./content/dashboard/DashboardContent.jsx";
import { ReportContent } from "./content/report/ReportContent.jsx";
import { useLiveUsage } from "./content/dashboard/use-live-usage.js";

export function App({ hosted = globalThis.location?.hostname.endsWith(".chatgpt.site") ?? false } = {}) {
  const live = useLiveUsage(reviewedSnapshot, !hosted && reviewedSnapshot.localUsage === true);
  return (
    <DataAppRuntime
      reviewedSnapshot={live.snapshot}
      localRefresh={live.control}
      DashboardContent={DashboardContent}
      ReportContent={ReportContent}
      hosted={hosted}
    />
  );
}
