"use client";

import { useEffect, useState } from "react";
import { Joyride, EventData, STATUS, Step } from "react-joyride";
import { usePathname } from "next/navigation";
import { useAuth } from "@/context/AuthContext";

/**
 * The tour used to fire on the first visit to the pre-login homepage. There is
 * no pre-login homepage any more, so it fires on the first successful sign-in
 * or sign-up instead.
 *
 * "First" is decided by the SERVER, per account
 * (user_profiles.onboarding_completed_at), not by localStorage: a user who
 * took the tour on their laptop is not shown it again when they sign in on
 * their phone. `shouldShowTour` already folds in the local same-device cache,
 * which exists only to stop the tour flashing while the backend wakes up.
 *
 * It still waits for the dashboard, because that is where its targets are.
 */
export default function OnboardingTour() {
  const [run, setRun] = useState(false);
  const pathname = usePathname();
  const { user, shouldShowTour, markTourSeen } = useAuth();

  useEffect(() => {
    if (!user || !shouldShowTour || pathname !== "/") {
      setRun(false);
      return;
    }
    // Short delay to let the dashboard mount, so the step targets exist.
    const timer = setTimeout(() => setRun(true), 1000);
    return () => clearTimeout(timer);
  }, [user, shouldShowTour, pathname]);

  const handleJoyrideCallback = (data: EventData) => {
    const { status } = data;
    const finishedStatuses: string[] = [STATUS.FINISHED, STATUS.SKIPPED];

    if (finishedStatuses.includes(status)) {
      setRun(false);
      // Records it against the account, so it does not replay on another
      // device. Failure is non-fatal: the local flag still suppresses it here.
      void markTourSeen();
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
