import type { Metadata } from "next";
import { Afacad, Sora } from "next/font/google";
import "./globals.css";

const display = Sora({ subsets: ["latin"], variable: "--font-display" });
const body = Afacad({ subsets: ["latin"], variable: "--font-body" });

export const metadata: Metadata = {
  title: "Stock (nom de travail) — Votre commerce, enfin réuni",
  description: "Pilotez stock, ventes, clients et fournisseurs depuis un seul espace pensé pour les commerces du Sénégal.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="fr">
      <body className={`${display.variable} ${body.variable}`}>
        <div
          className="design-contract"
          aria-hidden="true"
          dangerouslySetInnerHTML={{
            __html: `<!--
              THESIS: Le commerce devient un système de pilotage net et lisible; refus du hero SaaS décoratif et des cartes interchangeables.
              OWN-WORLD: système d'exploitation commercial en blanc, bleu nuit presque noir et bleu signal; grilles d'exploitation, surfaces papier et panneaux de contrôle précis.
              STORY: observer la boutique, suivre une opération, puis lire l'ensemble comme un tableau de bord opérationnel.
              FIRST VIEWPORT: manifeste court, scène de boutique encadrée comme un écran système, données essentielles en barres de commande.
              FORM: operating system du commerce, direction blanche et bleu nuit explicitement demandée par l'utilisateur, inspiration de niveau AtomOS sans copie.
              FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
            -->`,
          }}
        />
        {children}
      </body>
    </html>
  );
}
