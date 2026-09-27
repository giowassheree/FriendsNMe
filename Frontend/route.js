// Walking routes drawn on the map: to a friend who needs help, or
// back to the party after "I'm OK". Loaded after map.js and uses its
// map, latestData, userMarker and metersBetween.
//
// Routes come from the public OpenStreetMap foot router run by
// FOSSGIS (fair use, no key). If it can't be reached we draw a
// straight line instead.

const ROUTER_URL = "https://routing.openstreetmap.de/routed-foot/route/v1/driving";
// Re-plan when either end has moved this far, at most this often.
const REROUTE_METERS = 20;
const REROUTE_MS = 20000;
const WALKING_METERS_PER_SECOND = 1.3;

const routeEls = {
  panel: document.getElementById("routePanel"),
  label: document.getElementById("routeLabel"),
  detail: document.getElementById("routeDetail"),
  mapsLink: document.getElementById("routeMapsLink"),
  clear: document.getElementById("routeClear"),
};

// { label, tone, getTarget(), from, to, fetchedAt, fitted }
let activeRoute = null;
let routeRequestId = 0;

map.on("load", () => {
  map.addSource("route", { type: "geojson", data: emptyCollection() });
  map.addLayer({
    id: "route-line",
    type: "line",
    source: "route",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": toneColor("far"), "line-width": 5, "line-opacity": 0.9 },
  });
});

// Where the route starts: this phone's live position if tracking,
// otherwise its last location saved on the server.
function routeOrigin() {
  if (userMarkerAdded) {
    const { lng, lat } = userMarker.getLngLat();
    return { latitude: lat, longitude: lng };
  }
  return latestData?.me?.location || null;
}

// target: () => { latitude, longitude } | null, re-read on every update
// so the route follows someone who is still moving.
function showRoute(label, tone, getTarget) {
  activeRoute = { label, tone, getTarget, from: null, to: null, fetchedAt: 0, fitted: false };
  updateRoute(true);
}

function clearRoute() {
  activeRoute = null;
  routeRequestId++;
  routeEls.panel.hidden = true;
  if (map.getSource("route")) map.getSource("route").setData(emptyCollection());
}

function googleMapsWalkingUrl(to) {
  // No origin: Google Maps uses the phone's own location.
  return `https://www.google.com/maps/dir/?api=1&destination=${to.latitude},${to.longitude}&travelmode=walking`;
}

// Called on every map refresh (map.js render) and when a route starts.
function updateRoute(force = false) {
  if (!activeRoute) return;

  const to = activeRoute.getTarget();
  const from = routeOrigin();

  routeEls.panel.hidden = false;
  routeEls.panel.dataset.tone = activeRoute.tone;
  routeEls.label.textContent = `Walking route to ${activeRoute.label}`;

  if (!to) {
    routeEls.detail.textContent = `We don't know where ${activeRoute.label} is right now.`;
    routeEls.mapsLink.hidden = true;
    return;
  }

  routeEls.mapsLink.hidden = false;
  routeEls.mapsLink.href = googleMapsWalkingUrl(to);

  if (!from) {
    routeEls.detail.textContent =
      "Tap “Use my location” to draw the route here, or open it in Google Maps.";
    return;
  }

  const moved =
    !activeRoute.from ||
    metersBetween(activeRoute.from, from) > REROUTE_METERS ||
    metersBetween(activeRoute.to, to) > REROUTE_METERS;
  const due = Date.now() - activeRoute.fetchedAt > REROUTE_MS;
  if (!force && !(moved && due)) return;

  activeRoute.from = from;
  activeRoute.to = to;
  activeRoute.fetchedAt = Date.now();
  drawRoute(from, to, activeRoute.tone);
}

async function drawRoute(from, to, tone) {
  const requestId = ++routeRequestId;
  let route;

  try {
    const url = `${ROUTER_URL}/${from.longitude},${from.latitude};${to.longitude},${to.latitude}` +
      "?overview=full&geometries=geojson";
    const response = await fetch(url);
    const data = await response.json();
    if (data.code !== "Ok" || !data.routes?.length) throw new Error(data.code);
    // The router snaps both ends onto the nearest path; join the
    // real start and end points so the line reaches both people.
    const path = data.routes[0].geometry.coordinates;
    route = {
      geometry: {
        type: "LineString",
        coordinates: [[from.longitude, from.latitude], ...path, [to.longitude, to.latitude]],
      },
      meters: data.routes[0].distance,
      straight: false,
    };
  } catch (routeError) {
    console.warn("Walking route unavailable, drawing a straight line:", routeError);
    route = {
      geometry: {
        type: "LineString",
        coordinates: [[from.longitude, from.latitude], [to.longitude, to.latitude]],
      },
      meters: metersBetween(from, to),
      straight: true,
    };
  }

  // A newer request (or clearRoute) replaced this one meanwhile.
  if (requestId !== routeRequestId || !activeRoute) return;

  const minutes = Math.max(1, Math.round(route.meters / WALKING_METERS_PER_SECOND / 60));
  routeEls.detail.textContent =
    `${Math.round(route.meters)} m · about ${minutes} min walk` +
    (route.straight ? " (straight line; walking directions unavailable)" : "");

  if (!mapLoaded) return;
  map.getSource("route").setData({
    type: "FeatureCollection",
    features: [{ type: "Feature", properties: {}, geometry: route.geometry }],
  });
  map.setPaintProperty("route-line", "line-color", toneColor(tone));
  map.setPaintProperty("route-line", "line-dasharray", route.straight ? [2, 2] : null);

  // Zoom to the whole route once; later re-plans leave the view alone.
  if (activeRoute.fitted) return;
  activeRoute.fitted = true;
  const bounds = new maplibregl.LngLatBounds(
    [from.longitude, from.latitude],
    [from.longitude, from.latitude]
  );
  route.geometry.coordinates.forEach((point) => bounds.extend(point));
  map.fitBounds(bounds, { padding: 60, maxZoom: 18 });
}

routeEls.clear.addEventListener("click", clearRoute);
