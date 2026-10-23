import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Depotlaadpalen — private laadlocaties bij logistieke bedrijven",
  description:
    "Kaart met private depotlaadlocaties in Nederland, afgeleid uit vergunningen, vakpers en archiefonderzoek. Elke locatie met bron en zekerheidsniveau.",
  openGraph: {
    title: "Depotlaadpalen",
    description:
      "Private laadlocaties bij logistieke bedrijven in Nederland — met bewijs per locatie.",
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
