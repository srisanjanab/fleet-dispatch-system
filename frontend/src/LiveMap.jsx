import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

/**
 * Draws coloured dots on an OpenStreetMap. `markers` is a list of
 * { id, lat, lng, color, label, radius? }. Dots are updated in place by id, so a moving
 * agent glides instead of the whole map being redrawn on every ping.
 */
export default function LiveMap({ markers, height = 420 }) {
  const el = useRef(null);
  const map = useRef(null);
  const layers = useRef(new Map());
  const fitted = useRef(false);

  useEffect(() => {
    map.current = L.map(el.current).setView([12.9716, 77.5946], 12);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors",
    }).addTo(map.current);
    const store = layers.current;
    return () => {
      map.current.remove();
      map.current = null;
      store.clear();
      fitted.current = false;
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const seen = new Set();

    for (const mk of markers) {
      seen.add(mk.id);
      const style = {
        radius: mk.radius ?? 9,
        color: "#ffffff",
        weight: 2,
        fillColor: mk.color,
        fillOpacity: 1,
      };
      let dot = layers.current.get(mk.id);
      if (!dot) {
        dot = L.circleMarker([mk.lat, mk.lng], style).addTo(m);
        dot.bindTooltip(mk.label);
        layers.current.set(mk.id, dot);
      } else {
        dot.setLatLng([mk.lat, mk.lng]);
        dot.setStyle(style);
        dot.setTooltipContent(mk.label);
      }
    }

    for (const [id, dot] of layers.current) {
      if (!seen.has(id)) {
        dot.remove();
        layers.current.delete(id);
      }
    }

    // Zoom to fit the first batch of markers, then leave the camera alone so people can pan freely
    if (!fitted.current && markers.length > 0) {
      m.fitBounds(markers.map((x) => [x.lat, x.lng]), { padding: [40, 40], maxZoom: 15 });
      fitted.current = true;
    }
  }, [markers]);

  return <div ref={el} className="map" style={{ height }} role="img" aria-label="Live map" />;
}

export const COLORS = {
  available: "#1b7a46",
  busy: "#9a5b00",
  offline: "#8a969c",
  pickup: "#1d5fd1",
  dropoff: "#17252e",
};
