import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Laadlocaties zwaar vervoer — publiek, semi-publiek en privaat",
  description:
    "Kaart met publieke, semi-publieke en private laadlocaties voor zwaar vervoer in Nederland. Privaat bewijs uit vergunningen, vakpers en archiefonderzoek; publiek uit het laadpuntenregister. Elke locatie met bron en zekerheidsniveau.",
  openGraph: {
    title: "Depotlaadpalen",
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
