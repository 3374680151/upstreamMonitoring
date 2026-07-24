import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { OverviewPage } from "@/pages/OverviewPage";
import { ChannelsPage } from "@/pages/ChannelsPage";
import { ChannelDetailPage } from "@/pages/ChannelDetailPage";
import { PlaceholderPage } from "@/pages/PlaceholderPage";

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<OverviewPage />} />
        <Route path="/channels" element={<ChannelsPage />} />
        <Route path="/channels/:id" element={<ChannelDetailPage />} />
        <Route
          path="/logs"
          element={
            <PlaceholderPage
              title="请求日志"
              description="全量请求记录：2xx / 4xx / 5xx，含 request_id 与上游错误体。"
            />
          }
        />
        <Route
          path="/probes"
          element={
            <PlaceholderPage
              title="监测样本"
              description="定时探测 /v1/models 与轻量 chat，统计可用率与延迟。"
            />
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}
