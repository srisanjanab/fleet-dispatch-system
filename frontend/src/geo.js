// Straight-line distance between two points on Earth, in kilometres (haversine formula).
export function distanceKm(a, b) {
  const R = 6371;
  const rad = (deg) => (deg * Math.PI) / 180;
  const dLat = rad(b.lat - a.lat);
  const dLng = rad(b.lng - a.lng);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

// Rough delivery-time estimate. Straight-line distance understates road distance, so we add 30%,
// then assume an average city speed of 25 km/h. A real routing engine would replace this.
const ROAD_FACTOR = 1.3;
const AVG_SPEED_KMH = 25;

export function etaMinutes(straightLineKm) {
  return Math.max(1, Math.round(((straightLineKm * ROAD_FACTOR) / AVG_SPEED_KMH) * 60));
}
