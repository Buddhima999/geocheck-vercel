// Thin wrapper around the Google Maps JavaScript API: loads it lazily (only
// once, only when a map is first opened) using the browser key served from
// /api/config, and exposes a couple of drawing helpers used by app.js.
const GMaps = (() => {
  let loadPromise = null;

  function load(key) {
    if (loadPromise) return loadPromise;
    loadPromise = new Promise((resolve, reject) => {
      if (!key) {
        reject(new Error("No Google Maps browser key configured (set GOOGLE_MAPS_BROWSER_KEY in .env)."));
        return;
      }
      const cbName = "__gmapsReady_" + Math.random().toString(36).slice(2);
      window[cbName] = () => resolve(window.google.maps);
      const script = document.createElement("script");
      script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&callback=${cbName}`;
      script.async = true;
      script.onerror = () => reject(new Error("Google Maps failed to load - check your API key/restrictions."));
      document.head.appendChild(script);
    });
    return loadPromise;
  }

  function dotIcon(mapsLib, color, scale) {
    return {
      path: mapsLib.SymbolPath.CIRCLE,
      fillColor: color, fillOpacity: 1,
      strokeColor: "#ffffff", strokeWeight: 2,
      scale: scale || 9,
    };
  }

  // A thin extra ring drawn around a point (in metres) so it stays visible
  // even against a busy map or a large tolerance circle - independent of
  // marker size or zoom level.
  function highlightRing(mapsLib, map, center, color, radius_m) {
    return new mapsLib.Circle({
      center, radius: radius_m, map,
      strokeColor: color, strokeOpacity: 0.9, strokeWeight: 2,
      fillColor: color, fillOpacity: 0.18,
      clickable: false,
    });
  }

  // Two-point mode: customer + FDP, with a line between them. ring_radius_m
  // is the overhead-distance threshold, drawn as a ring around the customer
  // so it's visually clear whether the FDP falls inside it.
  function renderPair(container, mapsLib, { customer, fdp, ring_radius_m }) {
    const bounds = new mapsLib.LatLngBounds();
    const map = new mapsLib.Map(container, { mapTypeControl: false, streetViewControl: false, fullscreenControl: false });
    const custPos = { lat: customer.lat, lng: customer.lon };
    bounds.extend(custPos);

    new mapsLib.Marker({
      position: custPos, map, label: { text: "C", color: "#fff", fontSize: "11px", fontWeight: "700" },
      icon: dotIcon(mapsLib, "#ea4335", 11),
      title: `Customer ${customer.ext_id || customer.id}`,
      zIndex: 10,
    });
    const custRing = highlightRing(mapsLib, map, custPos, "#ea4335", ring_radius_m || 500);
    bounds.union(custRing.getBounds());

    if (fdp && fdp.lat != null) {
      const fdpPos = { lat: fdp.lat, lng: fdp.lon };
      bounds.extend(fdpPos);
      new mapsLib.Marker({
        position: fdpPos, map, label: { text: "F", color: "#fff", fontSize: "11px", fontWeight: "700" },
        icon: dotIcon(mapsLib, "#1e8e3e", 11),
        title: `FDP ${fdp.ext_id || fdp.id}`,
        zIndex: 9,
      });
      new mapsLib.Polyline({
        path: [custPos, fdpPos], map,
        strokeColor: "#1c4f82", strokeWeight: 3, strokeOpacity: 0,
        icons: [{ icon: { path: "M 0,-1 0,1", strokeOpacity: 1, scale: 3 }, offset: "0", repeat: "12px" }],
      });
    }
    map.fitBounds(bounds, 70);
    if (!fdp) map.setZoom(16);
  }

  // Circle mode: the match point (the address's road, or the raw geocoded
  // address as a fallback) at the centre of a tolerance circle, with the
  // customer's given coordinates plotted relative to it, clearly ringed so
  // it's easy to spot against the (larger, softer) tolerance circle.
  function renderCircle(container, mapsLib, { center, center_label, center_title, customer, radius_m }) {
    const bounds = new mapsLib.LatLngBounds();
    const map = new mapsLib.Map(container, { mapTypeControl: false, streetViewControl: false, fullscreenControl: false });
    const addrPos = { lat: center.lat, lng: center.lon };
    const custPos = { lat: customer.lat, lng: customer.lon };
    bounds.extend(addrPos);
    bounds.extend(custPos);

    const toleranceCircle = new mapsLib.Circle({
      center: addrPos, radius: radius_m, map,
      strokeColor: "#1a73e8", strokeOpacity: 0.85, strokeWeight: 2.5,
      fillColor: "#1a73e8", fillOpacity: 0.12,
    });

    new mapsLib.Marker({
      position: addrPos, map, label: { text: center_label || "A", color: "#fff", fontSize: "11px", fontWeight: "700" },
      icon: dotIcon(mapsLib, "#1a73e8", 11),
      title: center_title || "Geocoded address",
      zIndex: 9,
    });

    // Clearly-visible 500m ring right around the customer's own point - this
    // is what makes it "pop" even when it sits inside/near the (variable-size)
    // tolerance circle above.
    const custRing = highlightRing(mapsLib, map, custPos, "#ea4335", 500);
    new mapsLib.Marker({
      position: custPos, map, label: { text: "C", color: "#fff", fontSize: "11px", fontWeight: "700" },
      icon: dotIcon(mapsLib, "#ea4335", 11),
      title: "Customer's uploaded coordinates",
      zIndex: 10,
    });

    bounds.union(toleranceCircle.getBounds());
    bounds.union(custRing.getBounds());
    map.fitBounds(bounds, 70);
  }

  return { load, renderPair, renderCircle };
})();
