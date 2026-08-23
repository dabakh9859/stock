export type ActivityProfile = {
  code: string;
  label: string;
  summary: string;
  examples: string[];
};

export type SiteConfig = {
  appName: string;
  appUrl: string;
  loginUrl: string;
  currency: string;
  locale: string;
  profiles: ActivityProfile[];
  connected: boolean;
};

const fallback: SiteConfig = {
  appName: "Stock",
  appUrl: "http://localhost:8000",
  loginUrl: "http://localhost:8000/login",
  currency: "XOF",
  locale: "fr-SN",
  connected: false,
  profiles: [
    { code: "telephonie", label: "Téléphonie & électronique", summary: "Suivez chaque appareil, sa garantie et son passage en atelier.", examples: ["IMEI", "garanties"] },
    { code: "mode", label: "Mode & textile", summary: "Gérez tailles, couleurs et stock de chaque déclinaison.", examples: ["tailles", "couleurs"] },
    { code: "alimentation", label: "Alimentation & boissons", summary: "Comptez à l’unité ou au poids et surveillez les péremptions.", examples: ["lots", "réassort"] },
    { code: "general", label: "Commerce général", summary: "Un suivi simple et fiable pour un catalogue varié.", examples: ["quantités", "ventes"] }
  ]
};

export async function getSiteConfig(): Promise<SiteConfig> {
  const base = process.env.INTERNAL_API_BASE || "http://localhost:8000";
  try {
    const response = await fetch(`${base}/api/public/site-config`, {
      next: { revalidate: 60 },
      signal: AbortSignal.timeout(2500),
    });
    if (!response.ok) return fallback;
    return { ...(await response.json()), connected: true } as SiteConfig;
  } catch {
    return fallback;
  }
}
