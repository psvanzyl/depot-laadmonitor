import type { Metadata, Viewport } from "next";
import "./globals.css";

/**
 * Runs before first paint. Light is the default even when the OS prefers dark;
 * only a stored "dark", or "system" on a dark OS, turns the class on, so the
 * page never flashes the wrong theme. Mirrors paint() in ThemeToggle.
 */
const THEME_INIT = `(function(){try{var m=localStorage.getItem("depotdex-theme");if(m!=="light"&&m!=="dark"&&m!=="system")m="light";var d=m==="dark"||(m==="system"&&window.matchMedia("(prefers-color-scheme: dark)").matches);var r=document.documentElement;r.classList.toggle("dark",d);r.style.colorScheme=d?"dark":"light";}catch(e){}})();`;

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
    <html lang="nl" suppressHydrationWarning>
      <body className="antialiased">
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT }} />
        {children}
      </body>
    </html>
  );
}
