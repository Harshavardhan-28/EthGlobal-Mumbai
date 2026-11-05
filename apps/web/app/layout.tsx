import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Working Capital Desk",
  description: "Receivables-backed credit for autonomous agent teams.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
