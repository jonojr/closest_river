// Umami events: what people do on the site, never where they are. No
// coordinates, search text or river names are sent; Umami already gets the
// page URL and a rough location from the IP address.
//
// Everything here is a no-op when Umami isn't on the page: switched off (no
// UMAMI_WEBSITE_ID), blocked, or not loaded yet. Each bundle gets its own copy
// of this module, so it only exports functions; project.js starts the
// page-wide listeners once.

export function track(name, data) {
  window.umami?.track(name, data);
}

// One map-setting event for every setting, keyed by the setting's name, so
// Umami's property breakdown shows each setting's values on their own.
export function trackSetting(setting, value) {
  track('map-setting', { [setting]: value });
}

// Umami's visit duration only spans page views, and a whole visit here is
// usually one page of map, so it would read 0 seconds. Report how long the
// page was actually on screen instead. Sent once, the first time the page is
// hidden (closed, or switched away from): that is often the last chance on
// phones. Time after coming back to the tab isn't counted.
const TIME_BUCKETS = [
  [30, '< 30s'],
  [2 * 60, '30s–2m'],
  [5 * 60, '2–5m'],
  [15 * 60, '5–15m'],
];

function timeBucket(seconds) {
  return TIME_BUCKETS.find(([limit]) => seconds < limit)?.[1] ?? '15m+';
}

function trackTimeOnPage() {
  let visibleSince =
    document.visibilityState === 'visible' ? performance.now() : null;
  let visibleMs = 0;
  let sent = false;

  function report() {
    if (visibleSince !== null) {
      visibleMs += performance.now() - visibleSince;
      visibleSince = null;
    }
    // Opened in a background tab and closed unseen: nothing to report.
    if (sent || visibleMs === 0) {
      return;
    }
    sent = true;
    track('time-on-page', { time: timeBucket(visibleMs / 1000) });
  }

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      report();
    } else if (visibleSince === null) {
      visibleSince = performance.now();
    }
  });
  window.addEventListener('pagehide', report);
}

// Links off the site, by host: which of Wikipedia, Wikidata, OpenStreetMap
// and so on people follow. One listener catches every link, including those
// built later in map popups. Middle clicks open links too.
function trackOutboundLinks() {
  function onClick(event) {
    if (event.type === 'auxclick' && event.button !== 1) {
      return;
    }
    const link = event.target.closest?.('a[href]');
    if (
      !link ||
      link.origin === location.origin ||
      !/^https?:/.test(link.protocol)
    ) {
      return;
    }
    track('outbound-link', { site: link.hostname.replace(/^www\./, '') });
  }

  document.addEventListener('click', onClick);
  document.addEventListener('auxclick', onClick);
}

export function trackEngagement() {
  trackTimeOnPage();
  trackOutboundLinks();
}
