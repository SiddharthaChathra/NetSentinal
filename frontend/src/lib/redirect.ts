/**
 * "Send me back where I was going" support for the login gate.
 *
 * Kept free of React imports so the rules can be exercised directly by
 * scripts/verify_auth_coldstart.mjs — an open-redirect bug here would be a
 * real vulnerability, and it should not be reachable only through a rendered
 * component.
 */

/** The only routes reachable without a session. */
export const PUBLIC_ROUTES = ["/auth"];

/** Where to land after sign-in when there is no remembered destination. */
export const DEFAULT_LANDING = "/";

/** Where an account with no registered agent lands instead: the dashboard
 *  has nothing to show it yet. */
export const SETUP_ROUTE = "/getting-started";

export const isPublicRoute = (pathname: string) =>
  PUBLIC_ROUTES.some((route) => pathname === route || pathname.startsWith(`${route}/`));

/**
 * Deep links round-trip through the URL (`/auth?redirect=…`) so the
 * destination survives a full page load, and are mirrored into sessionStorage
 * so they also survive a trip through an email confirmation link, which comes
 * back on a URL we do not control.
 */
const PENDING_KEY = "netsentinel_redirect_after_login";

export const rememberDestination = (destination: string) => {
  try { sessionStorage.setItem(PENDING_KEY, destination); } catch { /* private mode */ }
};

export const takeRememberedDestination = (): string | null => {
  try {
    const value = sessionStorage.getItem(PENDING_KEY);
    sessionStorage.removeItem(PENDING_KEY);
    return value;
  } catch {
    return null;
  }
};

/**
 * Only same-origin paths are ever followed.
 *
 * Without this, `/auth?redirect=https://evil.example` would turn the login
 * page into an open redirect: an attacker sends that link, the victim really
 * does sign in to NetSentinel, and is then bounced to a site of the
 * attacker's choosing with the credibility of having just come from here.
 *
 * Rejected: absolute URLs, protocol-relative `//host` (which the browser
 * treats as absolute), anything not starting with `/`, and `/auth` itself
 * (which would bounce the user straight back to the form they just cleared).
 */
export const safeRedirect = (value: string | null | undefined): string => {
  if (!value) return DEFAULT_LANDING;
  if (!value.startsWith("/")) return DEFAULT_LANDING;
  if (value.startsWith("//")) return DEFAULT_LANDING;
  // Backslashes are normalised to forward slashes by some browsers, so
  // "/\evil.example" can escape the origin too.
  if (value.startsWith("/\\")) return DEFAULT_LANDING;
  if (isPublicRoute(value.split("?")[0])) return DEFAULT_LANDING;
  return value;
};
