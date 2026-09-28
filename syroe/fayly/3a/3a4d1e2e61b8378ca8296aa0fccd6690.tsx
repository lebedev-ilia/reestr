import { HeroSection } from "@/components/landing/hero";
import { HowItWorks } from "@/components/landing/how-it-works";
import { FeaturesGrid } from "@/components/landing/features-grid";
import { LandingNav } from "@/components/landing/nav";
import { LandingFooter } from "@/components/landing/footer";

export default function LandingPage() {
  return (
    <main className="min-h-screen bg-[--bg]">
      <LandingNav />
      <HeroSection />
      <HowItWorks />
      <FeaturesGrid />
      <LandingFooter />
    </main>
  );
}
