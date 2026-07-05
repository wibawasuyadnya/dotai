import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DotAI",
  description: "Multi-agent workspace for the DotAI terminal agent",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
