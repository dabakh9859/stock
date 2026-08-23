import { LandingPage } from "@/components/landing-page";
import { getSiteConfig } from "@/lib/site-config";

export default async function Home() {
  const config = await getSiteConfig();
  return <LandingPage config={config} />;
}
