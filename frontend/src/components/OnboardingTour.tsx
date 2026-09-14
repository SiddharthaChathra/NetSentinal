"use client";

import { useEffect, useState } from "react";
import { Joyride, EventData, STATUS, Step } from "react-joyride";
import { usePathname } from "next/navigation";

export default function OnboardingTour() {
  const [run, setRun] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    // Only run the tour if they haven't seen it, and only on the home page initially
    const tourDone = localStorage.getItem("netsentinel_onboarding_done");
    if (!tourDone && pathname === "/") {
      // Short delay to let the UI mount fully
      const timer = setTimeout(() => {
        setRun(true);
      }, 1000);
      return () => clearTimeout(timer);
    }
  }, [pathname]);

  const handleJoyrideCallback = (data: EventData) => {
    const { status } = data;
    const finishedStatuses: string[] = [STATUS.FINISHED, STATUS.SKIPPED];

    if (finishedStatuses.includes(status)) {
      setRun(false);
      localStorage.setItem("netsentinel_onboarding_done", "true");
    }
  };

  const steps: Step[] = [
    {
      target: "body",
      placement: "center",
      title: "Welcome to NetSentinel",
      content: "Let's take a quick tour of your network monitoring dashboard.",
    },
    {
      target: '[data-tour="dashboard-title"]',
      title: "Global Overview",
      content: "Here you can see a high-level summary of your fleet's health, latency, and any active incidents.",
      placement: "bottom",
    },
    {
      target: '[href="/backup"]',
      title: "Backup Readiness",
      content: "Ensure your critical targets meet their SLAs and simulate disaster scenarios.",
      placement: "right",
    },
    {
      target: '[href="/getting-started"]',
      title: "Getting Started",
      content: "Follow the setup guide to deploy agents and start monitoring your own devices.",
      placement: "right",
    }
  ];

  const [activeSteps, setActiveSteps] = useState<Step[]>([]);

  useEffect(() => {
    if (run) {
      // Check which targets actually exist in the DOM to avoid Joyride crashes
      const validSteps = steps.filter(step => {
        if (step.target === "body") return true;
        return document.querySelector(step.target as string) !== null;
      });
      setActiveSteps(validSteps);
    }
  }, [run]);

  if (!run || activeSteps.length === 0) return null;

  return (
    <Joyride
      steps={activeSteps}
      run={run}
      continuous={true}
      scrollToFirstStep={true}
      onEvent={handleJoyrideCallback}
      styles={{
        floater: {
          zIndex: 10000,
        },
        tooltip: {
          backgroundColor: '#0a0f1c',
          color: '#f8fafc',
          border: '1px solid rgba(255, 255, 255, 0.1)',
          borderRadius: '16px',
          boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5)',
        },
        tooltipContainer: {
          textAlign: 'left',
        },
        tooltipTitle: {
          fontSize: '18px',
          fontWeight: 700,
          color: '#ffffff',
          marginBottom: '8px',
        },
        tooltipContent: {
          fontSize: '14px',
          color: '#94a3b8',
          lineHeight: '1.5',
        },
        buttonPrimary: {
          backgroundColor: '#06d6d6',
          color: '#0f172a',
          fontWeight: 600,
          borderRadius: '8px',
          padding: '8px 16px',
          outline: 'none',
        },
        buttonBack: {
          color: '#94a3b8',
          marginRight: '8px',
        },
        buttonSkip: {
          color: '#64748b',
          fontSize: '14px',
        }
      }}
    />
  );
}
