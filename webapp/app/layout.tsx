import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DepotChargeDex — laadlocaties voor zwaar vervoer in Europa",
  description:
    "DepotChargeDex: kaart met publieke, semi-publieke en private laadlocaties voor zwaar vervoer in Europa. Operatorgegevens, vrachtlabels en laadpuntenregister, elk met bron en zekerheidsniveau. Inclusief 500 m-dekkingsanalyse.",
  openGraph: {
    title: "DepotChargeDex",
    description:
      "Publieke, semi-publieke en private laadlocaties voor zwaar vervoer — met bewijs per locatie.",
    type: "website",
    locale: "nl_NL",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="nl">
      <body className="antialiased">{children}</body>
    </html>
  );
}
