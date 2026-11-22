import type { Metadata } from "next";
import "../lib/report.css";
import "./app.css";

export const metadata: Metadata = {
  title: "SREGym × ClickStack",
  description: "Diagnosis success by model, arm and skill tier, served from ClickHouse",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
