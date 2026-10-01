import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";
import "./index.css";
import { AuthProvider, useAuth } from "./auth";
import Layout from "./components/Layout";
import CandidateJoin from "./pages/CandidateJoin";
import Dashboard from "./pages/Dashboard";
import InterviewDetail from "./pages/InterviewDetail";
import Login from "./pages/Login";
import Schedule from "./pages/Schedule";
import Templates from "./pages/Templates";

function Protected({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="join-page">Loading…</div>;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function LoginGate() {
  const { user, loading } = useAuth();
  if (loading) return <div className="join-page">Loading…</div>;
  if (user) return <Navigate to="/" replace />;
  return <Login />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginGate />} />
          <Route path="/join/:token" element={<CandidateJoin />} />
          <Route
            element={
              <Protected>
                <Layout />
              </Protected>
            }
          >
            <Route path="/" element={<Dashboard />} />
            <Route path="/schedule" element={<Schedule />} />
            <Route path="/interviews/:id" element={<InterviewDetail />} />
            <Route path="/templates" element={<Templates />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  </StrictMode>,
);
