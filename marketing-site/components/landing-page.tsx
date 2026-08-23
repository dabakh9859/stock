"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import {
  ArrowDown, ArrowRight, BarChart3, Box, Check, ChevronRight, CircleUserRound,
  FileCheck2, Menu, PackageCheck, ScanLine, ShoppingBag, Sparkles, Truck, Users, X,
} from "lucide-react";
import type { ActivityProfile, SiteConfig } from "@/lib/site-config";

const money = new Intl.NumberFormat("fr-SN", { maximumFractionDigits: 0 });

function Mark({ name }: { name: string }) {
  return <span className="brand" aria-label={`${name}, nom de travail`}><svg className="brand-mark" viewBox="0 0 38 38" aria-hidden="true"><path d="M4 12 19 4l15 8-15 8L4 12Z" /><path d="m4 18 15 8 15-8v8l-15 8-15-8v-8Z" /></svg><span>{name}</span><small>nom de travail</small></span>;
}

function MiniProduct({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`mini-product ${compact ? "mini-product--compact" : ""}`} aria-label="Aperçu de l’application avec des données fictives">
      <header className="mini-product__top"><span className="mini-product__logo">S</span><b>Vue d’ensemble</b><span className="live-dot"><i /> données fictives</span></header>
      <div className="mini-product__body">
        <aside aria-hidden="true"><BarChart3 /><Box /><ShoppingBag /><Users /></aside>
        <div className="mini-product__content">
          <div className="mini-product__hello"><span>Bonjour Awa,</span><strong>votre boutique aujourd’hui.</strong></div>
          <div className="metric-line"><div><small>Ventes du jour</small><strong>{money.format(428500)} F</strong><span>12 ventes validées</span></div><div><small>Articles en stock</small><strong>1 248</strong><span>32 à surveiller</span></div></div>
          <div className="activity-line"><div className="activity-chart" aria-hidden="true">{[36, 52, 44, 72, 62, 88, 75].map((h, i) => <i key={i} style={{ height: `${h}%` }} />)}</div><div className="activity-feed"><b>Derniers mouvements</b><span><i className="mint" />Vente #1842 <strong>89 000 F</strong></span><span><i className="orange" />Arrivage reçu <strong>+24</strong></span></div></div>
        </div>
      </div>
    </div>
  );
}

const journey = [
  { title: "L’arrivage part.", body: "La commande est suivie dès sa préparation." },
  { title: "Le stock se met à jour.", body: "Les quantités entrent une fois, au bon endroit." },
  { title: "La vente relie tout.", body: "Stock, paiement et historique bougent ensemble." },
  { title: "La vue d’ensemble apparaît.", body: "Chaque mouvement devient une information utile." },
];

function CommerceJourney() {
  return (
    <section className="world-story" id="produit" aria-label="Démonstration du fonctionnement de l’application">
      <div className="world-pin">
        <div className="journey-copy"><div className="journey-progress" aria-hidden="true"><i /></div><div className="journey-copy-stack">{journey.map((step, index) => <article className={`journey-step journey-step--${index}`} key={step.title}><h2>{step.title}</h2><p>{step.body}</p></article>)}</div><div className="journey-count" aria-hidden="true"><strong>01</strong><span>/ 04</span></div></div>
        <div className="world-stage">
          <div className="world-orbit orbit-one" aria-hidden="true" /><div className="world-orbit orbit-two" aria-hidden="true" />
          <svg className="world-lines" viewBox="0 0 1000 700" preserveAspectRatio="none" aria-hidden="true"><path className="line-supplier" d="M140,470 C280,380 350,370 500,390" /><path className="line-customer" d="M510,400 C650,415 735,450 865,500" /><path className="line-app" d="M500,360 C500,255 535,205 590,155" /></svg>
          <div className="node-label label-supplier"><Truck />Fournisseur</div>
          <div className="supplier-node"><Image src="/images/supplier-world-2-5d.png" alt="Un fournisseur sénégalais prépare une livraison depuis son dépôt" fill sizes="(max-width: 899px) 48vw, 32vw" /></div>
          <div className="node-label label-shop"><Box />Boutique</div>
          <div className="shop-node"><Image src="/images/commerce-world-2-5d.png" alt="Une commerçante sénégalaise gère sa boutique depuis son ordinateur" fill sizes="(max-width: 899px) 72vw, 42vw" /><span className="scanner-beam" aria-hidden="true" /></div>
          <div className="sale-cluster-node"><Image src="/images/customer-sale-2-5d.png" alt="Une cliente sénégalaise reçoit ses achats et sa facture" fill sizes="(max-width: 899px) 34vw, 17vw" /></div>
          <div className="customer-node" aria-label="Client servi"><div className="customer-avatar"><CircleUserRound /></div><div><b>Moussa</b><span>Client servi</span></div><Check /></div>
          <div className="parcel parcel-a" aria-hidden="true"><PackageCheck /></div><div className="parcel parcel-b" aria-hidden="true"><PackageCheck /></div><div className="parcel parcel-c" aria-hidden="true"><PackageCheck /></div>
          <div className="event event-order"><Truck /><span><small>Commande fournisseur</small><b>Prête à partir</b></span><Check /></div>
          <div className="event event-stock"><Box /><span><small>Stock disponible</small><b><em>1 224</em> → 1 248</b></span><strong>+24</strong></div>
          <div className="event event-sale"><ShoppingBag /><span><small>Vente #1842</small><b>89 000 F</b></span><strong>Payée</strong></div>
          <div className="event event-invoice"><FileCheck2 /><span><small>Facture</small><b>Créée automatiquement</b></span><Check /></div>
          <div className="app-hub"><MiniProduct compact /></div>
          <div className="network-note"><Sparkles /><span><b>Tout est relié.</b><small>Données de démonstration</small></span></div>
          <div className="demo-flag">Données de démonstration</div>
        </div>
      </div>
    </section>
  );
}

const productLines: Record<string, string[]> = {
  telephonie: ["Galaxy A55 · IMEI 3520…", "iPhone 15 · IMEI 3591…", "Chargeur USB-C · 28 pièces"],
  mode: ["Ensemble Ndar · M / Indigo", "Boubou Lina · L / Sable", "Mules Dakar · 39 / Noir"],
  alimentation: ["Lait en poudre · lot A2408", "Jus de bissap · 46 bouteilles", "Riz parfumé · 18 sacs"],
  cosmetique: ["Parfum Saly · 50 ml", "Crème karité · lot C128", "Mèche bouclée · Noir"],
  general: ["Article A · 42 pièces", "Article B · 18 pièces", "Article C · 9 pièces"],
};

function ProfileSelector({ profiles }: { profiles: ActivityProfile[] }) {
  const visible = profiles.slice(0, 5); const [active, setActive] = useState(visible[0]?.code || "general"); const selected = visible.find((profile) => profile.code === active) || visible[0];
  return <div className="profile-workbench"><div className="profile-list" role="tablist" aria-label="Profils métiers">{visible.map((profile) => <button key={profile.code} role="tab" aria-selected={profile.code === active} className={profile.code === active ? "active" : ""} onClick={() => setActive(profile.code)}><span>{profile.label}</span><ChevronRight /></button>)}</div>{selected && <div className="profile-product" role="tabpanel"><div className="profile-product__head"><span><ScanLine />Catalogue adapté</span><small>Démonstration</small></div><div className="profile-product__title"><div><h3>{selected.label}</h3><p>{selected.summary}</p></div><span className="profile-glyph">{selected.code === "telephonie" ? <ScanLine /> : selected.code === "mode" ? <ShoppingBag /> : selected.code === "alimentation" ? <PackageCheck /> : <Box />}</span></div><div className="catalog-lines">{(productLines[selected.code] || productLines.general).map((product, index) => <div key={product}><span className="catalog-thumb">{index + 1}</span><b>{product}</b><small>{index === 2 ? "À suivre" : "En stock"}</small></div>)}</div><div className="feature-rail">{selected.examples.map((example) => <span key={example}><Check />{example}</span>)}</div></div>}</div>;
}

export function LandingPage({ config }: { config: SiteConfig }) {
  const root = useRef<HTMLDivElement>(null); const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    let dispose = () => {};
    const start = async () => {
      const { gsap } = await import("gsap"); const { ScrollTrigger } = await import("gsap/ScrollTrigger"); gsap.registerPlugin(ScrollTrigger);
      const context = gsap.context(() => {
        const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        if (!reduceMotion) { gsap.from(".hero-copy > *", { y: 32, autoAlpha: 0, duration: 0.9, stagger: 0.09, ease: "expo.out" }); gsap.from(".hero-world", { clipPath: "inset(14% 14% 16% 14%)", scale: 0.92, filter: "blur(10px)", duration: 1.25, delay: 0.08, ease: "expo.out" }); gsap.from(".hero-float", { y: 22, autoAlpha: 0, duration: 0.85, delay: 0.55, stagger: 0.12, ease: "expo.out" }); }
        if (reduceMotion) { const steps = gsap.utils.toArray<HTMLElement>(".journey-step"); gsap.set(steps.slice(0, 3), { autoAlpha: 0 }); gsap.set(steps[3], { autoAlpha: 1 }); const count = document.querySelector(".journey-count strong"); if (count) count.textContent = "04"; }
        const mm = gsap.matchMedia();
        mm.add("(min-width: 900px) and (prefers-reduced-motion: no-preference)", () => {
          const steps = gsap.utils.toArray<HTMLElement>(".journey-step"); gsap.set(steps.slice(1), { autoAlpha: 0, y: 24 });
          gsap.set([".supplier-node", ".label-supplier", ".event-order"], { autoAlpha: 0, x: -50, scale: 0.92 }); gsap.set([".shop-node", ".label-shop"], { autoAlpha: 0, scale: 0.88, y: 30 }); gsap.set([".customer-node", ".sale-cluster-node", ".event-sale", ".event-invoice"], { autoAlpha: 0, x: 45, scale: 0.9 }); gsap.set([".event-stock", ".app-hub", ".network-note"], { autoAlpha: 0, y: 24, scale: 0.94 }); gsap.set(".parcel", { autoAlpha: 0, x: -160, y: 10, scale: 0.72 }); gsap.set(".world-lines path", { strokeDasharray: 1000, strokeDashoffset: 1000 });
          const count = document.querySelector(".journey-count strong"); const showStep = (index: number) => { steps.forEach((step, i) => gsap.to(step, { autoAlpha: i === index ? 1 : 0, y: i === index ? 0 : i < index ? -18 : 18, duration: 0.22, overwrite: true })); if (count) count.textContent = `0${index + 1}`; };
          const timeline = gsap.timeline({ paused: true });
          timeline
            .call(() => showStep(0))
            .to([".supplier-node", ".label-supplier"], { autoAlpha: 1, x: 0, scale: 1, duration: 0.7, ease: "expo.out" })
            .to(".event-order", { autoAlpha: 1, x: 0, scale: 1, duration: 0.45 }, "<.2")
            .to({}, { duration: 1.15 })
            .call(() => showStep(1))
            .to(".line-supplier", { strokeDashoffset: 0, duration: 0.8 })
            .to(".shop-node", { autoAlpha: 1, y: 0, scale: 1, duration: 0.8, ease: "expo.out" }, "<")
            .to(".label-shop", { autoAlpha: 1, scale: 1, y: 0, duration: 0.4 }, "<.2")
            .to(".parcel", { autoAlpha: 1, x: 0, y: 0, scale: 1, duration: 0.75, stagger: 0.09, ease: "power2.inOut" }, "<.1")
            .to(".event-order", { autoAlpha: 0, y: -16, duration: 0.25 })
            .to(".scanner-beam", { autoAlpha: 1, scaleX: 1, duration: 0.35 })
            .to(".event-stock", { autoAlpha: 1, y: 0, scale: 1, duration: 0.5, ease: "expo.out" }, "<")
            .to(".parcel", { autoAlpha: 0, y: 18, scale: 0.7, duration: 0.35 }, "<.2")
            .to({}, { duration: 1.15 })
            .call(() => showStep(2))
            .to([".customer-node", ".sale-cluster-node"], { autoAlpha: 1, x: 0, scale: 1, duration: 0.6, ease: "expo.out" })
            .to(".line-customer", { strokeDashoffset: 0, duration: 0.7 }, "<")
            .to(".event-sale", { autoAlpha: 1, x: 0, scale: 1, duration: 0.48 }, "<.2")
            .to(".event-invoice", { autoAlpha: 1, x: 0, scale: 1, duration: 0.48 }, ">.18")
            .to(".scanner-beam", { autoAlpha: 0, duration: 0.2 }, "<")
            .to({}, { duration: 1.15 })
            .call(() => showStep(3))
            .to(".line-app", { strokeDashoffset: 0, duration: 0.6 })
            .to(".app-hub", { autoAlpha: 1, y: 0, scale: 1, duration: 0.8, ease: "expo.out" }, "<")
            .to([".event-stock", ".event-sale", ".event-invoice"], { y: -10, duration: 0.35, stagger: 0.05 }, "<.25")
            .to(".world-orbit", { autoAlpha: 1, scale: 1, duration: 0.6, stagger: 0.08 })
            .to(".network-note", { autoAlpha: 1, y: 0, scale: 1, duration: 0.5 }, "<.15")
            .to(".supplier-node", { xPercent: -18, y: 22, scale: 0.92, duration: 0.65, ease: "power2.inOut" })
            .to(".shop-node", { xPercent: 5, y: 8, scale: 0.96, duration: 0.65, ease: "power2.inOut" }, "<")
            .to(".label-supplier", { x: -54, y: 16, duration: 0.65, ease: "power2.inOut" }, "<")
            .to(".label-shop", { x: 28, duration: 0.65, ease: "power2.inOut" }, "<");
          timeline.eventCallback("onUpdate", () => gsap.set(".journey-progress i", { scaleY: timeline.progress() }));
          ScrollTrigger.create({ trigger: ".world-story", start: "top 72%", end: "bottom 20%", onEnter: () => timeline.restart(), onLeave: () => timeline.pause(), onEnterBack: () => timeline.progress() === 1 ? timeline.restart() : timeline.resume(), onLeaveBack: () => timeline.pause() });
        });
        mm.add("(max-width: 899px) and (prefers-reduced-motion: no-preference)", () => {
          const steps = gsap.utils.toArray<HTMLElement>(".journey-step"); const count = document.querySelector(".journey-count strong");
          const showStep = (index: number) => { steps.forEach((step, i) => gsap.to(step, { autoAlpha: i === index ? 1 : 0, y: i === index ? 0 : i < index ? -12 : 12, duration: .28, overwrite: true })); if (count) count.textContent = `0${index + 1}`; };
          gsap.set(steps.slice(1), { autoAlpha: 0, y: 12 });
          gsap.set([".supplier-node", ".shop-node", ".sale-cluster-node", ".app-hub", ".event-order", ".event-stock", ".event-sale", ".event-invoice", ".network-note"], { "--stage-opacity": 0 });
          gsap.set([".label-supplier", ".label-shop"], { autoAlpha: 0 });
          const timeline = gsap.timeline({ paused: true })
            .call(() => showStep(0))
            .to([".supplier-node", ".event-order"], { "--stage-opacity": 1, duration: .65, ease: "expo.out" })
            .to(".label-supplier", { autoAlpha: 1, duration: .3 }, "<.15")
            .to({}, { duration: 1.2 })
            .call(() => showStep(1))
            .to(".supplier-node", { "--stage-opacity": .38, "--stage-scale": .9, duration: .4 })
            .to([".shop-node", ".event-stock"], { "--stage-opacity": 1, "--stage-scale": 1, duration: .65, ease: "expo.out" }, "<")
            .to(".label-shop", { autoAlpha: 1, duration: .3 }, "<.15")
            .to(".event-order", { "--stage-opacity": 0, duration: .25 }, "<")
            .to({}, { duration: 1.2 })
            .call(() => showStep(2))
            .to(".shop-node", { "--stage-opacity": .66, "--stage-scale": .92, duration: .4 })
            .to([".sale-cluster-node", ".event-sale", ".event-invoice"], { "--stage-opacity": 1, "--stage-scale": 1, duration: .65, stagger: .05, ease: "expo.out" }, "<")
            .to({}, { duration: 1.2 })
            .call(() => showStep(3))
            .to([".supplier-node", ".shop-node", ".sale-cluster-node"], { "--stage-opacity": .62, "--stage-scale": .9, duration: .45 })
            .to([".app-hub", ".network-note"], { "--stage-opacity": 1, "--stage-scale": 1, duration: .7, ease: "expo.out" }, "<")
            .to([".event-stock", ".event-sale", ".event-invoice"], { "--stage-opacity": .6, duration: .35 }, "<");
          timeline.eventCallback("onUpdate", () => gsap.set(".journey-progress i", { scaleY: timeline.progress() }));
          ScrollTrigger.create({ trigger: ".world-story", start: "top 78%", end: "bottom 15%", onEnter: () => timeline.restart(), onLeave: () => timeline.pause(), onEnterBack: () => timeline.progress() === 1 ? timeline.restart() : timeline.resume(), onLeaveBack: () => timeline.pause() });
        });
        dispose = () => { mm.revert(); context.revert(); };
      }, root);
    }; start(); return () => dispose();
  }, []);

  return (
    <div ref={root}>
      <header className="site-header"><a href="#top"><Mark name={config.appName} /></a><nav aria-label="Navigation principale"><a href="#produit">Le produit</a><a href="#metiers">Les métiers</a><a href="#assistant">L’assistant</a></nav><div className="header-actions"><a href={config.loginUrl}>Se connecter</a><a className="button button--small" href={config.loginUrl}>Ouvrir l’application <ArrowRight /></a></div><button className="menu-button" aria-label={menuOpen ? "Fermer le menu" : "Ouvrir le menu"} aria-expanded={menuOpen} onClick={() => setMenuOpen(!menuOpen)}>{menuOpen ? <X /> : <Menu />}</button>{menuOpen && <div className="mobile-menu"><a href="#produit" onClick={() => setMenuOpen(false)}>Le produit</a><a href="#metiers" onClick={() => setMenuOpen(false)}>Les métiers</a><a href="#assistant" onClick={() => setMenuOpen(false)}>L’assistant</a><a href={config.loginUrl}>Se connecter</a></div>}</header>
      <main>
        <section className="hero" id="top"><div className="hero-copy"><h1>Tout votre commerce. <em>Dans une seule vue.</em></h1><p>Stock, ventes, clients et fournisseurs avancent ensemble — du comptoir à la réserve.</p><div className="hero-actions"><a className="button" href={config.loginUrl}>Découvrir l’application <ArrowRight /></a><a className="plain-link" href="#produit">Voir le parcours <ArrowDown /></a></div><div className="connection-status"><i className={config.connected ? "is-live" : ""} />{config.connected ? "Configuration locale connectée" : "Backend local hors ligne"} · données fictives</div></div><div className="hero-world"><div className="hero-disc" aria-hidden="true" /><Image className="hero-world__image" src="/images/commerce-world-2-5d.png" alt="Une commerçante sénégalaise utilise l’application dans sa boutique" fill priority sizes="(max-width: 899px) 94vw, 58vw" /><div className="hero-location-note"><span className="senegal-colors"><i /><i /><i /></span>Conçu au Sénégal · démonstration</div><div className="hero-float hero-float--stock"><Box /><span><small>Stock fictif</small><b>1 248 articles</b></span><strong>+24</strong></div><div className="hero-float hero-float--sale"><ShoppingBag /><span><small>Vente fictive</small><b>89 000 F</b></span><Check /></div><div className="hero-float hero-float--sync"><Sparkles /><span><b>Aperçu de démonstration</b><small>{config.connected ? "configuration connectée" : "backend hors ligne"}</small></span></div></div><div className="hero-scroll"><span>Faites défiler</span><i /></div></section>
        <section className="promise"><h2>Une action en boutique.<br />Toute l’activité se met en ordre.</h2><div className="promise-flow" aria-label="Stock, ventes, clients et fournisseurs sont reliés"><span><Truck />Fournisseur</span><i /><span><Box />Stock</span><i /><span><ShoppingBag />Vente</span><i /><span><Users />Client</span><i /><span><BarChart3 />Pilotage</span></div></section>
        <CommerceJourney />
        <section className="profiles" id="metiers"><div className="profiles-intro"><h2>La même simplicité.<br /><em>Votre métier en plus.</em></h2><p>IMEI, tailles, couleurs, lots ou unités : l’application s’adapte à ce que vous vendez.</p></div><ProfileSelector profiles={config.profiles} /></section>
        <section className="assistant-section" id="assistant"><div className="assistant-copy"><h2>Demandez.<br />L’assistant prépare.</h2><p>Vous gardez la décision. Il rassemble les informations et propose l’action à valider.</p><div className="permission-note"><Check /> Permissions et règles métier respectées</div></div><div className="assistant-demo" aria-label="Exemple fictif de conversation avec l’assistant"><div className="assistant-demo__bar"><span><Sparkles /> Assistant</span><small>Données de démonstration</small></div><div className="message message--user">Quels produits dois-je recommander cette semaine ?</div><div className="message message--assistant"><span className="assistant-orb"><Sparkles /></span><div><p>J’ai repéré 3 références sous leur seuil habituel.</p><div className="reorder-line"><i>JD</i><span><b>Jus de bissap</b><small>14 unités restantes</small></span><strong>+24</strong></div><div className="reorder-line"><i>RP</i><span><b>Riz parfumé</b><small>8 sacs restants</small></span><strong>+12</strong></div><button>Préparer le bon de commande <ArrowRight /></button></div></div><div className="human-check"><FileCheck2 /><span><b>À valider par Awa</b><small>Aucune commande ne part sans confirmation.</small></span></div></div></section>
        <section className="final-cta"><div className="final-loop" aria-hidden="true"><Truck /><i /><Box /><i /><ShoppingBag /><i /><BarChart3 /></div><h2>Votre commerce est déjà vivant.<br /><em>Donnez-lui une vue d’ensemble.</em></h2><a className="button button--light" href={config.loginUrl}>Ouvrir l’application <ArrowRight /></a><p>Interface de démonstration · aucune donnée client réelle</p></section>
      </main>
      <footer><Mark name={config.appName} /><p>Gestion commerciale pour les commerces du Sénégal.</p><a href={config.loginUrl}>Se connecter <ArrowRight /></a></footer>
    </div>
  );
}
